from django import forms
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import EmployeeProfile, User, record_event
EMPLOYEE_ID_PREFIX = "EMP"   # change to e.g. "AVS" if you want a different prefix
EMPLOYEE_ID_WIDTH = 3        # EMP001, EMP002, ... EMP999, EMP1000


def next_employee_id():
    """Next employee ID in sequence: highest existing PREFIX+number, plus one."""
    prefix = EMPLOYEE_ID_PREFIX
    numbers = []
    existing = EmployeeProfile.objects.filter(employee_id__startswith=prefix).values_list("employee_id", flat=True)
    for value in existing:
        suffix = value[len(prefix):]
        if suffix.isdigit():
            numbers.append(int(suffix))
    return f"{prefix}{max(numbers, default=0) + 1:0{EMPLOYEE_ID_WIDTH}d}"



class EmployeeIDAuthenticationForm(AuthenticationForm):
    employee_id = forms.CharField(label="Employee ID", max_length=20)

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Please enter a valid employee ID and password.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username")

    def clean(self):
        employee_id = self.cleaned_data.get("employee_id")
        password = self.cleaned_data.get("password")
        if employee_id and password:
            profile = EmployeeProfile.objects.select_related("user").filter(employee_id=employee_id).first()
            user = (
                authenticate(
                    self.request,
                    username=profile.user.get_username(),
                    password=password,
                    employee_id=employee_id,
                )
                if profile
                else None
            )
            if user is None:
                if profile is None:
                    record_event(
                        action="auth.login_failed",
                        metadata={"employee_id": employee_id},
                    )
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(user)
            self.user_cache = user
        return self.cleaned_data


class EmployeeForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    employee_id = forms.CharField(max_length=20)
    department = forms.ChoiceField(choices=[("", "---------"), *EmployeeProfile.Department.choices], required=False)
    phone = forms.CharField(max_length=15, required=False)
    status = forms.ChoiceField(choices=EmployeeProfile.Status.choices)
    password = forms.CharField(
        min_length=8,
        required=False,
        widget=forms.PasswordInput,
        help_text="Required when creating an employee; leave blank to keep the current password.",
    )

    def __init__(self, *args, profile=None, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)
        if profile:
            self.fields["username"].initial = profile.user.username
            self.fields["first_name"].initial = profile.user.first_name
            self.fields["last_name"].initial = profile.user.last_name
            self.fields["email"].initial = profile.user.email
            self.fields["employee_id"].initial = profile.employee_id
            self.fields["department"].initial = profile.department
            self.fields["phone"].initial = profile.phone
            self.fields["status"].initial = profile.status
        else:
            self.fields["status"].initial = EmployeeProfile.Status.ACTIVE
            self.fields["password"].required = True
            self.fields["employee_id"].required = False
            self.fields["employee_id"].initial = next_employee_id()
            self.fields["employee_id"].help_text = "Generated automatically."

        for field in self.fields.values():
            css_class = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css_class

        if not profile:
            self.fields["employee_id"].widget.attrs.update({"readonly": "readonly", "class": "form-control bg-light"})

    def clean_username(self):
        username = self.cleaned_data["username"]
        users = User.objects.filter(username=username)
        if self.profile:
            users = users.exclude(pk=self.profile.user_id)
        if users.exists():
            raise forms.ValidationError("That username is already in use.")
        return username

    def clean_employee_id(self):
        employee_id = self.cleaned_data["employee_id"]
        if not self.profile:
            return ""  # new employees get their ID from the server when saved
        profiles = EmployeeProfile.objects.filter(employee_id=employee_id)
        # profiles = EmployeeProfile.objects.filter(employee_id=employee_id)
        if self.profile:
            profiles = profiles.exclude(pk=self.profile.pk)
        if profiles.exists():
            raise forms.ValidationError("That employee ID is already in use.")
        return employee_id

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not password and self.profile:
            return password
        candidate = self.profile.user if self.profile else User(
            username=self.cleaned_data.get("username", ""),
            first_name=self.cleaned_data.get("first_name", ""),
            last_name=self.cleaned_data.get("last_name", ""),
            email=self.cleaned_data.get("email", ""),
        )
        try:
            validate_password(password, user=candidate)
        except DjangoValidationError as error:
            raise forms.ValidationError(error.messages) from error
        return password


def _style(form):
    for f in form.fields.values():
        if not isinstance(f.widget, forms.RadioSelect):
            f.widget.attrs.setdefault("class", "input")


class LoginForm(forms.Form):
    employee_id = forms.CharField(
        label="Employee ID / Admin ID",
        max_length=20,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "placeholder": "e.g. EMP001"}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)


class ForgotPasswordForm(forms.Form):
    employee_id = forms.CharField(
        max_length=20,
        required=True,
        label="Employee ID",
        widget=forms.TextInput(attrs={
            "placeholder": "Enter Employee ID",
            "autocomplete": "off",
        }),
    )

    email = forms.EmailField(
        required=True,
        label="Gmail Address",
        widget=forms.EmailInput(attrs={
            "placeholder": "Enter registered Gmail address",
            "autocomplete": "email",
        }),
    )

    def clean(self):
        cleaned_data = super().clean()

        employee_id = cleaned_data.get("employee_id")
        email = cleaned_data.get("email")

        if employee_id:
            cleaned_data["employee_id"] = employee_id.strip()

        if email:
            cleaned_data["email"] = email.strip().lower()

        return cleaned_data


class OTPForm(forms.Form):
    otp = forms.RegexField(
        regex=r"^\d{6}$",
        label="Enter the 6-digit OTP",
        error_messages={"invalid": "The OTP is 6 digits."},
        widget=forms.TextInput(attrs={"inputmode": "numeric", "maxlength": 6, "autocomplete": "one-time-code",
                                      "autofocus": True, "class": "input otp-input", "placeholder": "••••••"}),
    )


class ResetPasswordForm(SetPasswordForm):
    """New password + confirm (used after OTP is verified)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)


class ChangePasswordForm(PasswordChangeForm):
    """Old password + new password + confirm (used when logged in)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)

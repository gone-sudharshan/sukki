import re
import secrets
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import EmployeeProfile

User = get_user_model()
SESSION_KEY = "pw_reset"     # everything about the OTP lives here, server side, in request.session


# ---------- find the employee ----------
def find_user_by_email(email):
    users = User.objects.filter(email__iexact=email.strip(), is_active=True, employee__status=Employee.Status.ACTIVE)
    return users.first() if users.count() == 1 else None


def find_user_by_phone(phone):
    digits = re.sub(r"\D", "", phone)[-10:]  # compare last 10 digits (ignores +91, spaces, dashes)
    if len(digits) < 10:
        return None
    users = User.objects.filter(employee__phone__endswith=digits, is_active=True, employee__status=Employee.Status.ACTIVE)
    return users.first() if users.count() == 1 else None


# ---------- masking (shown on the OTP page) ----------
def mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'*' * max(len(name) - 2, 1)}@{domain}"


def mask_phone(phone):
    digits = re.sub(r"\D", "", phone)
    return f"{'*' * max(len(digits) - 3, 0)}{digits[-3:]}"


# ---------- OTP state in the session ----------
def _hash(user_id, code):
    return salted_hmac("password-reset-otp", f"{user_id}:{code}").hexdigest()


def generate_code():
    return "".join(str(secrets.randbelow(10)) for _ in range(settings.OTP_LENGTH))


def start_otp(request, user, channel, target_masked):
    """Make a new OTP and remember it in the session. Returns the plain code (to send).
    user=None keeps the page looking identical when the email/number is not registered."""
    code = generate_code()
    request.session[SESSION_KEY] = {
        "user_id": user.pk if user else None,
        "channel": channel,
        "target": target_masked,
        "hash": _hash(user.pk, code) if user else "",
        "expires": time.time() + settings.OTP_EXPIRY_MINUTES * 60,
        "attempts": 0,
        "sent_at": time.time(),
        "verified": False,
    }
    return code


def get_state(request):
    return request.session.get(SESSION_KEY)


def clear(request):
    request.session.pop(SESSION_KEY, None)


def check_otp(request, code):
    """Returns (ok, error_message)."""
    state = get_state(request)
    if not state or not state.get("user_id"):
        return False, "Wrong OTP."
    if time.time() >= state["expires"]:
        return False, "This OTP has expired. Request a new one."
    if state["attempts"] >= settings.OTP_MAX_ATTEMPTS:
        return False, "Too many wrong attempts. Request a new OTP."

    state["attempts"] += 1
    if not constant_time_compare(state["hash"], _hash(state["user_id"], code)):
        request.session[SESSION_KEY] = state
        left = settings.OTP_MAX_ATTEMPTS - state["attempts"]
        return False, f"Wrong OTP. {left} attempt(s) left."

    state["verified"] = True
    state["hash"] = ""            # a code can be used only once
    request.session[SESSION_KEY] = state
    return True, ""


def verified_user_id(request):
    state = get_state(request)
    return state["user_id"] if state and state.get("verified") else None


def seconds_until_resend(request):
    state = get_state(request)
    if not state:
        return 0
    remaining = state["sent_at"] + settings.OTP_RESEND_SECONDS - time.time()
    return int(remaining) + 1 if remaining > 0 else 0


# ---------- delivery ----------
def send_otp_email(user, code):
    send_mail(
        subject="Your password reset code",
        message=(
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"Your password reset code is: {code}\n"
            f"It is valid for {settings.OTP_EXPIRY_MINUTES} minutes.\n\n"
            "If you did not request this, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def _ten_digits(phone):
    return re.sub(r"\D", "", phone)[-10:]


def send_otp_sms(phone, code):
    text = f"Your password reset code is {code}. Valid for {settings.OTP_EXPIRY_MINUTES} minutes."
    backend = settings.SMS_BACKEND

    if backend == "fast2sms":  # Indian numbers, https://www.fast2sms.com (OTP route)
        import json
        import urllib.parse
        import urllib.request

        query = urllib.parse.urlencode({"route": "otp", "variables_values": code, "numbers": _ten_digits(phone), "flash": 0})
        req = urllib.request.Request(
            f"https://www.fast2sms.com/dev/bulkV2?{query}",
            headers={"authorization": settings.FAST2SMS_API_KEY, "cache-control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        if not result.get("return"):
            raise RuntimeError(f"Fast2SMS error: {result.get('message')}")

    elif backend == "twilio":  # pip install twilio
        from twilio.rest import Client

        number = phone if phone.startswith("+") else settings.DEFAULT_COUNTRY_CODE + _ten_digits(phone)
        Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create(
            body=text, from_=settings.TWILIO_FROM_NUMBER, to=number
        )

    else:  # "console" - development only
        print(f"\n[SMS to {phone}] {text}\n")
from django.contrib.auth import get_user_model
from .models import EmployeeProfile

User = get_user_model()


def find_user_by_employee_id_and_email(employee_id, email):
    """
    Find an active employee only when BOTH:
      Employee.employee_id matches
      User.email matches
    """

    employee_id = (employee_id or "").strip()
    email = (email or "").strip().lower()

    employee = (
        EmployeeProfile.objects
        .select_related("user")
        .filter(
            employee_id__iexact=employee_id,
            user__email__iexact=email,
            status=EmployeeProfile.Status.ACTIVE,
            user__is_active=True,
        )
        .first()
    )

    return employee.user if employee else None
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import EmployeeIDAuthenticationForm
from . import views

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="portal/login.html",
            authentication_form=EmployeeIDAuthenticationForm,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("profile/", views.profile, name="profile"),
    path("attendance/check-in/", views.check_in, name="check_in"),
    path("attendance/check-out/", views.check_out, name="check_out"),
    # path("employees/", views.employee_list, name="employee_list"),
    # path("employees/new/", views.employee_form, name="employee_create"),
    # path("employees/<int:profile_id>/edit/", views.employee_form, name="employee_edit"),

    path("employees/", views.EmployeeListView.as_view(), name="employee_list"),
    path("employees/data/", views.EmployeeDataView.as_view(), name="employee_data"),
    path("employees/<int:profile_id>/quick-edit/", views.EmployeeQuickEditView.as_view(), name="employee_quick_edit"),
    path("employees/new/", views.EmployeeFormView.as_view(), name="employee_create"),
    path("employees/<int:profile_id>/edit/", views.EmployeeFormView.as_view(), name="employee_edit"),

    path("my-attendance/", views.my_attendance, name="my_attendance"),
    path("attendance/", views.attendance_list, name="attendance_list"),
    path("audit/", views.audit_event_list, name="audit_event_list"),
    path("forgot-password/",views.forgot_password, name="forgot_password"),
    path("forgot-password/verify/",views.verify_otp,name="verify_otp"),
    path("forgot-password/resend/",views.resend_otp,name="resend_otp"),
    path("forgot-password/reset/",views.reset_password,name="reset_password"),
    path("change-password/",views.change_password,name="change_password"),
]

# Serve uploaded profile photos in development (no effect when DEBUG is False)
if settings.MEDIA_URL:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
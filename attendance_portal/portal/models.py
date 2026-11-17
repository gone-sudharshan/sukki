from contextvars import ContextVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.dispatch import receiver
from django.utils import timezone


audit_context = ContextVar("audit_context", default=None)


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        EMPLOYEE = "employee", "Employee"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.EMPLOYEE)

    objects = UserManager()

    class Meta:
        db_table = "accounts_user"


class EmployeeProfile(models.Model):
    class Department(models.TextChoices):
        HR = "HR", "Human Resources"
        IT = "IT", "Information Technology"
        FINANCE = "FIN", "Finance"
        SALES = "SALES", "Sales"
        MARKETING = "MKT", "Marketing"
        OPERATIONS = "OPS", "Operations"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employee_profile")
    employee_id = models.CharField(max_length=20, unique=True)
    department = models.CharField(max_length=16, choices=Department.choices, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profiles_created",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profiles_updated",
    )

    class Meta:
        db_table = "accounts_employeeprofile"
        ordering = ["employee_id"]

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.username}"


class Attendance(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    work_date = models.DateField(default=timezone.localdate)
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_in_note = models.CharField(max_length=255, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    check_out_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_attendance"
        ordering = ["-work_date", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["user", "work_date"], name="unique_attendance_per_user_day"),
            models.CheckConstraint(
                condition=Q(check_out_at__isnull=True)
                | (Q(check_in_at__isnull=False) & Q(check_out_at__gte=F("check_in_at"))),
                name="checkout_after_checkin",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.work_date}"

    @property
    def work_duration(self):
        if self.check_in_at and self.check_out_at:
            return self.check_out_at - self.check_in_at
        return None


class AuditEvent(models.Model):
    action = models.CharField(max_length=80, db_index=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor_identifier = models.CharField(max_length=64, blank=True)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_audit_events",
    )
    target_identifier = models.CharField(max_length=64, blank=True)
    entity_type = models.CharField(max_length=120, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)
    request_id = models.CharField(max_length=36, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "auditlog_auditevent"
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.occurred_at} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events cannot be deleted.")


def record_event(
    *,
    action,
    actor=None,
    target_user=None,
    entity_type="",
    entity_id="",
    metadata=None,
):
    context = audit_context.get() or {}
    return AuditEvent.objects.create(
        action=action,
        actor=actor,
        actor_identifier=str(actor.pk) if actor else "",
        target_user=target_user,
        target_identifier=str(target_user.pk) if target_user else "",
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else "",
        request_id=context.get("request_id", ""),
        ip_address=context.get("ip_address"),
        user_agent=context.get("user_agent", ""),
        metadata=metadata or {},
    )


@receiver(user_logged_in, dispatch_uid="portal.user_logged_in")
def log_user_login(sender, request, user, **kwargs):
    record_event(action="auth.login", actor=user, target_user=user, entity_type="portal.User", entity_id=user.pk)


@receiver(user_logged_out, dispatch_uid="portal.user_logged_out")
def log_user_logout(sender, request, user, **kwargs):
    record_event(
        action="auth.logout",
        actor=user,
        target_user=user,
        entity_type="portal.User",
        entity_id=user.pk if user else "",
    )


@receiver(user_login_failed, dispatch_uid="portal.user_login_failed")
def log_failed_login(sender, credentials, request, **kwargs):
    employee_id = credentials.get("employee_id")
    record_event(
        action="auth.login_failed",
        metadata={"employee_id": employee_id} if employee_id else {},
    )

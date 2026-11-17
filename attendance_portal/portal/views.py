import calendar as pycal
from collections import Counter
from datetime import date , datetime, timedelta
from functools import wraps
from uuid import uuid4

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import utils as utils
from .forms import ChangePasswordForm, ForgotPasswordForm, LoginForm, OTPForm, ResetPasswordForm
from django.http import JsonResponse

from .forms import EmployeeForm
from .models import Attendance, AuditEvent, EmployeeProfile, User, audit_context, record_event
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.views import View
from django.urls import reverse


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = str(uuid4())
        token = audit_context.set(
            {
                "request_id": request_id,
                "ip_address": request.META.get("REMOTE_ADDR"),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:512],
            }
        )
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            return response
        finally:
            audit_context.reset(token)


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.ADMIN:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def require_active_employee(user):
    if user.role not in (User.Role.EMPLOYEE, User.Role.ADMIN) or not user.is_active:
        raise PermissionDenied
    try:
        if user.employee_profile.status != EmployeeProfile.Status.ACTIVE:
            raise PermissionDenied
    except EmployeeProfile.DoesNotExist:
        pass


def _greeting(now=None):
    hour = (now or timezone.localtime()).hour
    if hour < 12:
        return "Good Morning"
    if hour < 17:
        return "Good Afternoon"
    return "Good Evening"


def _office_start(day):
    start = datetime.strptime(getattr(settings, "OFFICE_START_TIME", "09:30"), "%H:%M").time()
    return timezone.make_aware(datetime.combine(day, start))


def _late_cutoff(day):
    """Moment after which a check-in on `day` counts as late (office start + grace)."""
    return _office_start(day) + timedelta(minutes=getattr(settings, "OFFICE_LATE_GRACE_MINUTES", 15))


def _recent_weekdays(today, count=7):
    days, day = [], today
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))

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



def _admin_dashboard_context(today):
    employees = EmployeeProfile.objects.filter(
        status=EmployeeProfile.Status.ACTIVE, user__role=User.Role.EMPLOYEE
    )
    total = employees.count()

    records = list(
        Attendance.objects.filter(
            work_date=today,
            check_in_at__isnull=False,
            user__role=User.Role.EMPLOYEE,
            user__employee_profile__status=EmployeeProfile.Status.ACTIVE,
        )
        .select_related("user", "user__employee_profile")
        .order_by("-check_in_at")
    )
    cutoff = _late_cutoff(today)
    for record in records:
        record.is_late = record.check_in_at > cutoff

    present = len(records)
    late = sum(1 for r in records if r.is_late)
    working_now = sum(1 for r in records if r.check_out_at is None)
    absent = max(total - present, 0)
    rate = round(present / total * 100, 1) if total else 0

    # Employees who have not checked in yet.
    present_ids = [r.user_id for r in records]
    not_in = list(employees.exclude(user_id__in=present_ids).select_related("user")[:8])

    # Department breakdown for today.
    names = dict(EmployeeProfile.Department.choices)
    totals = {
        row["department"]: row["n"]
        for row in employees.order_by().values("department").annotate(n=Count("id"))
    }
    present_by_dept = Counter(r.user.employee_profile.department for r in records)
    departments = sorted(
        (
            {
                "name": names.get(code) or "Unassigned",
                "total": count,
                "present": present_by_dept.get(code, 0),
                "pct": round(present_by_dept.get(code, 0) / count * 100),
            }
            for code, count in totals.items()
        ),
        key=lambda d: (-d["total"], d["name"]),
    )

    # Last 7 working days trend (uses today's headcount as the baseline).
    days = _recent_weekdays(today)
    per_day = {
        row["work_date"]: row["n"]
        for row in Attendance.objects.filter(
            work_date__in=days,
            check_in_at__isnull=False,
            user__role=User.Role.EMPLOYEE,
            user__employee_profile__status=EmployeeProfile.Status.ACTIVE,
        )
        .order_by()
        .values("work_date")
        .annotate(n=Count("id"))
    }
    trend = {
        "labels": [d.strftime("%a %d") for d in days],
        "present": [per_day.get(d, 0) for d in days],
        "absent": [max(total - per_day.get(d, 0), 0) for d in days],
    }

    # Live activity feed (latest check-ins and check-outs today).
    activity = []
    for r in records:
        activity.append({"user": r.user, "kind": "in", "at": r.check_in_at, "late": r.is_late})
        if r.check_out_at:
            activity.append({"user": r.user, "kind": "out", "at": r.check_out_at, "late": False})
    activity.sort(key=lambda e: e["at"], reverse=True)

    return {
        "total": total,
        "present": present,
        "absent": absent,
        "rate": rate,
        "on_time": present - late,
        "late": late,
        "working_now": working_now,
        "checked_out": present - working_now,
        "not_in": not_in,
        "not_in_more": max(absent - len(not_in), 0),
        "departments": departments,
        "trend": trend,
        "activity": activity[:8],
        "records": records,
        "office_start": getattr(settings, "OFFICE_START_TIME", "09:30"),
    }


def _work_hours_target():
    return getattr(settings, "OFFICE_WORK_HOURS", 8)


def _fmt_duration(delta):
    """timedelta -> '8h 05m' (or '-' when there is nothing to show)."""
    if delta is None:
        return "-"
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _punch_state(attendance):
    if attendance is None or attendance.check_in_at is None:
        return "ready"
    if attendance.check_out_at is None:
        return "working"
    return "done"


def _attendance_rows(user, start, end, today):
    """One row per relevant day between start and end (newest first).

    Included: days with a check-in, past weekdays without one (absent) and today if the
    person has not checked in yet (pending). Weekends without a record are skipped.
    """
    records = {
        r.work_date: r
        for r in Attendance.objects.filter(user=user, work_date__range=(start, end))
    }
    joined = timezone.localtime(user.date_joined).date()  # never mark someone absent before they joined
    rows = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        rec = records.get(day)
        has_in = bool(rec and rec.check_in_at)
        if not has_in and (day.weekday() >= 5 or day > today or day < joined):
            continue

        row = {
            "date": day,
            "record": rec if has_in else None,
            "check_in": rec.check_in_at if has_in else None,
            "check_out": rec.check_out_at if has_in else None,
            "duration": None,
            "hours": "-",
            "out_state": "",
            "late": False,
            "note": "",
        }
        if has_in:
            row["late"] = rec.check_in_at > _late_cutoff(day)
            row["code"] = "late" if row["late"] else "ontime"
            row["label"] = "Late" if row["late"] else "On time"
            if rec.check_out_at:
                row["duration"] = rec.check_out_at - rec.check_in_at
                row["hours"] = _fmt_duration(row["duration"])
                row["out_state"] = "done"
            elif day == today:
                row["hours"] = "In progress"
                row["out_state"] = "working"
            else:
                row["hours"] = "-"
                row["out_state"] = "missing"
            row["note"] = " | ".join(n for n in (rec.check_in_note, rec.check_out_note) if n)
        elif day == today:
            row["code"], row["label"] = "pending", "Not checked in"
        else:
            row["code"], row["label"] = "absent", "Absent"
        rows.append(row)
    rows.reverse()
    return rows


def _parse_month(value, today):
    """'2026-11' -> date(2026, 11, 1); invalid or future values fall back to the current month."""
    current = today.replace(day=1)
    try:
        year, month = (int(part) for part in (value or "").split("-"))
        first = date(year, month, 1)
    except (ValueError, TypeError):
        return current
    return min(first, current) if first.year >= 2000 else current


def _shift_month(first, step):
    index = first.year * 12 + (first.month - 1) + step
    return date(index // 12, index % 12 + 1, 1)


def _month_report(user, first, today):
    last = first.replace(day=pycal.monthrange(first.year, first.month)[1])
    rows = _attendance_rows(user, first, min(last, today), today)
    by_date = {r["date"]: r for r in rows}

    present = sum(1 for r in rows if r["check_in"])
    late = sum(1 for r in rows if r["late"])
    absent = sum(1 for r in rows if r["code"] == "absent")
    durations = [r["duration"] for r in rows if r["duration"]]
    total_time = sum(durations, timedelta())
    avg_time = total_time / len(durations) if durations else None
    counted = present + absent

    weeks = []
    for week in pycal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month):
        cells = []
        for day in week:
            row = by_date.get(day)
            cell = {
                "day": day,
                "in_month": day.month == first.month,
                "today": day == today,
                "weekend": day.weekday() >= 5,
                "future": day > today,
                "code": row["code"] if row else "",
                "time": timezone.localtime(row["check_in"]).strftime("%H:%M") if row and row["check_in"] else "",
                "tip": "",
            }
            if row:
                cell["tip"] = f'{row["label"]}' + (f' - {row["hours"]}' if row["duration"] else "")
            cells.append(cell)
        weeks.append(cells)

    return {
        "rows": rows,
        "weeks": weeks,
        "present": present,
        "late": late,
        "on_time": present - late,
        "absent": absent,
        "rate": round(present / counted * 100, 1) if counted else 0,
        "total_hours": _fmt_duration(total_time) if durations else "0h 00m",
        "avg_hours": _fmt_duration(avg_time) if avg_time else "-",
    }


def _week_start(today):
    """Monday of the week containing `today` (the working week runs Monday to Friday)."""
    return today - timedelta(days=today.weekday())


def _week_chart(user, today):
    """Hours worked on each day of the current Monday-Friday week (future days stay at 0)."""
    start = _week_start(today)
    days = [start + timedelta(days=i) for i in range(5)]
    records = {r.work_date: r for r in Attendance.objects.filter(user=user, work_date__in=days)}
    now, hours = timezone.now(), []
    for day in days:
        rec = records.get(day)
        value = 0
        if rec and rec.check_in_at:
            end = rec.check_out_at or (now if day == today else None)
            if end:
                value = round(max((end - rec.check_in_at).total_seconds(), 0) / 3600, 2)
        hours.append(value)
    return {
        "labels": [d.strftime("%a %d") for d in days],
        "hours": hours,
        "target": _work_hours_target(),
        "today_index": days.index(today) if today in days else -1,  # -1 on Sat/Sun
    }


def _employee_dashboard_context(user, today):
    attendance = Attendance.objects.filter(user=user, work_date=today).first()
    state = _punch_state(attendance)
    target = _work_hours_target()
    office_start = getattr(settings, "OFFICE_START_TIME", "09:30")

    today_row = _attendance_rows(user, today, today, today)[0] if state != "ready" else None
    worked, worked_pct, remaining_text, arrival_note = timedelta(0), 0, "", ""
    if state != "ready":
        end = attendance.check_out_at or timezone.now()
        worked = max(end - attendance.check_in_at, timedelta(0))
        worked_pct = min(round(worked.total_seconds() / 3600 / target * 100), 100) if target else 0
        if state == "working":
            left = timedelta(hours=target) - worked
            remaining_text = f"{_fmt_duration(left)} left of {target}h" if left.total_seconds() > 0 else "Daily target reached"
        minutes = round((attendance.check_in_at - _office_start(today)).total_seconds() / 60)
        if minutes < 0:
            arrival_note = f"{-minutes} min before the {office_start} start"
        elif minutes == 0:
            arrival_note = f"Right on the {office_start} start"
        else:
            arrival_note = f"{minutes} min after the {office_start} start"

    week_start = _week_start(today)
    week_end = week_start + timedelta(days=4)
    return {
        "is_admin": False,
        "attendance": attendance,
        "punch_state": state,
        "today_row": today_row,
        "worked": _fmt_duration(worked) if state != "ready" else "0h 00m",
        "worked_pct": worked_pct,
        "work_hours": target,
        "remaining_text": remaining_text,
        "arrival_note": arrival_note,
        "week_chart": _week_chart(user, today),
        "week_label": f"{week_start:%b} {week_start.day} - {week_end:%b} {week_end.day}",
        "recent": _attendance_rows(user, week_start, today, today),
        "office_start": office_start,
        "late_after": timezone.localtime(_late_cutoff(today)).strftime("%H:%M"),
    }


@login_required
def dashboard(request):
    today = timezone.localdate()
    context = {"today": today, "greeting": _greeting()}

    if request.user.role == User.Role.ADMIN:
        # Company-wide overview. Admins are employees too, so their own status shows in the greeting.
        attendance = Attendance.objects.filter(user=request.user, work_date=today).first()
        context.update(_admin_dashboard_context(today), is_admin=True, attendance=attendance,
                       punch_state=_punch_state(attendance))
        return render(request, "portal/dashboard.html", context)

    require_active_employee(request.user)
    context.update(_employee_dashboard_context(request.user, today))
    return render(request, "portal/dashboard.html", context)


def _redirect_back(request):
    """Send the user back to the page they clicked check-in/out from (top nav works on any page)."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect("dashboard")


@login_required
@require_POST
@transaction.atomic
def check_in(request):
    require_active_employee(request.user)
    note = request.POST.get("note", "").strip()
    if len(note) > 255:
        messages.error(request, "Notes must be 255 characters or fewer.")
        return _redirect_back(request)

    user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
    work_date = timezone.localdate()
    attendance, _ = Attendance.objects.get_or_create(user=user, work_date=work_date)
    if attendance.check_in_at:
        messages.error(request, "You have already checked in today.")
        return _redirect_back(request)

    attendance.check_in_at = timezone.now()
    attendance.check_in_note = note
    attendance.save(update_fields=["check_in_at", "check_in_note", "updated_at"])
    record_event(
        action="attendance.checked_in",
        actor=user,
        target_user=user,
        entity_type="portal.Attendance",
        entity_id=attendance.pk,
        metadata={"work_date": work_date.isoformat()},
    )
    messages.success(request, "Check-in recorded.")
    return _redirect_back(request)

@login_required
def my_attendance(request):
    """The signed-in person's own attendance: month summary, calendar and day-by-day table."""
    require_active_employee(request.user)
    today = timezone.localdate()
    first = _parse_month(request.GET.get("month"), today)
    is_current = first == today.replace(day=1)
    context = {
        "today": today,
        "month_first": first,
        "month_label": first.strftime("%B %Y"),
        "prev_month": _shift_month(first, -1).strftime("%Y-%m"),
        "next_month": None if is_current else _shift_month(first, 1).strftime("%Y-%m"),
        "is_current": is_current,
        "report": _month_report(request.user, first, today),
        "office_start": getattr(settings, "OFFICE_START_TIME", "09:30"),
        "late_after": timezone.localtime(_late_cutoff(today)).strftime("%H:%M"),
    }
    return render(request, "portal/my_attendance.html", context)


@login_required
@require_POST
@transaction.atomic
def check_out(request):
    require_active_employee(request.user)
    note = request.POST.get("note", "").strip()
    if len(note) > 255:
        messages.error(request, "Notes must be 255 characters or fewer.")
        return _redirect_back(request)

    work_date = timezone.localdate()
    attendance = Attendance.objects.select_for_update().filter(user=request.user, work_date=work_date).first()
    if attendance is None or attendance.check_in_at is None:
        messages.error(request, "Check in before checking out.")
        return _redirect_back(request)
    if attendance.check_out_at:
        messages.error(request, "You have already checked out today.")
        return _redirect_back(request)

    attendance.check_out_at = timezone.now()
    attendance.check_out_note = note
    attendance.save(update_fields=["check_out_at", "check_out_note", "updated_at"])
    record_event(
        action="attendance.checked_out",
        actor=request.user,
        target_user=request.user,
        entity_type="portal.Attendance",
        entity_id=attendance.pk,
        metadata={"work_date": work_date.isoformat()},
    )
    messages.success(request, "Check-out recorded.")
    return _redirect_back(request)



@admin_required
def employee_list(request):
    employees = EmployeeProfile.objects.select_related("user").all()
    return render(request, "portal/employees.html", {"employees": employees})


@admin_required
def employee_form(request, profile_id=None):
    profile = None
    if profile_id is not None:
        profile = get_object_or_404(
            EmployeeProfile.objects.select_related("user"),
            pk=profile_id,
            user__role=User.Role.EMPLOYEE,
        )

    form = EmployeeForm(request.POST or None, profile=profile)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                if profile is None:
                    employee = User.objects.create_user(
                        username=data["username"],
                        password=data["password"],
                        first_name=data["first_name"],
                        last_name=data["last_name"],
                        email=data["email"],
                        role=User.Role.EMPLOYEE,
                    )
                    profile = EmployeeProfile.objects.create(
                        user=employee,
                        employee_id=data["employee_id"],
                        department=data["department"],
                        phone=data["phone"],
                        status=data["status"],
                        created_by=request.user,
                        updated_by=request.user,
                    )
                    record_event(
                        action="user.created",
                        actor=request.user,
                        target_user=employee,
                        entity_type="portal.User",
                        entity_id=employee.pk,
                        metadata={"role": employee.role},
                    )
                    record_event(
                        action="employee.profile.created",
                        actor=request.user,
                        target_user=employee,
                        entity_type="portal.EmployeeProfile",
                        entity_id=profile.pk,
                        metadata={"employee_id": profile.employee_id},
                    )
                else:
                    employee = profile.user
                    user_fields = ("username", "first_name", "last_name", "email")
                    user_changes = [field for field in user_fields if getattr(employee, field) != data[field]]
                    profile_fields = ("employee_id", "department", "phone", "status")
                    profile_changes = [field for field in profile_fields if getattr(profile, field) != data[field]]
                    should_be_active = data["status"] == EmployeeProfile.Status.ACTIVE
                    if employee.is_active != should_be_active:
                        user_changes.append("is_active")
                    password_changed = bool(data["password"])
                    if user_changes or password_changed:
                        for field in user_fields:
                            setattr(employee, field, data[field])
                        employee.is_active = should_be_active
                        if password_changed:
                            employee.set_password(data["password"])
                        employee.save()
                        record_event(
                            action="user.updated",
                            actor=request.user,
                            target_user=employee,
                            entity_type="portal.User",
                            entity_id=employee.pk,
                            metadata={
                                "changed_fields": user_changes,
                                "password_changed": password_changed,
                            },
                        )
                        if password_changed:
                            record_event(
                                action="auth.password_changed",
                                actor=request.user,
                                target_user=employee,
                                entity_type="portal.User",
                                entity_id=employee.pk,
                            )
                    if profile_changes:
                        for field in profile_fields:
                            setattr(profile, field, data[field])
                        profile.updated_by = request.user
                        profile.save()
                        record_event(
                            action="employee.profile.updated",
                            actor=request.user,
                            target_user=employee,
                            entity_type="portal.EmployeeProfile",
                            entity_id=profile.pk,
                            metadata={"changed_fields": profile_changes},
                        )
            messages.success(request, "Employee saved.")
            return redirect("employee_list")
        except IntegrityError:
            form.add_error(None, "The employee could not be saved because a username or employee ID is already in use.")

    return render(
        request,
        "portal/employee_form.html",
        {"form": form, "profile": profile, "is_edit": profile is not None},
    )


@admin_required
def attendance_list(request):
    records = Attendance.objects.select_related("user").all()
    return render(request, "portal/attendance.html", {"records": records})


@admin_required
def audit_event_list(request):
    events = AuditEvent.objects.select_related(
        "actor",
        "actor__employee_profile",
        "target_user",
        "target_user__employee_profile",
    ).all()
    action_labels = {
        "auth.login": ("Successful sign-in", "success"),
        "auth.logout": ("Sign-out", "secondary"),
        "auth.login_failed": ("Failed sign-in", "danger"),
        "auth.password_changed": ("Password changed", "warning"),
        "attendance.checked_in": ("Check-in", "success"),
        "attendance.checked_out": ("Check-out", "primary"),
        "user.created": ("Account created", "info"),
        "user.updated": ("Account updated", "info"),
        "employee.profile.created": ("Employee created", "info"),
        "employee.profile.updated": ("Employee details updated", "info"),
    }

    field_labels = {
        "username": "username",
        "first_name": "first name",
        "last_name": "last name",
        "email": "email",
        "employee_id": "employee ID",
        "department": "department",
        "phone": "phone",
        "status": "status",
        "is_active": "active status",
        "photo": "profile photo",
    }

    for event in events:
        event.action_label, event.badge_class = action_labels.get(
            event.action,
            (event.action.replace(".", " ").replace("_", " ").capitalize(), "secondary"),
        )
        metadata = event.metadata or {}
        if event.action == "auth.login":
            event.summary = "Signed in successfully."
        elif event.action == "auth.logout":
            event.summary = "Signed out."
        elif event.action == "auth.login_failed":
            employee_id = metadata.get("employee_id")
            event.summary = (
                f"Sign-in failed for employee ID {employee_id}; the ID may be unknown or the password incorrect."
                if employee_id
                else "Sign-in failed; no employee ID was recorded."
            )
        elif event.action == "auth.password_changed":
            event.summary = "Password was changed. The password itself is never recorded."
        elif event.action in {"attendance.checked_in", "attendance.checked_out"}:
            work_date = metadata.get("work_date")
            event.summary = f"Attendance recorded for {work_date}." if work_date else "Attendance time recorded."
        elif event.action == "user.created":
            role = metadata.get("role")
            event.summary = f"Created account{f' with role {role}' if role else ''}."
        elif event.action == "user.updated":
            changed_fields = [
                field_labels.get(field, field.replace("_", " "))
                for field in metadata.get("changed_fields", [])
            ]
            if metadata.get("password_changed"):
                changed_fields.append("password")
            event.summary = (
                f"Updated {', '.join(changed_fields)}."
                if changed_fields
                else "Account updated; no field-level details were recorded."
            )
        elif event.action == "employee.profile.created":
            employee_id = metadata.get("employee_id")
            event.summary = f"Created employee profile{f' for ID {employee_id}' if employee_id else ''}."
        elif event.action == "employee.profile.updated":
            changed_fields = [
                field_labels.get(field, field.replace("_", " "))
                for field in metadata.get("changed_fields", [])
            ]
            event.summary = (
                f"Updated {', '.join(changed_fields)}."
                if changed_fields
                else "Employee profile updated; no field-level details were recorded."
            )
        elif metadata:
            event.summary = "; ".join(
                f"{key.replace('_', ' ').capitalize()}: {value}"
                for key, value in metadata.items()
                if value not in (None, "", [], {})
            ) or "No additional details recorded."
        else:
            event.summary = "No additional details recorded."

        actor_profile = getattr(event.actor, "employee_profile", None) if event.actor else None
        target_profile = getattr(event.target_user, "employee_profile", None) if event.target_user else None
        event.actor_label = (
            event.actor.get_full_name() or event.actor.username
            if event.actor
            else "Unknown login attempt" if event.action == "auth.login_failed" else "System"
        )
        if actor_profile and actor_profile.employee_id:
            event.actor_label = f"{event.actor_label} ({actor_profile.employee_id})"

        event.target_label = (
            event.target_user.get_full_name() or event.target_user.username
            if event.target_user
            else f"Employee ID {metadata['employee_id']}" if event.action == "auth.login_failed" and metadata.get("employee_id")
            else "—"
        )
        if target_profile and target_profile.employee_id:
            event.target_label = f"{event.target_label} ({target_profile.employee_id})"
    return render(request, "portal/audit_events.html", {"events": events})



User = get_user_model()
logger = logging.getLogger(__name__)



@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


# ------------------------------------------------------- forgot password
def _deliver(request, user, channel, code, phone=None):
    """Send the OTP. Returns False (and shows an error) if sending fails."""
    if channel == "email" and "console" in settings.EMAIL_BACKEND:
        logger.warning("EMAIL NOT CONFIGURED: OTP is only printed in this terminal. Set EMAIL_HOST_USER / EMAIL_HOST_PASSWORD in .env")
    if channel == "phone" and settings.SMS_BACKEND == "console":
        logger.warning("SMS NOT CONFIGURED: OTP is only printed in this terminal. Set SMS_BACKEND in .env")
    try:
        if channel == "email":
            utils.send_otp_email(user, code)
        else:
            utils.send_otp_sms(phone or user.employee.phone, code)
        return True
    except Exception:
        logger.exception("Could not send OTP via %s", channel)
        messages.error(request, "We could not send the OTP right now. Please try again later or contact your admin.")
        return False


def forgot_password(request):
    """
    Step 1:
    Employee enters Employee ID + registered Gmail.
    Both must match the same active employee.
    """

    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST" and form.is_valid():

        employee_id = form.cleaned_data["employee_id"]
        email = form.cleaned_data["email"]

        # BOTH Employee ID and Email must match
        user = utils.find_user_by_employee_id_and_email(
            employee_id,
            email,
        )

        if not user:
            form.add_error(
                None,
                "Employee ID and Gmail address do not match our records."
            )
            return render(
                request,
                "portal/forgot_password.html",
                {"form": form},
            )

        # Generate and save OTP in session
        code = utils.start_otp(
            request,
            user,
            "email",
            utils.mask_email(email),
        )

        # Send OTP
        if not _deliver(
            request,
            user,
            "email",
            code,
        ):
            utils.clear(request)

            return render(
                request,
                "portal/forgot_password.html",
                {"form": form},
            )

        messages.success(
            request,
            "OTP has been sent to your registered Gmail address."
        )

        return redirect("verify_otp")

    return render(
        request,
        "portal/forgot_password.html",
        {"form": form},
    )
    """Step 1: choose Email or Mobile, enter it, send OTP."""
    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        method = form.cleaned_data["method"]
        if method == "email":
            target = form.cleaned_data["email"]
            user = utils.find_user_by_email(target)
            masked = utils.mask_email(target)
        else:
            target = form.cleaned_data["phone"]
            user = utils.find_user_by_phone(target)
            masked = utils.mask_phone(target)

        code = utils.start_otp(request, user, method, masked)
        if user and not _deliver(request, user, method, code, phone=target):
            utils.clear(request)
            return render(request, "portal/forgot_password.html", {"form": form})

        # Same message whether or not the account exists (does not leak who is registered).
        messages.info(request, f"If this {'email' if method == 'email' else 'number'} is registered, an OTP has been sent.")
        return redirect("verify_otp")

    return render(request, "portal/forgot_password.html", {"form": form})


def verify_otp(request):
    """Step 2: check the OTP."""
    state = utils.get_state(request)
    if not state:
        return redirect("forgot_password")

    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ok, error = utils.check_otp(request, form.cleaned_data["otp"])
        if ok:
            return redirect("reset_password")
        form.add_error("otp", error)
        state = utils.get_state(request)

    return render(request, "portal/verify_otp.html", {
        "form": form,
        "target": state["target"],
        "channel": state["channel"],
        "wait": utils.seconds_until_resend(request),
    })


@require_POST
def resend_otp(request):
    state = utils.get_state(request)
    if not state:
        return redirect("forgot_password")

    if utils.seconds_until_resend(request) > 0:
        messages.error(request, "Please wait a moment before requesting another OTP.")
        return redirect("verify_otp")

    user = User.objects.filter(pk=state["user_id"]).first()
    channel = state["channel"]
    code = utils.start_otp(request, user, channel, state["target"])
    if user and not _deliver(request, user, channel, code):
        return redirect("verify_otp")
    messages.info(request, "A new OTP has been sent.")
    return redirect("verify_otp")


def reset_password(request):
    """Step 3: set a new password (only after the OTP was verified)."""
    user = User.objects.filter(pk=utils.verified_user_id(request)).first()
    if not user:
        return redirect("forgot_password")

    form = ResetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        utils.clear(request)
        messages.success(request, "Password changed. Log in with your new password.")
        return redirect("login")

    return render(request, "portal/reset_password.html", {"form": form})


# --------------------------------------------------------- change password
@login_required
def change_password(request):
    """Logged in: old password + new password."""
    form = ChangePasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)  # keeps the user logged in
        messages.success(request, "Your password has been changed.")
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "portal/change_password.html", {"form": form, "cancel_url": resolve_url(settings.LOGIN_REDIRECT_URL)})

# >>> PROFILE-RELATED VIEWS ADDED BELOW <<<
import io
import re
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import RegexValidator
from django.db import models


def _photo_field_name():
    """Name of the ImageField on EmployeeProfile, or None if the model has none."""
    for field in EmployeeProfile._meta.get_fields():
        if isinstance(field, models.ImageField):
            return field.name
    return None


def _initial_phone(raw):
    """Pre-fill the mobile box with a clean 10-digit number (drops +91 / spaces / dashes)."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if len(digits) >= 10 else (raw or "")


def _file_photo_name(user):
    """Storage path used when EmployeeProfile has no ImageField (also used for admins)."""
    return f"profile_photos/user_{user.pk}.jpg"


def _store_file_photo(user, upload):
    """Resize the upload and save it as a JPEG under MEDIA_ROOT/profile_photos/."""
    from PIL import Image, ImageOps  # imported here so the rest of the app never depends on Pillow

    image = ImageOps.exif_transpose(Image.open(upload))
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.split()[-1])
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((600, 600))

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    name = _file_photo_name(user)
    if default_storage.exists(name):
        default_storage.delete(name)
    default_storage.save(name, ContentFile(buffer.getvalue()))


def _current_photo_url(user, employee_profile, photo_field):
    """URL of the user's profile photo, or None."""
    if employee_profile is not None and photo_field:
        photo_file = getattr(employee_profile, photo_field)
        if not photo_file:
            return None
        try:
            return photo_file.url
        except ValueError:
            return None

    name = _file_photo_name(user)
    if not default_storage.exists(name):
        return None
    url = default_storage.url(name)
    try:  # cache-buster so a replaced photo shows immediately
        url += "?v=%d" % int(default_storage.get_modified_time(name).timestamp())
    except (NotImplementedError, OSError, AttributeError):
        pass
    return url


class ProfileEditForm(forms.Form):
    """What a signed-in user may change on their own profile page.

    Employee ID, username and role are deliberately NOT part of this form.
    """

    MAX_PHOTO_BYTES = 2 * 1024 * 1024

    username = forms.CharField(
        max_length=150,
        validators=list(User._meta.get_field("username").validators),
        widget=forms.TextInput(attrs={"autocomplete": "username", "placeholder": "Choose a username"}),
    )
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    phone = forms.CharField(
        required=False,
        validators=[
            RegexValidator(
                r"^[0-9]{10}\Z",
                "Enter exactly 10 digits - numbers only, with no letters, spaces or symbols.",
            )
        ],
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "maxlength": "10",
                "pattern": "[0-9]{10}",
                "autocomplete": "tel-national",
                "placeholder": "10-digit mobile number",
            }
        ),
    )
    photo = forms.ImageField(required=False, widget=forms.FileInput(attrs={"accept": "image/*"}))
    remove_photo = forms.BooleanField(required=False)
    current_password = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )
    password1 = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "id": "pfPassword"})
    )
    password2 = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "id": "pfConfirm"})
    )

    def __init__(self, *args, user, employee_profile=None, photo_field=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.employee_profile = employee_profile
        self.photo_field = photo_field if employee_profile is not None else None

        self.initial.update(
            {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "phone": _initial_phone(employee_profile.phone) if employee_profile is not None else "",
            }
        )

        # Admin accounts have no EmployeeProfile, so no mobile number for them
        if employee_profile is None:
            del self.fields["phone"]

        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-check-input"
            elif not isinstance(widget, forms.FileInput):
                widget.attrs["class"] = "form-control"

    def full_clean(self):
        super().full_clean()
        if self.is_bound:
            for name in self.errors:
                if name in self.fields:
                    widget = self.fields[name].widget
                    widget.attrs["class"] = (widget.attrs.get("class", "") + " is-invalid").strip()

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        taken = User.objects.filter(username__iexact=username).exclude(pk=self.user.pk)
        if taken.exists():
            raise forms.ValidationError("That username is already taken. Please choose another one.")
        return username

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and photo.size > self.MAX_PHOTO_BYTES:
            raise forms.ValidationError("Photo must be 2 MB or smaller.")
        return photo

    def clean(self):
        cleaned = super().clean()
        current = cleaned.get("current_password")
        new1 = cleaned.get("password1")
        new2 = cleaned.get("password2")

        if current or new1 or new2:
            if not current:
                self.add_error("current_password", "Enter your current password to set a new one.")
            elif not self.user.check_password(current):
                self.add_error("current_password", "Your current password is incorrect.")

            if not new1:
                self.add_error("password1", "Enter a new password.")
            elif new1 != new2:
                self.add_error("password2", "The two passwords do not match.")
            elif current and new1 == current:
                self.add_error("password1", "The new password must be different from the current one.")
            else:
                try:
                    validate_password(new1, user=self.user)
                except DjangoValidationError as error:
                    self.add_error("password1", error.messages)
        return cleaned


def _initials(user):
    first = (user.first_name or "").strip()[:1]
    last = (user.last_name or "").strip()[:1]
    return (first + last).upper() or user.username[:2].upper()


def _format_duration(delta):
    total_minutes = int(delta.total_seconds() // 60)
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


PROFILE_FIELD_LABELS = {
    "username": "username",
    "first_name": "first name",
    "last_name": "last name",
    "email": "email",
    "phone": "mobile number",
    "photo": "profile photo",
    "password": "password",
}


def _join_labels(labels):
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _apply_profile_changes(request, form, employee_profile, photo_field):
    """Save the profile form. Returns the list of things that actually changed."""
    user = request.user
    data = form.cleaned_data
    user_changes = [f for f in ("username", "first_name", "last_name", "email") if getattr(user, f) != data[f]]
    password_changed = bool(data.get("password1"))
    profile_changes = []
    stale_file = None
    new_photo = data.get("photo")
    remove_photo = data.get("remove_photo")
    photo_in_field = employee_profile is not None and bool(photo_field)
    photo_in_file = False

    with transaction.atomic():
        if user_changes or password_changed:
            for field in user_changes:
                setattr(user, field, data[field])
            if password_changed:
                user.set_password(data["password1"])
            user.save()
            record_event(
                action="user.updated",
                actor=user,
                target_user=user,
                entity_type="portal.User",
                entity_id=user.pk,
                metadata={"changed_fields": user_changes, "password_changed": password_changed},
            )
            if password_changed:
                record_event(
                    action="auth.password_changed",
                    actor=user,
                    target_user=user,
                    entity_type="portal.User",
                    entity_id=user.pk,
                )

        if employee_profile is not None:
            if "phone" in data and data["phone"] != (employee_profile.phone or ""):
                employee_profile.phone = data["phone"]
                profile_changes.append("phone")

            if photo_in_field:  # picture stored in the model's own ImageField
                current_photo = getattr(employee_profile, photo_field)
                if new_photo:
                    stale_file = current_photo.name if current_photo else None
                    setattr(employee_profile, photo_field, new_photo)
                    profile_changes.append("photo")
                elif remove_photo and current_photo:
                    stale_file = current_photo.name
                    setattr(employee_profile, photo_field, "")
                    profile_changes.append("photo")

            if profile_changes:
                employee_profile.updated_by = user
                employee_profile.save()
                record_event(
                    action="employee.profile.updated",
                    actor=user,
                    target_user=user,
                    entity_type="portal.EmployeeProfile",
                    entity_id=employee_profile.pk,
                    metadata={"changed_fields": profile_changes},
                )

        if not photo_in_field:  # no ImageField on the model (or admin account): store the file directly
            if new_photo:
                _store_file_photo(user, new_photo)
                photo_in_file = True
            elif remove_photo and default_storage.exists(_file_photo_name(user)):
                default_storage.delete(_file_photo_name(user))
                photo_in_file = True
            if photo_in_file:
                record_event(
                    action="user.updated",
                    actor=user,
                    target_user=user,
                    entity_type="portal.User",
                    entity_id=user.pk,
                    metadata={"changed_fields": ["photo"], "password_changed": False},
                )

    # Remove the replaced / removed picture from storage once the save has succeeded
    if stale_file:
        new_name = getattr(employee_profile, photo_field).name
        if stale_file != new_name:
            try:
                employee_profile._meta.get_field(photo_field).storage.delete(stale_file)
            except OSError:
                pass

    if password_changed:
        update_session_auth_hash(request, user)  # keep the user signed in

    return (
        user_changes
        + profile_changes
        + (["photo"] if photo_in_file else [])
        + (["password"] if password_changed else [])
    )


def _flash_profile_result(request, changes):
    if not changes:
        messages.info(request, "No changes were made to your profile.")
        return
    text = "Profile updated successfully. You updated your " + _join_labels(
        [PROFILE_FIELD_LABELS[c] for c in changes]
    ) + "."
    if "password" in changes:
        text += " You are still signed in."
    messages.success(request, text)


@login_required
def profile(request):
    """Profile page for the signed-in user (admin or employee), with an edit form."""
    user = request.user

    try:
        employee_profile = user.employee_profile
    except EmployeeProfile.DoesNotExist:
        employee_profile = None  # e.g. admin accounts have no employee profile

    photo_field = _photo_field_name() if employee_profile is not None else None
    form_kwargs = {"user": user, "employee_profile": employee_profile, "photo_field": photo_field}
    open_modal = False

    if request.method == "POST":
        form = ProfileEditForm(request.POST, request.FILES, **form_kwargs)
        if form.is_valid():
            try:
                changes = _apply_profile_changes(request, form, employee_profile, photo_field)
            except IntegrityError:
                form.add_error(None, "Your profile could not be saved. Please try again.")
                messages.error(request, "Your profile could not be saved. Please try again.")
                open_modal = True
            else:
                _flash_profile_result(request, changes)
                return redirect("profile")
        else:
            messages.error(
                request, "Your changes were not saved. Please fix the highlighted fields and try again."
            )
            open_modal = True
    else:
        form = ProfileEditForm(**form_kwargs)

    photo_url = _current_photo_url(user, employee_profile, photo_field)

    today = timezone.localdate()
    month_records = Attendance.objects.filter(
        user=user,
        work_date__year=today.year,
        work_date__month=today.month,
    )

    days_present = month_records.filter(check_in_at__isnull=False).count()
    durations = [r.work_duration for r in month_records if r.work_duration]
    average_hours = _format_duration(sum(durations, timedelta()) / len(durations)) if durations else None
    last_record = (
        Attendance.objects.filter(user=user, check_in_at__isnull=False)
        .order_by("-work_date")
        .first()
    )

    # User Activity card: recent check-in / check-out events, newest first
    activity_records = (
        Attendance.objects.filter(user=user)
        .filter(models.Q(check_in_at__isnull=False) | models.Q(check_out_at__isnull=False))
        .order_by("-work_date", "-pk")[:30]
    )
    activity_events = []
    for record in activity_records:
        if record.check_in_at:
            activity_events.append(
                {"kind": "in", "at": record.check_in_at, "note": getattr(record, "check_in_note", "")}
            )
        if record.check_out_at:
            activity_events.append(
                {"kind": "out", "at": record.check_out_at, "note": getattr(record, "check_out_note", "")}
            )
    activity_events.sort(key=lambda event: event["at"], reverse=True)
    for event in activity_events:
        if event["kind"] == "out":
            event["is_last"] = True  # flag only the most recent check-out
            break

    return render(
        request,
        "portal/profile.html",
        {
            "profile_user": user,
            "employee_profile": employee_profile,
            "initials": _initials(user),
            "month_label": today.strftime("%B %Y"),
            "days_present": days_present,
            "average_hours": average_hours,
            "last_record": last_record,
            "activity_events": activity_events,
            "form": form,
            "open_modal": open_modal,
            "photo_url": photo_url,
        },
    )

class AdminRequiredMixin(LoginRequiredMixin):
    """Class-based version of the admin_required decorator."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != User.Role.ADMIN:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

class EmployeeQuickEditForm(forms.Form):
    department = forms.ChoiceField(
        choices=[("", "---------"), *EmployeeProfile.Department.choices],
        required=False,
    )
    status = forms.ChoiceField(choices=EmployeeProfile.Status.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select"

class EmployeeListView(AdminRequiredMixin, TemplateView):
    """Renders the page. Rows are loaded by DataTables through EmployeeDataView."""
    template_name = "portal/employees.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = EmployeeQuickEditForm()
        return context


class EmployeeDataView(AdminRequiredMixin, View):
    """JSON feed for the DataTable."""

    def get(self, request):
        profiles = EmployeeProfile.objects.select_related("user").all()
        data = []
        for p in profiles:
            editable = p.user.role == User.Role.EMPLOYEE
            data.append({
                "employee_id": p.employee_id,
                "name": p.user.get_full_name() or p.user.username,
                "department": p.get_department_display() or "-",
                "status": p.status,
                "status_label": p.get_status_display(),
                "department_value": p.department,
                "editable": editable,
                "edit_url": reverse("employee_quick_edit", args=[p.pk]) if editable else "",
            })
        # return JsonResponse({"data": data})
        response = JsonResponse({"data": data})
        response["Cache-Control"] = "no-store"   # always send fresh rows, never a cached copy
        return response


class EmployeeQuickEditView(AdminRequiredMixin, View):
    """Saves the modal: only department and status."""

    def post(self, request, profile_id):
        profile = get_object_or_404(
            EmployeeProfile.objects.select_related("user"),
            pk=profile_id,
            user__role=User.Role.EMPLOYEE,
        )
        form = EmployeeQuickEditForm(request.POST)
        if not form.is_valid():
            errors = {field: list(errs) for field, errs in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errors}, status=400)

        data = form.cleaned_data
        with transaction.atomic():
            employee = profile.user

            changed = [f for f in ("department", "status") if getattr(profile, f) != data[f]]
            if changed:
                for field in changed:
                    setattr(profile, field, data[field])
                profile.updated_by = request.user
                profile.save()
                record_event(
                    action="employee.profile.updated",
                    actor=request.user,
                    target_user=employee,
                    entity_type="portal.EmployeeProfile",
                    entity_id=profile.pk,
                    metadata={"changed_fields": changed},
                )

            should_be_active = data["status"] == EmployeeProfile.Status.ACTIVE
            if employee.is_active != should_be_active:
                employee.is_active = should_be_active
                employee.save(update_fields=["is_active"])
                record_event(
                    action="user.updated",
                    actor=request.user,
                    target_user=employee,
                    entity_type="portal.User",
                    entity_id=employee.pk,
                    metadata={"changed_fields": ["is_active"], "password_changed": False},
                )

        return JsonResponse({"ok": True, "message": f"Employee {profile.employee_id} updated."})

class EmployeeFormView(AdminRequiredMixin, View):
    """Add / edit employee. Supports normal posts and AJAX (live validation + save)."""

    template_name = "portal/employee_form.html"
    account_fields = ("username", "password", "first_name", "last_name", "email")
    employment_fields = ("employee_id", "department", "phone", "status")

    # ---------- helpers ----------
    def get_profile(self, profile_id):
        if profile_id is None:
            return None
        return get_object_or_404(
            EmployeeProfile.objects.select_related("user"),
            pk=profile_id,
            user__role=User.Role.EMPLOYEE,
        )

    def render_form(self, request, form, profile):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "profile": profile,
                "is_edit": profile is not None,
                "account_fields": [form[name] for name in self.account_fields],
                "employment_fields": [form[name] for name in self.employment_fields],
            },
        )

    @staticmethod
    def is_ajax(request):
        return request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("ajax") == "1"

    @staticmethod
    def error_response(form):
        errors = {field: list(errs) for field, errs in form.errors.items()}
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    # ---------- GET ----------
    def get(self, request, profile_id=None):
        profile = self.get_profile(profile_id)
        return self.render_form(request, EmployeeForm(profile=profile), profile)

    # ---------- POST ----------
    def post(self, request, profile_id=None):
        profile = self.get_profile(profile_id)
        form = EmployeeForm(request.POST, profile=profile)
        ajax = self.is_ajax(request)

        if not form.is_valid():
            if ajax:
                return self.error_response(form)
            return self.render_form(request, form, profile)

        # live validation while typing/leaving a field: check only, do not save
        if ajax and request.POST.get("validate_only") == "1":
            return JsonResponse({"ok": True, "validated": True})

        saved = None
        is_new = profile is None
        attempts = 3 if is_new else 1  # a new ID is regenerated if two admins save at the same moment
        for attempt in range(attempts):
            try:
                with transaction.atomic():
                    saved = self.save_employee(request, form.cleaned_data, profile)
                break
            except IntegrityError:
                if attempt == attempts - 1:
                    form.add_error(None, "The employee could not be saved because a username or employee ID is already in use.")
                    if ajax:
                        return self.error_response(form)
                    return self.render_form(request, form, profile)

        if ajax:
            return JsonResponse({
                "ok": True,
                "message": "Employee added successfully." if is_new else "Employee updated successfully.",
                "employee_id": saved.employee_id,
                "redirect_url": reverse("employee_list"),
            })
        messages.success(request, "Employee saved.")
        return redirect("employee_list")

    # ---------- save logic (unchanged from the old function view) ----------
    def save_employee(self, request, data, profile):
        if profile is None:
            employee = User.objects.create_user(
                username=data["username"],
                password=data["password"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                email=data["email"],
                role=User.Role.EMPLOYEE,
            )
            profile = EmployeeProfile.objects.create(
                user=employee,
                employee_id=next_employee_id(),
                department=data["department"],
                phone=data["phone"],
                status=data["status"],
                created_by=request.user,
                updated_by=request.user,
            )
            record_event(
                action="user.created",
                actor=request.user,
                target_user=employee,
                entity_type="portal.User",
                entity_id=employee.pk,
                metadata={"role": employee.role},
            )
            record_event(
                action="employee.profile.created",
                actor=request.user,
                target_user=employee,
                entity_type="portal.EmployeeProfile",
                entity_id=profile.pk,
                metadata={"employee_id": profile.employee_id},
            )
        else:
            employee = profile.user
            user_fields = ("username", "first_name", "last_name", "email")
            user_changes = [field for field in user_fields if getattr(employee, field) != data[field]]
            profile_fields = ("employee_id", "department", "phone", "status")
            profile_changes = [field for field in profile_fields if getattr(profile, field) != data[field]]
            should_be_active = data["status"] == EmployeeProfile.Status.ACTIVE
            if employee.is_active != should_be_active:
                user_changes.append("is_active")
            password_changed = bool(data["password"])
            if user_changes or password_changed:
                for field in user_fields:
                    setattr(employee, field, data[field])
                employee.is_active = should_be_active
                if password_changed:
                    employee.set_password(data["password"])
                employee.save()
                record_event(
                    action="user.updated",
                    actor=request.user,
                    target_user=employee,
                    entity_type="portal.User",
                    entity_id=employee.pk,
                    metadata={
                        "changed_fields": user_changes,
                        "password_changed": password_changed,
                    },
                )
                if password_changed:
                    record_event(
                        action="auth.password_changed",
                        actor=request.user,
                        target_user=employee,
                        entity_type="portal.User",
                        entity_id=employee.pk,
                    )
            if profile_changes:
                for field in profile_fields:
                    setattr(profile, field, data[field])
                profile.updated_by = request.user
                profile.save()
                record_event(
                    action="employee.profile.updated",
                    actor=request.user,
                    target_user=employee,
                    entity_type="portal.EmployeeProfile",
                    entity_id=profile.pk,
                    metadata={"changed_fields": profile_changes},
                )
        return profile

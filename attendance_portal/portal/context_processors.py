from django.utils import timezone

from .models import Attendance


def attendance_widget(request):
    """Expose today's attendance state so the top-nav check-in/out button works on every page."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    attendance = Attendance.objects.filter(user=user, work_date=timezone.localdate()).first()
    if attendance is None or attendance.check_in_at is None:
        state = "ready"       # not checked in yet
    elif attendance.check_out_at is None:
        state = "working"     # checked in, not checked out
    else:
        state = "done"        # day complete
    return {"nav_attendance": attendance, "nav_attendance_state": state}
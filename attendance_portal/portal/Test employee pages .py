import re
from datetime import date, datetime, time
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from .models import Attendance, EmployeeProfile, User

TODAY = date(2026, 11, 12)  # a Thursday; its Sunday-Saturday week is 8-14 Nov


def greeting_banner(response):
    """The green welcome banner's HTML (so we can check what the greeting does and does not say)."""
    return re.search(r'<section class="card ap-hero.*?</section>', response.content.decode(), re.S).group(0)


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class EmployeePagesTests(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.user = User.objects.create_user(username="emp", password="Pass-12345-x", first_name="Ravi")
        EmployeeProfile.objects.create(user=self.user, employee_id="E-1", department="IT")
        self.admin = User.objects.create_user(
            username="boss", password="Pass-12345-x", role=User.Role.ADMIN, first_name="Asha"
        )
        # Joined on Sunday 1 Nov, so Monday 2 Nov is the first working day that can count as absent.
        User.objects.filter(pk__in=[self.user.pk, self.admin.pk]).update(date_joined=at(date(2026, 11, 1), 10))
        self.user.refresh_from_db()
        self.admin.refresh_from_db()

        def rec(day, ci, co=None):
            Attendance.objects.create(
                user=self.user, work_date=date(2026, 11, day),
                check_in_at=ci, check_out_at=co, check_in_note="wfh" if day == 3 else "",
            )

        rec(2, at(date(2026, 11, 2), 9, 0), at(date(2026, 11, 2), 18, 0))      # on time, 9h
        rec(3, at(date(2026, 11, 3), 10, 30), at(date(2026, 11, 3), 18, 30))   # late, 8h
        rec(4, at(date(2026, 11, 4), 9, 5))                                     # on time, forgot to check out
        # Nov 5 and 6: absent
        rec(9, at(date(2026, 11, 9), 9, 10), at(date(2026, 11, 9), 17, 10))    # on time, 8h
        # Nov 10: absent
        rec(11, at(date(2026, 11, 11), 9, 0), at(date(2026, 11, 11), 17, 30))  # on time, 8h30
        rec(12, at(TODAY, 9, 20))                                               # today, working

    # ------------------------------------------------------------ employee dashboard
    def test_employee_dashboard_working(self):
        self.client.force_login(self.user)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        c = r.context
        self.assertEqual(c["punch_state"], "working")
        self.assertEqual(c["arrival_note"], "10 min before the 09:30 start")
        self.assertFalse(c["today_row"]["late"])
        self.assertContains(r, "Check-in today")
        self.assertContains(r, "Check-out today")
        self.assertContains(r, "On-time arrival")
        self.assertContains(r, "Worked hours today")
        self.assertContains(r, "9:20 AM")
        self.assertContains(r, "Currently working")
        self.assertContains(r, "Check out")
        self.assertContains(r, "Good ")
        self.assertContains(r, "ID E-1")
        # things that were removed
        banner = greeting_banner(r)
        for gone in ("Day complete", "streak", "present"):
            self.assertNotIn(gone.lower(), banner.lower())
        for gone in ("streak", "monthChart", "Hours this week", "summary"):
            self.assertNotContains(r, gone)

    def test_weekly_chart_runs_sunday_to_saturday(self):
        Attendance.objects.filter(work_date=TODAY).update(check_out_at=at(TODAY, 17, 20))  # 8h exactly
        self.client.force_login(self.user)
        chart = self.client.get("/").context["week_chart"]
        self.assertEqual(chart["labels"], ["Sun 08", "Mon 09", "Tue 10", "Wed 11", "Thu 12", "Fri 13", "Sat 14"])
        self.assertEqual(chart["hours"], [0, 8.0, 0, 8.5, 8.0, 0, 0])
        self.assertEqual(chart["today_index"], 4)
        self.assertEqual(chart["target"], 8)

    def test_recent_attendance_is_only_the_current_week(self):
        self.client.force_login(self.user)
        r = self.client.get("/")
        dates = [row["date"] for row in r.context["recent"]]
        self.assertEqual(dates, [date(2026, 11, d) for d in (12, 11, 10, 9)])   # newest first, nothing before Sunday 8 Nov
        self.assertEqual([row["code"] for row in r.context["recent"]], ["ontime", "ontime", "absent", "ontime"])
        self.assertEqual(r.context["week_label"], "Nov 8 - Nov 14")
        self.assertNotContains(r, "Tue, Nov 3")

    def test_dashboard_states_ready_and_done_and_late(self):
        self.client.force_login(self.user)
        Attendance.objects.filter(work_date=TODAY).delete()
        r = self.client.get("/")
        self.assertEqual(r.context["punch_state"], "ready")
        self.assertContains(r, "Check in now")
        self.assertContains(r, "Not checked in")
        self.assertEqual(r.context["worked"], "0h 00m")

        Attendance.objects.create(user=self.user, work_date=TODAY, check_in_at=at(TODAY, 10, 0), check_out_at=at(TODAY, 18, 0))
        r = self.client.get("/")
        c = r.context
        self.assertEqual(c["punch_state"], "done")
        self.assertEqual(c["worked"], "8h 00m")
        self.assertEqual(c["worked_pct"], 100)
        self.assertEqual(c["arrival_note"], "30 min after the 09:30 start")
        self.assertTrue(c["today_row"]["late"])
        self.assertContains(r, "Checked out")
        self.assertContains(r, ">Late<")
        self.assertIn("Thank you for your work today", greeting_banner(r))
        self.assertNotIn("Day complete", greeting_banner(r))

    # ------------------------------------------------------------ admin = employee too
    def test_admin_dashboard_keeps_all_cards_but_greeting_has_no_chips_or_buttons(self):
        emp2 = User.objects.create_user(username="emp2", password="Pass-12345-x", first_name="Meera")
        EmployeeProfile.objects.create(user=emp2, employee_id="E-2", department="HR")
        emp3 = User.objects.create_user(username="emp3", password="Pass-12345-x", first_name="Dev")
        EmployeeProfile.objects.create(user=emp3, employee_id="E-3", department="IT")
        Attendance.objects.create(user=emp3, work_date=TODAY, check_in_at=at(TODAY, 10, 30), check_out_at=at(TODAY, 18, 0))
        # The admin checks in too, but must not change the employee numbers.
        Attendance.objects.create(user=self.admin, work_date=TODAY, check_in_at=at(TODAY, 9, 0))

        self.client.force_login(self.admin)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        c = r.context
        # 3 employees (E-1 working, E-2 not in, E-3 late + checked out)
        self.assertEqual((c["total"], c["present"], c["absent"], c["rate"]), (3, 2, 1, 66.7))
        self.assertEqual((c["on_time"], c["late"], c["working_now"], c["checked_out"]), (1, 1, 1, 1))
        self.assertEqual([e.employee_id for e in c["not_in"]], ["E-2"])
        self.assertEqual({d["name"]: (d["present"], d["total"]) for d in c["departments"]},
                         {"Information Technology": (2, 2), "Human Resources": (0, 1)})
        self.assertEqual(len(c["trend"]["labels"]), 7)

        # everything below the greeting is still on the page
        for kept in ("Total employees", "Present today", "Attendance rate", "Absent", "Attendance trend",
                     "Today's breakdown", "By department", "Yet to check in", "Recent activity",
                     "Today's attendance log", "trendChart", "statusChart"):
            self.assertContains(r, kept)

        # the greeting: professional text only, no chips and no buttons
        banner = greeting_banner(r)
        self.assertIn("Good ", banner)
        self.assertIn("administration console", banner)
        self.assertIn("2</strong> of <strong>3</strong> employees have checked in", banner)
        self.assertIn("You checked in at", banner)   # the admin's own status, as plain text
        for gone in ("ap-chip", "On time:", "Late:", "Working now:", "Checked out:",
                     "Add employee", "Attendance history", "<a ", "<button"):
            self.assertNotIn(gone, banner)

    def test_admin_sidebar_has_my_attendance_and_admin_links(self):
        self.client.force_login(self.admin)
        r = self.client.get("/")
        for label in ("Dashboard", "My attendance", "Employees", "Audit trail", "Logout"):
            self.assertContains(r, label)
        self.assertContains(r, 'href="/my-attendance/"')
        self.assertContains(r, 'href="/attendance/"')

    def test_admin_can_check_in_out_and_sees_own_status(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/employees/"), 'action="/attendance/check-in/"')   # top-nav button
        self.assertRedirects(self.client.post("/attendance/check-in/", {"next": "/"}), "/")
        self.assertContains(self.client.get("/"), "You checked in at")
        self.assertContains(self.client.get("/employees/"), 'action="/attendance/check-out/"')
        self.client.post("/attendance/check-out/", {"next": "/"})
        self.assertContains(self.client.get("/"), "You checked out at")
        self.assertIsNotNone(Attendance.objects.get(user=self.admin, work_date=TODAY).check_out_at)

    def test_admin_my_attendance_page(self):
        Attendance.objects.create(user=self.admin, work_date=date(2026, 11, 9), check_in_at=at(date(2026, 11, 9), 9, 0), check_out_at=at(date(2026, 11, 9), 17, 0))
        self.client.force_login(self.admin)
        r = self.client.get("/my-attendance/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["report"]["present"], 1)
        self.assertContains(r, "My attendance")

    # ------------------------------------------------------------ my attendance page
    def test_my_attendance_numbers(self):
        self.client.force_login(self.user)
        r = self.client.get("/my-attendance/")
        self.assertEqual(r.status_code, 200)
        rep = r.context["report"]
        self.assertEqual((rep["present"], rep["late"], rep["on_time"], rep["absent"]), (6, 1, 5, 3))
        self.assertEqual(rep["rate"], 66.7)
        self.assertEqual(rep["total_hours"], "33h 30m")      # 9 + 8 + 8 + 8.5
        self.assertEqual(rep["avg_hours"], "8h 22m")
        self.assertEqual(r.context["month_label"], "November 2026")
        self.assertIsNone(r.context["next_month"])
        self.assertEqual(r.context["prev_month"], "2026-10")
        self.assertContains(r, "Missing")      # Nov 4: no check-out
        self.assertContains(r, "Working")      # today
        self.assertContains(r, "wfh")          # note tooltip
        self.assertEqual(len(rep["weeks"]), 6)   # 1 Nov is a Sunday, 30 Nov a Monday

    def test_previous_month_and_bad_input(self):
        self.client.force_login(self.user)
        r = self.client.get("/my-attendance/?month=2026-10")
        self.assertEqual(r.context["month_label"], "October 2026")
        self.assertEqual(r.context["next_month"], "2026-11")
        self.assertEqual(r.context["report"]["present"], 0)
        self.assertContains(r, "No attendance recorded")
        for bad in ("garbage", "2026-13", "2099-01", "0001-01", ""):
            r = self.client.get("/my-attendance/", {"month": bad})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.context["month_label"], "November 2026", bad)

    def test_no_absences_before_the_person_joined(self):
        self.client.force_login(self.user)
        rep = self.client.get("/my-attendance/?month=2026-10").context["report"]
        self.assertEqual((rep["absent"], rep["present"], len(rep["rows"])), (0, 0, 0))

    def test_my_attendance_only_shows_own_records(self):
        other = User.objects.create_user(username="other", password="Pass-12345-x")
        EmployeeProfile.objects.create(user=other, employee_id="E-2")
        Attendance.objects.create(user=other, work_date=date(2026, 11, 2), check_in_at=at(date(2026, 11, 2), 9, 0))
        self.client.force_login(other)
        self.assertEqual(self.client.get("/my-attendance/").context["report"]["present"], 1)

    def test_access_rules(self):
        self.assertRedirects(self.client.get("/my-attendance/"), "/login/?next=/my-attendance/")
        self.user.employee_profile.status = EmployeeProfile.Status.INACTIVE
        self.user.employee_profile.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/my-attendance/").status_code, 403)
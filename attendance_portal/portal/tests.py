from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase

from .models import Attendance, AuditEvent, EmployeeProfile, User


class PortalWorkflowTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user(username="employee", password="safe-test-password")
        EmployeeProfile.objects.create(user=self.employee, employee_id="E-100")
        self.admin = User.objects.create_user(
            username="portal-admin",
            password="safe-admin-password",
            role=User.Role.ADMIN,
        )
        EmployeeProfile.objects.create(user=self.admin, employee_id="A-100")

    def test_employee_login_and_dashboard_use_standalone_login_and_shared_app_layout(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "mobileSidebar")

        response = self.client.post(
            "/login/",
            {"employee_id": "E-100", "password": "safe-test-password"},
            HTTP_USER_AGENT="Portal test",
        )
        self.assertEqual(response.status_code, 302)
        login_event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual(login_event.actor, self.employee)
        self.assertTrue(login_event.request_id)
        self.assertEqual(login_event.user_agent, "Portal test")
        self.assertEqual(response["X-Request-ID"], login_event.request_id)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check in")
        self.assertContains(response, "mobileSidebar")
        self.assertNotContains(response, "Employees</a>")

    def test_failed_login_is_audited_without_credentials(self):
        self.client.post("/login/", {"employee_id": "E-100", "password": "wrong-secret"})

        event = AuditEvent.objects.get(action="auth.login_failed")
        self.assertEqual(event.metadata, {"employee_id": "E-100"})
        self.assertNotIn("wrong-secret", str(event.metadata))

    def test_unknown_employee_id_login_is_audited_without_password(self):
        self.client.post("/login/", {"employee_id": "UNKNOWN-9", "password": "wrong-secret"})

        event = AuditEvent.objects.get(action="auth.login_failed")
        self.assertEqual(event.metadata, {"employee_id": "UNKNOWN-9"})
        self.assertNotIn("wrong-secret", str(event.metadata))

    def test_username_is_not_accepted_as_the_login_identifier(self):
        response = self.client.post(
            "/login/",
            {"employee_id": "employee", "password": "safe-test-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_can_also_log_in_with_their_employee_id(self):
        response = self.client.post(
            "/login/",
            {"employee_id": "A-100", "password": "safe-admin-password"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action="auth.login", actor=self.admin).exists())

    def test_employee_checkin_checkout_and_duplicate_protection(self):
        self.client.force_login(self.employee)
        response = self.client.post("/attendance/check-in/")
        self.assertEqual(response.status_code, 302)
        attendance = Attendance.objects.get(user=self.employee)
        self.assertIsNotNone(attendance.check_in_at)

        response = self.client.post("/attendance/check-in/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AuditEvent.objects.filter(action="attendance.checked_in").count(), 1)

        response = self.client.post("/attendance/check-out/")
        self.assertEqual(response.status_code, 302)
        attendance.refresh_from_db()
        self.assertIsNotNone(attendance.check_out_at)
        self.assertGreaterEqual(attendance.work_duration.total_seconds(), 0)
        self.assertTrue(AuditEvent.objects.filter(action="attendance.checked_out").exists())

    def test_inactive_employee_cannot_use_attendance(self):
        self.employee.employee_profile.status = EmployeeProfile.Status.INACTIVE
        self.employee.employee_profile.save()
        self.client.force_login(self.employee)

        self.assertEqual(self.client.get("/").status_code, 403)
        self.assertEqual(self.client.post("/attendance/check-in/").status_code, 403)
        self.assertFalse(Attendance.objects.exists())

    def test_website_admin_role_can_manage_employees_review_attendance_and_audit(self):
        self.assertFalse(self.admin.is_staff)
        self.client.force_login(self.admin)

        response = self.client.get("/employees/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add employee")

        response = self.client.post(
            "/employees/new/",
            {
                "username": "new-employee",
                "first_name": "New",
                "last_name": "Employee",
                "email": "new@example.com",
                "employee_id": "E-101",
                "department": "IT",
                "phone": "1234567890",
                "status": "active",
                "password": "safe-new-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        employee = User.objects.get(username="new-employee")
        profile = employee.employee_profile
        self.assertTrue(profile.employee_id.startswith("EMP"))
        self.assertEqual(profile.created_by, self.admin)
        self.assertTrue(AuditEvent.objects.filter(action="user.created", target_user=employee).exists())
        self.assertTrue(
            AuditEvent.objects.filter(action="employee.profile.created", target_user=employee).exists()
        )

        Attendance.objects.create(user=employee)
        self.assertEqual(self.client.get("/attendance/").status_code, 200)
        self.assertContains(self.client.get("/attendance/"), "New Employee")
        response = self.client.get("/audit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Employee created")

        response = self.client.post(
            f"/employees/{profile.pk}/edit/",
            {
                "username": "new-employee",
                "first_name": "New",
                "last_name": "Employee",
                "email": "new@example.com",
                "employee_id": profile.employee_id,
                "department": "IT",
                "phone": "1234567890",
                "status": "inactive",
                "password": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        employee.refresh_from_db()
        self.assertEqual(profile.status, EmployeeProfile.Status.INACTIVE)
        self.assertFalse(employee.is_active)
        self.assertTrue(
            AuditEvent.objects.filter(action="employee.profile.updated", target_user=employee).exists()
        )

    def test_employee_creation_rejects_weak_passwords(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            "/employees/new/",
            {
                "username": "new-employee",
                "first_name": "New",
                "last_name": "Employee",
                "email": "new@example.com",
                "employee_id": "E-101",
                "department": "IT",
                "phone": "",
                "status": "active",
                "password": "password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="new-employee").exists())

    def test_employee_is_forbidden_from_admin_pages(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get("/employees/").status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/employees/").status_code, 200)

    def test_audit_page_uses_readable_summaries_and_descriptive_headers(self):
        AuditEvent.objects.create(
            action="auth.login",
            actor=self.employee,
            actor_identifier=str(self.employee.pk),
            target_user=self.employee,
            target_identifier=str(self.employee.pk),
        )
        AuditEvent.objects.create(
            action="auth.login_failed",
            metadata={"employee_id": "UNKNOWN-9"},
        )
        self.client.force_login(self.admin)

        response = self.client.get("/audit/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Date &amp; time")
        self.assertContains(response, "Account")
        self.assertContains(response, "Summary")
        self.assertNotContains(response, "Performed by")
        self.assertNotContains(response, "IP address</th>")
        self.assertContains(response, "Successful sign-in")
        self.assertContains(response, "Signed in successfully.")
        self.assertContains(response, "Sign-in failed for employee ID UNKNOWN-9")
        self.assertNotContains(response, "<td>{}</td>")

    def test_audit_events_are_append_only_and_attendance_preserves_employee(self):
        event = AuditEvent.objects.create(action="test.event")
        event.action = "changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

        Attendance.objects.create(user=self.employee)
        with self.assertRaises(ProtectedError):
            self.employee.delete()
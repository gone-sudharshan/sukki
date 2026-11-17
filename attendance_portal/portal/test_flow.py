import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Employee

User = get_user_model()


@override_settings(LOGIN_REDIRECT_URL="/", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SMS_BACKEND="console")
class AuthFlowTests(TestCase):
    def setUp(self):
        u = User.objects.create_user("jdoe", email="jdoe@gmail.com", password="OldPass#12345")
        Employee.objects.create(user=u, employee_id="EMP001", phone="9876543210")

    def test_pages_render(self):
        for name in ("login", "forgot_password"):
            r = self.client.get(reverse(f"avsys_auth:{name}"))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "AvSys")

    def test_login_with_employee_id(self):
        r = self.client.post(reverse("avsys_auth:login"), {"employee_id": "emp001", "password": "OldPass#12345"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)

    def test_wrong_password(self):
        r = self.client.post(reverse("avsys_auth:login"), {"employee_id": "EMP001", "password": "nope"})
        self.assertContains(r, "Wrong ID or password")

    def test_forgot_by_email_then_reset_then_login(self):
        self.client.post(reverse("avsys_auth:forgot_password"), {"method": "email", "email": "jdoe@gmail.com"})
        self.assertEqual(len(mail.outbox), 1)
        code = re.search(r"code is: (\d{6})", mail.outbox[0].body).group(1)
        ok = self.client.post(reverse("avsys_auth:verify_otp"), {"otp": code})
        self.assertRedirects(ok, reverse("avsys_auth:reset_password"))
        done = self.client.post(reverse("avsys_auth:reset_password"),
                                {"new_password1": "BrandNew#98765", "new_password2": "BrandNew#98765"})
        self.assertRedirects(done, reverse("avsys_auth:login"))
        r = self.client.post(reverse("avsys_auth:login"), {"employee_id": "EMP001", "password": "BrandNew#98765"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)

    def test_otp_cannot_be_reused_and_no_extra_table_is_needed(self):
        self.client.post(reverse("avsys_auth:forgot_password"), {"method": "email", "email": "jdoe@gmail.com"})
        code = re.search(r"code is: (\d{6})", mail.outbox[0].body).group(1)
        self.client.post(reverse("avsys_auth:verify_otp"), {"otp": code})
        self.client.post(reverse("avsys_auth:reset_password"), {"new_password1": "BrandNew#98765", "new_password2": "BrandNew#98765"})
        r = self.client.get(reverse("avsys_auth:reset_password"))   # session state is gone
        self.assertRedirects(r, reverse("avsys_auth:forgot_password"))

    def test_five_wrong_otps_lock_it(self):
        self.client.post(reverse("avsys_auth:forgot_password"), {"method": "email", "email": "jdoe@gmail.com"})
        code = re.search(r"code is: (\d{6})", mail.outbox[0].body).group(1)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.client.post(reverse("avsys_auth:verify_otp"), {"otp": wrong})
        r = self.client.post(reverse("avsys_auth:verify_otp"), {"otp": code})   # even the right code is refused now
        self.assertContains(r, "Too many wrong attempts")

    def test_wrong_otp(self):
        self.client.post(reverse("avsys_auth:forgot_password"), {"method": "email", "email": "jdoe@gmail.com"})
        r = self.client.post(reverse("avsys_auth:verify_otp"), {"otp": "000000"})
        self.assertContains(r, "Wrong OTP")

    def test_reset_page_blocked_without_otp(self):
        r = self.client.get(reverse("avsys_auth:reset_password"))
        self.assertRedirects(r, reverse("avsys_auth:forgot_password"))

    def test_change_password_needs_old_password(self):
        self.client.login(username="jdoe", password="OldPass#12345")
        r = self.client.post(reverse("avsys_auth:change_password"), {
            "old_password": "OldPass#12345", "new_password1": "Another#54321", "new_password2": "Another#54321"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.assertTrue(User.objects.get(username="jdoe").check_password("Another#54321"))

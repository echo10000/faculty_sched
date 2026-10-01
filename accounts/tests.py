from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from core.models import College, Department
from .models import AdminProfile, LoginFailureBucket
from .throttle import _key


class AuthenticationAndScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first = College.objects.create(code="A", name="College Alpha")
        cls.second = College.objects.create(code="B", name="College Bravo")
        cls.department = Department.objects.create(college=cls.first, code="A1", name="Alpha One")
        cls.sibling = Department.objects.create(college=cls.first, code="A2", name="Alpha Two")
        cls.external = Department.objects.create(college=cls.second, code="B1", name="Bravo One")
        cls.password = "Test-only-correct-horse-123!"
        cls.admin = cls.make_user("system", AdminProfile.Role.SUPER_ADMIN, is_staff=True)
        cls.dean = cls.make_user("dean", AdminProfile.Role.STAFF, college=cls.first)
        cls.chair = cls.make_user("chair", AdminProfile.Role.STAFF, department=cls.department)
        cls.staff = cls.make_user("staff", AdminProfile.Role.STAFF, department=cls.department)
        cls.college_staff = cls.make_user("college-staff", AdminProfile.Role.STAFF, college=cls.first)

    @classmethod
    def make_user(cls, name, role, is_staff=False, **scope):
        user = get_user_model().objects.create_user(username=name, password=cls.password, is_staff=is_staff)
        AdminProfile.objects.create(user=user, role=role, **scope)
        return user

    def grant(self, user, codename):
        user.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename=codename))

    def test_anonymous_is_redirected_to_real_login(self):
        response = self.client.get("/dashboard/")
        self.assertRedirects(response, "/accounts/login/?next=/dashboard/")

    def test_login_and_post_logout_are_audited(self):
        response = self.client.post(reverse("accounts:login"), {"username": "dean", "password": self.password})
        self.assertRedirects(response, "/dashboard/")
        self.assertTrue(AuditLog.objects.filter(actor=self.dean, action="auth.login").exists())
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditLog.objects.filter(actor=self.dean, action="auth.logout").exists())

    def test_bad_credentials_never_enter_audit_payload(self):
        secret = "do-not-log-this-password"
        response = self.client.post(reverse("accounts:login"), {"username": "dean", "password": secret})
        self.assertContains(response, "Unable to sign in")
        self.assertNotIn(secret, str(list(AuditLog.objects.values())))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rejects_external_redirect(self):
        response = self.client.post(reverse("accounts:login"), {"username": "dean", "password": self.password, "next": "https://evil.example/"})
        self.assertEqual(response.url, "/dashboard/")

    def test_login_and_logout_enforce_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("accounts:login"), {"username": "dean", "password": self.password}).status_code, 403)
        client.force_login(self.dean)
        self.assertEqual(client.post(reverse("accounts:logout")).status_code, 403)

    def test_inactive_and_disabled_profile_cannot_login(self):
        for disable_user in [True, False]:
            with self.subTest(disable_user=disable_user):
                get_user_model().objects.filter(pk=self.dean.pk).update(is_active=not disable_user)
                AdminProfile.objects.filter(user=self.dean).update(is_enabled=disable_user)
                response = self.client.post(reverse("accounts:login"), {"username": "dean", "password": self.password})
                self.assertContains(response, "Unable to sign in")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_revocation_blocks_existing_session(self):
        self.client.force_login(self.dean)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)
        AdminProfile.objects.filter(user=self.dean).update(is_enabled=False)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_missing_profile_is_denied_even_with_permissions(self):
        user = get_user_model().objects.create_user(username="unassigned", password=self.password)
        self.grant(user, "view_dashboard")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_dean_lists_counts_and_detail_are_college_scoped(self):
        self.client.force_login(self.dean)
        self.assertContains(self.client.get("/colleges/"), self.first.name)
        self.assertNotContains(self.client.get("/colleges/?college=B"), self.second.name)
        response = self.client.get("/departments/")
        self.assertContains(response, self.department.name)
        self.assertContains(response, self.sibling.name)
        self.assertNotContains(response, self.external.name)
        self.assertEqual(self.client.get(f"/colleges/{self.second.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/departments/{self.external.pk}/").status_code, 404)
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.context["college_count"], 1)
        self.assertEqual(dashboard.context["department_count"], 2)

    def test_chair_cannot_see_sibling_department(self):
        self.client.force_login(self.chair)
        response = self.client.get("/departments/")
        self.assertContains(response, self.department.name)
        self.assertNotContains(response, self.sibling.name)
        self.assertEqual(self.client.get(f"/departments/{self.sibling.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/dashboard/").context["department_count"], 1)

    def test_staff_default_access_remains_department_scoped(self):
        self.client.force_login(self.staff)
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'href="/departments/"')
        self.assertEqual(self.client.get("/departments/").status_code, 200)
        self.grant(self.staff, "view_department")
        self.assertContains(self.client.get("/dashboard/"), 'href="/departments/"')
        self.assertContains(self.client.get("/departments/"), self.department.name)
        self.assertEqual(self.client.get(f"/departments/{self.sibling.pk}/").status_code, 404)

    def test_group_grant_does_not_widen_staff_scope(self):
        group = Group.objects.create(name="Department readers")
        group.permissions.add(Permission.objects.get(content_type__app_label="core", codename="view_department"))
        self.college_staff.groups.add(group)
        self.client.force_login(self.college_staff)
        self.assertContains(self.client.get("/departments/"), self.sibling.name)
        self.assertEqual(self.client.get(f"/departments/{self.external.pk}/").status_code, 404)

    def test_system_admin_and_django_superuser_have_institution_scope(self):
        root = get_user_model().objects.create_superuser(username="root", password=self.password)
        for user in [self.admin, root]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertContains(self.client.get("/colleges/"), self.second.name)
                self.assertEqual(self.client.get(f"/departments/{self.external.pk}/").status_code, 200)
                self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_admin_requires_system_role_even_with_is_staff_and_model_permissions(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(is_staff=True)
        self.staff.user_permissions.add(*Permission.objects.all())
        self.client.force_login(self.staff)
        response = self.client.get("/admin/core/college/")
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(self.client.get("/dashboard/"), "System administration")
        before = College.objects.count()
        response = self.client.post("/admin/core/college/add/", {"name": "Unauthorized", "code": "NO"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(College.objects.count(), before)

    def test_read_only_pages_cannot_mutate_records(self):
        self.client.force_login(self.dean)
        self.assertEqual(self.client.post(f"/departments/{self.department.pk}/", {"college": self.second.pk}).status_code, 405)
        self.department.refresh_from_db()
        self.assertEqual(self.department.college_id, self.first.pk)

    def test_retiring_organization_revokes_scope(self):
        self.client.force_login(self.chair)
        College.objects.filter(pk=self.first.pk).update(is_active=False)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_legacy_modules_are_not_exposed(self):
        self.client.force_login(self.admin)
        for path in ["/faculty/dashboard/", "/scheduling/", "/scheduling/autoschedule/", "/admin/faculty/faculty/", "/admin/academics/student/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_scope_constraints_reject_model_and_database_bypass(self):
        with self.assertRaises(ValidationError):
            AdminProfile.objects.create(user=get_user_model().objects.create_user(username="bad-scope"), role="staff")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdminProfile.objects.filter(user=self.dean).update(college=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdminProfile.objects.filter(user=self.staff).update(college=self.first)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdminProfile.objects.filter(user=self.staff).update(role="unknown")

    def test_system_admin_create_is_audited_and_csrf_protected(self):
        self.client.force_login(self.admin)
        response = self.client.post("/admin/core/college/add/", {"name": "College Created", "code": "NEW", "is_active": "on", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        created = College.objects.get(code="NEW")
        self.assertEqual(created.created_by, self.admin)
        self.assertTrue(AuditLog.objects.filter(actor=self.admin, action="record.create", object_type="core.College", object_id=str(created.pk)).exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post("/admin/core/college/add/", {"code": "NO"}).status_code, 403)


class LoginThrottleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Test-only-correct-horse-123!"
        college = College.objects.create(code="TH", name="Throttle College")
        cls.dean = get_user_model().objects.create_user(username="dean", password=cls.password)
        AdminProfile.objects.create(user=cls.dean, role=AdminProfile.Role.STAFF, college=college)
        cls.admin = get_user_model().objects.create_user(
            username="system", password=cls.password, is_staff=True,
        )
        AdminProfile.objects.create(user=cls.admin, role=AdminProfile.Role.SUPER_ADMIN)

    def test_account_limit_blocks_correct_password_until_window_expires(self):
        login = reverse("accounts:login")
        for _ in range(5):
            self.assertEqual(self.client.post(login, {"username": "dean", "password": "wrong"}).status_code, 200)
        bucket = LoginFailureBucket.objects.get(pk=_key("account", "dean"))
        self.assertEqual(bucket.failures, 5)
        self.assertNotIn("dean", str(list(LoginFailureBucket.objects.values())))
        self.assertNotIn("127.0.0.1", str(list(LoginFailureBucket.objects.values())))
        blocked = self.client.post(login, {"username": "dean", "password": self.password})
        self.assertEqual(blocked.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        LoginFailureBucket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertRedirects(self.client.post(login, {"username": "dean", "password": self.password}), "/dashboard/")
        self.assertEqual(LoginFailureBucket.objects.get(pk=_key("account", "dean")).failures, 0)

    def test_success_resets_account_failures_but_not_source_failures(self):
        login = reverse("accounts:login")
        for _ in range(4):
            self.client.post(login, {"username": "dean", "password": "wrong"})
        self.assertRedirects(self.client.post(login, {"username": "dean", "password": self.password}), "/dashboard/")
        self.assertEqual(LoginFailureBucket.objects.get(pk=_key("account", "dean")).failures, 0)
        self.assertEqual(LoginFailureBucket.objects.get(pk=_key("source", "127.0.0.1")).failures, 4)

    def test_admin_login_shares_limit_and_ignores_untrusted_forwarded_for(self):
        for index in range(5):
            response = self.client.post("/admin/login/", {
                "username": "system", "password": "wrong",
            }, HTTP_X_FORWARDED_FOR=f"203.0.113.{index + 1}")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(LoginFailureBucket.objects.get(pk=_key("source", "127.0.0.1")).failures, 5)
        blocked = self.client.post("/admin/login/", {"username": "system", "password": self.password})
        self.assertEqual(blocked.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_source_limit_blocks_password_spray_across_accounts(self):
        login = reverse("accounts:login")
        with patch("accounts.throttle.SOURCE_LIMIT", 2):
            for username in ("unknown-one", "unknown-two"):
                self.client.post(login, {"username": username, "password": "wrong"})
            self.assertEqual(LoginFailureBucket.objects.get(
                pk=_key("source", "127.0.0.1"),
            ).failures, 2)
            response = self.client.post(login, {"username": "dean", "password": self.password})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_purge_command_removes_only_expired_counters(self):
        LoginFailureBucket.objects.create(
            key="a" * 64, failures=5, expires_at=timezone.now() - timedelta(seconds=1),
        )
        LoginFailureBucket.objects.create(
            key="b" * 64, failures=5, expires_at=timezone.now() + timedelta(minutes=15),
        )
        call_command("purge_login_failures", stdout=StringIO())
        self.assertFalse(LoginFailureBucket.objects.filter(pk="a" * 64).exists())
        self.assertTrue(LoginFailureBucket.objects.filter(pk="b" * 64).exists())

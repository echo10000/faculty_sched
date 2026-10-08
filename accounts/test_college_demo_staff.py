"""College sample staff use the real login and server-side scope boundaries."""
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from academics.models import AcademicYear, AcademicTerm, Semester
from audit.models import AuditLog
from core.models import College, Department
from timetabling.models import ClassSection, Schedule, ScheduleGenerationRun
from .models import AdminProfile


@override_settings(DEBUG=True)
class CollegeDemoStaffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("dev.admin", "", "test-admin-only")
        year = AcademicYear.objects.create(label="Sample year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        semester = Semester.objects.create(code="SAMPLE", name="Sample semester")
        cls.term = AcademicTerm.objects.create(academic_year=year, semester=semester, code="SAMPLE",
                                             start_date=date(2026, 1, 1), end_date=date(2026, 6, 30))
        for code in ("CAS", "CCJE", "CIT", "CAF", "CBA"):
            college = College.objects.create(code=code, name=f"Sample {code} college")
            for index in (1, 2):
                department = Department.objects.create(college=college, code=f"{code}{index}",
                                                       name=f"Sample {code} department {index}")
                Schedule.objects.create(department=department, academic_term=cls.term, name=f"{code} draft {index}")
                ClassSection.objects.create(department=department, academic_term=cls.term, year_level=1, code=f"{code}{index}-1A")

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        settings_override = override_settings(BASE_DIR=self.directory)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def provision(self):
        output = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("create_college_demo_staff", actor="dev.admin", stdout=output)
        return output.getvalue()

    def credentials(self):
        return dict(line.split(": ", 1) for line in
                    (self.directory / ".local/development-credentials.txt").read_text().splitlines())

    def test_creates_five_staff_with_working_logins_and_preserves_passwords_on_repeat(self):
        self.provision()
        credentials = self.credentials()
        self.assertEqual(set(credentials), {f"dev.staff.{code}" for code in ("cas", "ccje", "cit", "caf", "cba")})
        self.assertEqual(len(set(credentials.values())), 5)
        for username, password in credentials.items():
            user = get_user_model().objects.get(username=username)
            self.assertFalse(user.is_superuser)
            self.assertFalse(user.is_staff)
            self.assertEqual(user.admin_profile.role, "staff")
            self.assertEqual(user.admin_profile.college.code.lower(), username.rsplit(".", 1)[1])
            self.assertIsNone(user.admin_profile.department)
            self.assertTrue(user.has_perm("timetabling.generate_schedule"))
            self.assertTrue(user.has_perm("timetabling.finalize_schedule"))
            self.assertFalse(user.has_perm("accounts.change_adminprofile"))
            self.assertNotIn(password, str(list(AuditLog.objects.values())))
            response = self.client.post(reverse("accounts:login"), {"username": username, "password": password})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(str(self.client.session["_auth_user_id"]), str(user.pk))
            self.client.logout()
        hashes = dict(get_user_model().objects.values_list("username", "password"))
        audits = AuditLog.objects.count()
        output = self.provision()
        self.assertEqual(self.credentials(), credentials)
        self.assertEqual(dict(get_user_model().objects.values_list("username", "password")), hashes)
        self.assertEqual(AuditLog.objects.count(), audits)
        self.assertIn("0 new", output)

    def test_each_staff_sees_all_own_departments_and_cannot_read_or_mutate_other_colleges(self):
        self.provision()
        for profile in AdminProfile.objects.filter(role="staff").select_related("user", "college"):
            with self.subTest(college=profile.college.code):
                self.client.force_login(profile.user)
                response = self.client.get("/departments/")
                for department in Department.objects.all():
                    if department.college_id == profile.college_id:
                        self.assertContains(response, department.name)
                        self.assertEqual(self.client.get(f"/departments/{department.pk}/").status_code, 200)
                    else:
                        self.assertNotContains(response, department.name)
                        self.assertEqual(self.client.get(f"/departments/{department.pk}/").status_code, 404)
                external = Department.objects.exclude(college=profile.college).first()
                schedule = Schedule.objects.get(department=external)
                section = ClassSection.objects.get(department=external)
                self.assertEqual(self.client.get(reverse("timetabling:schedules-detail", args=[schedule.pk])).status_code, 404)
                self.assertEqual(self.client.get(reverse("timetabling:entries-add", args=[schedule.pk])).status_code, 404)
                self.assertEqual(self.client.post(reverse("timetabling:validate", args=[schedule.pk])).status_code, 404)
                form_response = self.client.get(reverse("timetabling:section-generator"))
                self.assertEqual(set(form_response.context["form"].fields["department"].queryset.values_list(
                    "college_id", flat=True)), {profile.college_id})
                response = self.client.post(reverse("timetabling:section-generator"), {
                    "academic_term": self.term.pk, "department": external.pk, "year_level": 1,
                    "section": section.pk, "schedule": schedule.pk, "strategy": "FILL_GAPS"})
                self.assertEqual(response.status_code, 200)
                self.assertIn("department", response.context["form"].errors)
                self.assertTrue(self.client.get("/admin/").status_code in (302, 403))
        self.assertFalse(ScheduleGenerationRun.objects.exists())
        self.assertFalse(Schedule.objects.exclude(status="draft").exists())

    def test_existing_wrong_scope_username_aborts_without_creating_accounts_or_credentials(self):
        user = get_user_model().objects.create_user("dev.staff.cba", password="existing-only")
        AdminProfile.objects.create(user=user, role="staff", college=College.objects.get(code="CAS"))
        with self.assertRaises(CommandError):
            self.provision()
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertTrue(user.check_password("existing-only"))
        self.assertFalse((self.directory / ".local/development-credentials.txt").exists())

    def test_disabled_sample_is_not_reenabled(self):
        self.provision()
        user = get_user_model().objects.get(username="dev.staff.cas")
        user.is_active = False
        user.save(update_fields=["is_active"])
        profile = user.admin_profile
        profile.is_enabled = False
        profile.save()
        self.provision()
        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(profile.is_enabled)

    def test_rejects_production_and_missing_colleges(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            self.provision()
        College.objects.filter(code="CBA").update(is_active=False)
        with self.assertRaises(CommandError):
            self.provision()
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse((self.directory / ".local/development-credentials.txt").exists())

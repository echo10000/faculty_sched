"""The simplified sample accounts retain scope, identity and historical boundaries."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command, CommandError
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import reverse

from academics.models import AcademicTerm
from audit.models import AuditLog
from core.models import College, Department
from faculty.models import Faculty
from timetabling.models import Schedule
from timetabling.queries import get_schedule
from workloads.selectors import scoped_faculty
from .models import AdminProfile


@override_settings(DEBUG=True)
class DemoAccountTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        settings_override = override_settings(BASE_DIR=self.directory)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        with self.captureOnCommitCallbacks(execute=True):
            call_command("seed_foundation", create_users=True, stdout=StringIO())
        call_command("seed_resources", stdout=StringIO())
        self.college = College.objects.get(code="DEMO-A")
        self.department = Department.objects.get(code="DEMO-D1")
        self.staff = get_user_model().objects.get(username="dev.staff")
        self.profile = self.staff.admin_profile
        self.profile.college, self.profile.department = None, self.department
        self.profile.save()
        for username in ("dev.chair", "dev.dean"):
            user = get_user_model().objects.create_user(username=username, password="test-old-password")
            AdminProfile.objects.create(user=user, role="staff", college=self.college)
        Faculty.objects.create(employee_id="DEMO-F1B", first_name="Morgan", last_name="Example", home_department=self.department)
        self.staff.user_permissions.add(Permission.objects.get(content_type__app_label="faculty", codename="view_facultyqualification"))

    def configure(self):
        output = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("configure_demo_accounts", stdout=output)
        return output.getvalue()

    def test_staff_college_scope_and_custom_grants_are_preserved(self):
        grants = self.staff.get_all_permissions()
        password_hash = self.staff.password
        sibling = Department.objects.create(code="DEMO-SIB", name="Sibling", college=self.college)
        sibling_faculty = Faculty.objects.create(employee_id="SIBLING-F", first_name="Other", last_name="Faculty", home_department=sibling)
        term = AcademicTerm.objects.get(code="DEMO-TERM")
        inside = Schedule.objects.create(name="College schedule", department=sibling, academic_term=term)
        outside = Schedule.objects.create(name="Other college", department=Department.objects.get(code="DEMO-D2"), academic_term=term)
        self.configure()
        self.staff.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.college, self.college)
        self.assertIsNone(self.profile.department)
        self.assertEqual(self.staff.password, password_hash)
        self.assertEqual(self.staff.get_all_permissions(), grants)
        self.assertIn(sibling_faculty, scoped_faculty(self.staff))
        self.assertNotIn(Faculty.objects.get(employee_id="DEMO-F2"), scoped_faculty(self.staff))
        self.assertEqual(get_schedule(self.staff, inside.pk, action="change"), inside)
        with self.assertRaises(Http404):
            get_schedule(self.staff, outside.pk, action="change")
        self.assertTrue(AuditLog.objects.filter(action="account.scope_updated", object_id=str(self.staff.pk)).exists())

    def test_legacy_accounts_are_disabled_without_deleting_schedule_history(self):
        chair = get_user_model().objects.get(username="dev.chair")
        schedule = Schedule.objects.create(name="Existing draft", department=self.department,
            academic_term=AcademicTerm.objects.get(code="DEMO-TERM"), created_by=chair)
        self.client.force_login(chair)
        self.configure()
        for username in ("dev.chair", "dev.dean"):
            user = get_user_model().objects.get(username=username)
            self.assertFalse(user.is_active)
            self.assertFalse(user.admin_profile.is_enabled)
        schedule.refresh_from_db()
        self.assertEqual(schedule.created_by_id, chair.pk)
        self.assertEqual(schedule.status, "draft")
        self.assertRedirects(self.client.get(reverse("home")), reverse("accounts:login") + "?next=" + reverse("home"))

    def test_each_faculty_has_only_their_own_teaching_identity(self):
        self.configure()
        people = list(Faculty.objects.select_related("user").filter(employee_id__in=("DEMO-F1", "DEMO-F2", "DEMO-F1B")))
        self.assertEqual(len({person.user_id for person in people}), 3)
        for person in people:
            with self.subTest(person=person.employee_id):
                self.assertEqual(person.user.admin_profile.role, "faculty")
                self.assertFalse(person.user.is_staff)
                self.assertFalse(person.user.has_perm("timetabling.generate_schedule"))
                self.client.force_login(person.user)
                other = next(item for item in people if item.pk != person.pk)
                response = self.client.get(reverse("faculty-portal:schedule"), {"faculty": other.pk})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["person"], person)
                self.assertEqual(self.client.get(reverse("timetabling:schedules")).status_code, 403)

    def test_rerun_preserves_passwords_credentials_and_account_decisions(self):
        self.configure()
        hashes = dict(get_user_model().objects.values_list("username", "password"))
        credentials = (self.directory / ".local/development-credentials.txt").read_text()
        audits = AuditLog.objects.count()
        self.configure()
        self.assertEqual(dict(get_user_model().objects.values_list("username", "password")), hashes)
        self.assertEqual((self.directory / ".local/development-credentials.txt").read_text(), credentials)
        self.assertEqual(AuditLog.objects.count(), audits)
        for line in credentials.splitlines():
            username, password = line.split(": ", 1)
            self.assertTrue(get_user_model().objects.get(username=username).check_password(password))
        with self.captureOnCommitCallbacks(execute=True):
            call_command("seed_foundation", create_users=True, with_balancing=True, stdout=StringIO())
        self.assertFalse(get_user_model().objects.get(username="dev.chair").is_active)
        self.assertFalse(get_user_model().objects.get(username="dev.dean").is_active)

    def test_existing_username_collision_rolls_back_configuration(self):
        get_user_model().objects.create_user(username="dev.faculty")
        with self.assertRaises(CommandError):
            self.configure()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.department, self.department)
        self.assertTrue(get_user_model().objects.get(username="dev.chair").is_active)
        self.assertFalse(Faculty.objects.exclude(user=None).exists())

    def test_configuration_rejects_production(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("configure_demo_accounts", stdout=StringIO())
        self.assertFalse(Faculty.objects.exclude(user=None).exists())

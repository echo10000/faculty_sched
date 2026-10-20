"""Program setup reuses the existing system-admin boundary and audit trail."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminProfile
from audit.models import AuditLog
from faculty.models import Faculty
from .models import College, Department, Program


class ProgramAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('program-admin', '', 'test-only')
        college = College.objects.create(code='PROG', name='Fictional college')
        cls.department = Department.objects.create(college=college, code='ONE', name='First department')
        cls.other = Department.objects.create(college=college, code='TWO', name='Second department')
        cls.staff = get_user_model().objects.create_user('program-staff', is_staff=True)
        AdminProfile.objects.create(user=cls.staff, role='staff', department=cls.department)
        # Even additive model grants and Django's staff flag cannot bypass the site gate.
        cls.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='core', content_type__model='program'))
        cls.faculty_user = get_user_model().objects.create_user('program-faculty')
        Faculty.objects.create(user=cls.faculty_user, employee_id='PROG-F', first_name='Example',
            last_name='Faculty', home_department=cls.department)
        AdminProfile.objects.create(user=cls.faculty_user, role='faculty')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_admin_navigation_and_create_are_available_and_audited(self):
        self.assertContains(self.client.get(reverse('home')), reverse('admin:core_program_changelist'))
        self.assertContains(self.client.get(reverse('admin:index')), 'Programs')
        response = self.client.post(reverse('admin:core_program_add'), {
            'code': 'DEMO', 'name': 'Fictional program', 'department': self.department.pk, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        program = Program.objects.get(code='DEMO')
        self.assertEqual(program.department, self.department)
        self.assertTrue(AuditLog.objects.filter(action='record.create', actor=self.admin,
            object_id=str(program.pk), object_type='core.Program').exists())

    def test_admin_edit_and_department_linkage(self):
        program = Program.objects.create(code='EDIT', name='Before', department=self.department)
        response = self.client.post(reverse('admin:core_program_change', args=[program.pk]), {
            'code': 'EDIT', 'name': 'After', 'department': self.other.pk, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        program.refresh_from_db()
        self.assertEqual((program.name, program.department_id), ('After', self.other.pk))
        self.assertTrue(AuditLog.objects.filter(action='record.update', actor=self.admin).exists())

    def test_duplicate_code_rejected_within_department_but_allowed_in_another(self):
        Program.objects.create(code='SAME', name='Existing', department=self.department)
        data = {'code': 'SAME', 'name': 'Duplicate', 'department': self.department.pk, '_save': 'Save'}
        response = self.client.post(reverse('admin:core_program_add'), data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['adminform'].form.errors)
        self.assertEqual(Program.objects.count(), 1)
        data['department'] = self.other.pk
        self.assertEqual(self.client.post(reverse('admin:core_program_add'), data).status_code, 302)
        self.assertEqual(Program.objects.count(), 2)

    def test_invalid_department_is_rejected(self):
        response = self.client.post(reverse('admin:core_program_add'), {
            'code': 'INVALID', 'name': 'Example', 'department': 999999, '_save': 'Save'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('department', response.context['adminform'].form.errors)
        self.assertFalse(Program.objects.exists())

    def test_staff_faculty_and_anonymous_cannot_manage_programs(self):
        program = Program.objects.create(code='SAFE', name='Unchanged', department=self.department)
        for user in (self.staff, self.faculty_user, None):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in (reverse('admin:core_program_changelist'), reverse('admin:core_program_add'),
                        reverse('admin:core_program_change', args=[program.pk])):
                with self.subTest(user=user, url=url):
                    self.assertEqual(self.client.get(url).status_code, 302)
                    self.assertEqual(self.client.post(url, {'name': 'Tampered'}).status_code, 302)
            if user:
                self.assertNotContains(self.client.get(reverse('home'), follow=True), reverse('admin:core_program_changelist'))
        program.refresh_from_db()
        self.assertEqual(program.name, 'Unchanged')
        self.assertEqual(Program.objects.count(), 1)

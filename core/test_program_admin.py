"""Program setup reuses the existing system-admin boundary and audit trail."""
from datetime import date

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminProfile
from academics.models import AcademicTerm, AcademicYear, Semester
from audit.models import AuditLog
from faculty.models import Faculty
from timetabling.models import ClassSection
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

    def linked_program(self, *, active=True, historical=False):
        year = 2020 if historical else 2026
        academic_year = AcademicYear.objects.create(label=f'Fictional {year}',
            start_date=date(year, 1, 1), end_date=date(year, 12, 31))
        semester = Semester.objects.create(code='PROG', name='Fictional semester')
        term = AcademicTerm.objects.create(academic_year=academic_year, semester=semester,
            code='PROG', start_date=date(year, 1, 1), end_date=date(year, 5, 31))
        program = Program.objects.create(code='LINKED', name='Fictional linked program', department=self.department)
        section = ClassSection.objects.create(academic_term=term, department=self.department,
            program=program, code='LINKED-1A', is_active=active)
        return program, section

    def change_program(self, program, **changes):
        return self.client.post(reverse('admin:core_program_change', args=[program.pk]), {
            'code': program.code, 'name': program.name, 'department': program.department_id,
            '_save': 'Save', **changes})

    def assert_department_move_rejected(self, program, section):
        before = Program.objects.values().get(pk=program.pk)
        references = ClassSection.objects.values().get(pk=section.pk)
        audit_count, admin_log_count = AuditLog.objects.count(), LogEntry.objects.count()
        response = self.change_program(program, department=self.other.pk, name='Unsaved name', code='UNSAVED')
        self.assertEqual(response.status_code, 200)
        form = response.context['adminform'].form
        self.assertIn('department', form.errors)
        self.assertIn('cannot change departments', form.errors['department'][0])
        self.assertEqual(str(form['department'].value()), str(self.other.pk))
        self.assertEqual(form['name'].value(), 'Unsaved name')
        self.assertEqual(form['code'].value(), 'UNSAVED')
        self.assertEqual(Program.objects.values().get(pk=program.pk), before)
        self.assertEqual(ClassSection.objects.values().get(pk=section.pk), references)
        self.assertEqual((AuditLog.objects.count(), LogEntry.objects.count()), (audit_count, admin_log_count))
        section.refresh_from_db()
        section.full_clean()

    def test_active_linked_section_blocks_department_edit_without_writes_or_success_audit(self):
        self.assert_department_move_rejected(*self.linked_program())

    def test_inactive_linked_section_still_blocks_department_edit(self):
        self.assert_department_move_rejected(*self.linked_program(active=False))

    def test_historical_linked_section_still_blocks_department_edit(self):
        self.assert_department_move_rejected(*self.linked_program(historical=True))

    def test_linked_program_name_code_and_same_department_saves_remain_allowed(self):
        program, section = self.linked_program()
        for changes in ({'name': 'Revised name'}, {'code': 'REVISED'}, {}):
            with self.subTest(changes=changes):
                self.assertEqual(self.change_program(program, **changes).status_code, 302)
                program.refresh_from_db()
                for field, value in changes.items():
                    self.assertEqual(getattr(program, field), value)
                self.assertEqual(program.department_id, section.department_id)
                section.refresh_from_db()
                section.full_clean()
        self.assertEqual(AuditLog.objects.filter(action='record.update', object_type='core.Program',
            object_id=str(program.pk), actor=self.admin).count(), 3)

    def test_linked_program_edit_still_enforces_duplicate_code_and_rolls_back(self):
        program, section = self.linked_program()
        Program.objects.create(code='DUPLICATE', name='Other example', department=self.department)
        response = self.change_program(program, code='DUPLICATE')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['adminform'].form.non_field_errors())
        program.refresh_from_db()
        self.assertEqual(program.code, 'LINKED')
        self.assertFalse(AuditLog.objects.filter(action='record.update', object_type='core.Program').exists())

    def test_staff_and_faculty_cannot_edit_a_linked_program(self):
        program, section = self.linked_program()
        for user in (self.staff, self.faculty_user):
            self.client.force_login(user)
            self.assertEqual(self.change_program(program, department=self.other.pk).status_code, 302)
        program.refresh_from_db()
        self.assertEqual(program.department_id, section.department_id)
        self.assertFalse(AuditLog.objects.filter(action='record.update', object_type='core.Program').exists())

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

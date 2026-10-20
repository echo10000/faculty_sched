"""Instance-write guards and PostgreSQL races for Program/section ownership."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from academics.models import AcademicTerm, AcademicYear, Semester
from audit.models import AuditLog
from timetabling.models import ClassSection
from timetabling.tests import TimetableFixture
from .models import College, Department, Program


class ProgramModelIntegrityTests(TimetableFixture):
    def setUp(self):
        self.program = Program.objects.create(department=self.department, code='USED', name='Fictional program')
        self.section.program = self.program
        self.section.save()

    def test_model_clean_exposes_department_field_error(self):
        self.program.department = self.external
        with self.assertRaises(ValidationError) as error:
            self.program.full_clean()
        self.assertIn('department', error.exception.message_dict)

    def test_direct_save_cannot_move_program_or_partially_save_other_fields(self):
        before = Program.objects.values().get(pk=self.program.pk)
        self.program.department = self.external
        self.program.name = 'Must not persist'
        with self.assertRaises(ValidationError):
            self.program.save()
        self.assertEqual(Program.objects.values().get(pk=self.program.pk), before)
        self.section.refresh_from_db()
        self.section.full_clean()

    def test_partial_department_writes_are_guarded_for_field_name_and_attname(self):
        self.program.department = self.external
        for field in ('department', 'department_id'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.program.save(update_fields=[field])
        self.program.refresh_from_db()
        self.assertEqual(self.program.department_id, self.department.pk)

    def test_update_or_create_uses_the_same_guard(self):
        with self.assertRaises(ValidationError):
            Program.objects.update_or_create(pk=self.program.pk,
                defaults={'department': self.external, 'name': 'Must not persist'})
        self.program.refresh_from_db()
        self.assertEqual((self.program.department_id, self.program.name), (self.department.pk, 'Fictional program'))

    def test_name_and_code_partial_saves_do_not_persist_unsaved_department(self):
        self.program.department = self.external
        self.program.name = 'Updated name'
        self.program.code = 'UPDATED'
        self.program.save(update_fields=iter(['name', 'code']))
        self.program.refresh_from_db()
        self.assertEqual((self.program.department_id, self.program.name, self.program.code),
                         (self.department.pk, 'Updated name', 'UPDATED'))

    def test_unused_program_can_move_through_model_save(self):
        unused = Program.objects.create(department=self.department, code='UNUSED', name='Unused example')
        unused.department = self.external
        unused.save()
        unused.refresh_from_db()
        self.assertEqual(unused.department_id, self.external.pk)

    def test_section_save_reloads_cached_program_after_permitted_move(self):
        unused = Program.objects.create(department=self.department, code='UNUSED', name='Unused example')
        cached = Program.objects.get(pk=unused.pk)
        unused.department = self.external
        unused.save()
        section = ClassSection(academic_term=self.term, department=self.department, program=cached, code='STALE')
        with self.assertRaises(ValidationError) as error:
            section.save()
        self.assertIn('program', error.exception.message_dict)
        self.assertFalse(ClassSection.objects.filter(code='STALE').exists())

    def test_explicit_zero_program_pk_is_also_protected(self):
        program = Program.objects.create(pk=0, department=self.department, code='ZERO', name='Explicit key example')
        ClassSection.objects.create(academic_term=self.term, department=self.department, program=program, code='ZERO')
        program.department = self.external
        with self.assertRaises(ValidationError):
            program.save()
        program.refresh_from_db()
        self.assertEqual(program.department_id, self.department.pk)

    def test_unsaved_related_program_is_not_silently_detached(self):
        program = Program(department=self.department, code='UNSAVED', name='Unsaved example')
        section = ClassSection(academic_term=self.term, department=self.department, program=program, code='UNSAVED')
        with self.assertRaises(ValueError):
            section.save()
        self.assertFalse(ClassSection.objects.filter(code='UNSAVED').exists())
        self.assertFalse(Program.objects.filter(code='UNSAVED').exists())


class ProgramSectionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        college = College.objects.create(code='RACE', name='Fictional race college')
        self.department = Department.objects.create(college=college, code='ONE', name='First')
        self.other = Department.objects.create(college=college, code='TWO', name='Second')
        year = AcademicYear.objects.create(label='Fictional race year', start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        semester = Semester.objects.create(code='RACE', name='Fictional semester')
        self.term = AcademicTerm.objects.create(academic_year=year, semester=semester, code='RACE',
            start_date=date(2026, 1, 1), end_date=date(2026, 5, 31))
        self.program = Program.objects.create(department=self.department, code='RACE', name='Race example')
        self.admin = get_user_model().objects.create_superuser('program-race-admin', '', 'test-only')

    def assert_writer_waits(self, pid_queue):
        writer_pid = pid_queue.get(timeout=10)
        deadline = monotonic() + 10
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            holder_pid = cursor.fetchone()[0]
            while monotonic() < deadline:
                cursor.execute('SELECT pg_blocking_pids(%s)', [writer_pid])
                if holder_pid in cursor.fetchone()[0]:
                    return
                Event().wait(0.02)
        self.fail('The competing supported writer did not wait for the scheduling transaction.')

    def worker_pid(self, queue):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            queue.put(cursor.fetchone()[0])

    def add_section(self):
        return ClassSection.objects.create(academic_term=self.term, department=self.department,
                                           program=self.program, code='RACE-1A')

    def test_model_move_waits_for_new_section_then_rejects(self):
        pids = Queue()
        def move():
            close_old_connections()
            try:
                program = Program.objects.get(pk=self.program.pk)
                program.department = self.other
                program.full_clean()  # The uncommitted link is not visible yet.
                self.worker_pid(pids)
                try:
                    program.save()
                except ValidationError as error:
                    return error.message_dict
                return {}
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                section = self.add_section()
                future = pool.submit(move)
                self.assert_writer_waits(pids)
            self.assertIn('department', future.result(timeout=15))
        self.program.refresh_from_db()
        self.assertEqual(self.program.department_id, section.department_id)
        section.full_clean()

    def test_admin_post_waits_for_new_section_then_returns_field_error_without_audit(self):
        pids = Queue()
        def edit():
            close_old_connections()
            try:
                client = Client()
                client.force_login(get_user_model().objects.get(pk=self.admin.pk))
                self.worker_pid(pids)
                response = client.post(reverse('admin:core_program_change', args=[self.program.pk]), {
                    'department': self.other.pk, 'name': self.program.name, 'code': self.program.code, '_save': 'Save'})
                form = response.context_data['adminform'].form if response.status_code == 200 else None
                return response.status_code, list(form.errors) if form else []
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                section = self.add_section()
                future = pool.submit(edit)
                self.assert_writer_waits(pids)
            status, errors = future.result(timeout=15)
        self.assertEqual(status, 200)
        self.assertIn('department', errors)
        self.assertFalse(AuditLog.objects.filter(action='record.update', object_type='core.Program').exists())
        self.program.refresh_from_db()
        self.assertEqual(self.program.department_id, section.department_id)

    def test_section_save_waits_for_program_move_and_rejects_stale_cached_parent(self):
        cached = Program.objects.get(pk=self.program.pk)
        pids = Queue()
        def attach():
            close_old_connections()
            try:
                section = ClassSection(academic_term=self.term, department=self.department, program=cached, code='STALE')
                self.worker_pid(pids)
                try:
                    section.save()
                except ValidationError as error:
                    return error.message_dict
                return {}
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                self.program.department = self.other
                self.program.save()
                future = pool.submit(attach)
                self.assert_writer_waits(pids)
            self.assertIn('program', future.result(timeout=15))
        self.assertFalse(ClassSection.objects.exists())
        self.program.refresh_from_db()
        self.assertEqual(self.program.department_id, self.other.pk)

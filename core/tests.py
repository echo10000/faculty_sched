from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings

from audit.models import AuditLog
from audit.services import record_event
from .models import College, Department, SystemSetting


class FoundationTests(TestCase):
    def test_college_delete_cannot_cascade_departments(self):
        college = College.objects.create(code="X", name="Example")
        Department.objects.create(college=college, code="D", name="Department")
        with self.assertRaises(ProtectedError):
            college.delete()

    def test_registered_settings_are_validated(self):
        with self.assertRaises(ValidationError):
            SystemSetting.objects.create(key="database_password", value="secret")
        with self.assertRaises(ValidationError):
            SystemSetting.objects.create(key="support_email", value="invalid")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SystemSetting.objects.bulk_create([SystemSetting(key="unknown", value="x")])

    def test_audit_is_append_only_in_orm_and_database(self):
        event = record_event("test.created")
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            AuditLog.objects.all().update(action="tampered")
        with self.assertRaises(ValidationError):
            event.delete()
        for statement in ["UPDATE audit_auditlog SET action='tampered' WHERE id=%s", "DELETE FROM audit_auditlog WHERE id=%s"]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement, [event.pk])

    def test_audit_rolls_back_with_operation(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                record_event("test.rollback")
                raise RuntimeError("Failed operation")
        self.assertFalse(AuditLog.objects.filter(action="test.rollback").exists())

    def test_seed_requires_development_mode(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("seed_foundation", stdout=StringIO())

    def test_seed_is_idempotent_and_does_not_create_faculty_or_schedules(self):
        from faculty.models import Faculty
        from scheduling.models import Assignment
        with TemporaryDirectory() as directory, override_settings(DEBUG=True, BASE_DIR=Path(directory)):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("seed_foundation", create_users=True, stdout=StringIO())
            user = get_user_model().objects.get(username="dev.admin")
            initial_hash = user.password
            first_count = College.objects.count()
            with self.captureOnCommitCallbacks(execute=True):
                call_command("seed_foundation", create_users=True, stdout=StringIO())
            user.refresh_from_db()
            self.assertEqual(user.password, initial_hash)
            self.assertEqual(College.objects.count(), first_count)
            self.assertEqual(get_user_model().objects.count(), 4)
            self.assertEqual(Faculty.objects.count(), 0)
            self.assertEqual(Assignment.objects.count(), 0)
            self.assertTrue((Path(directory) / ".local/development-credentials.txt").exists())

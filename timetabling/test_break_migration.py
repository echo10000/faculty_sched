from datetime import date, time

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class BreakConfigurationMigrationTests(TransactionTestCase):
    def test_existing_configuration_is_preserved_with_disabled_safe_defaults(self):
        executor = MigrationExecutor(connection)
        old = [("timetabling", "0008_alter_schedule_options_and_more")]
        current = executor.loader.graph.leaf_nodes()
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            college = apps.get_model("core", "College").objects.create(code="REST-MIG", name="Rest migration college")
            department = apps.get_model("core", "Department").objects.create(code="REST-MIG", name="Rest migration department", college=college)
            year = apps.get_model("academics", "AcademicYear").objects.create(label="Rest migration year",
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
            semester = apps.get_model("academics", "Semester").objects.create(code="REST-MIG", name="Rest migration semester")
            term = apps.get_model("academics", "AcademicTerm").objects.create(code="REST-MIG", academic_year=year,
                semester=semester, start_date=date(2026, 1, 1), end_date=date(2026, 5, 31))
            saved = apps.get_model("timetabling", "SchedulingConfiguration").objects.create(
                department=department, academic_term=term, allowed_weekdays=[1, 3], earliest_start=time(8),
                latest_end=time(17), slot_increment_minutes=30, solver_time_limit_seconds=10,
                random_seed=37, worker_count=1, faculty_gap_weight=7)
            executor = MigrationExecutor(connection)
            executor.migrate(current)
            updated = executor.loader.project_state(current).apps.get_model("timetabling", "SchedulingConfiguration").objects.get(pk=saved.pk)
            self.assertFalse(updated.lunch_break_enabled)
            self.assertIsNone(updated.lunch_start)
            self.assertIsNone(updated.lunch_end)
            self.assertEqual(updated.faculty_rest_minutes, 0)
            self.assertEqual(updated.faculty_gap_weight, 7)
            self.assertEqual(updated.allowed_weekdays, [1, 3])
            self.assertEqual(updated.updated_at, saved.updated_at)
        finally:
            MigrationExecutor(connection).migrate(current)

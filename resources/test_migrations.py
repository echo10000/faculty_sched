from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MasterDataUpgradeTests(TransactionTestCase):
    def test_phase1_rows_keep_ids_values_and_receive_reference_backfills(self):
        executor = MigrationExecutor(connection)
        current = executor.loader.graph.leaf_nodes()
        old = [("faculty", "0001_initial"), ("academics", "0005_term_date_integrity"),
               ("scheduling", "0005_assignmentstatuslog_old_new_status"),
               ("core", "0002_alter_college_options_college_created_by_and_more"),
               ("auth", "0012_alter_user_first_name_max_length")]
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            College = apps.get_model("core", "College")
            Department = apps.get_model("core", "Department")
            Faculty = apps.get_model("faculty", "Faculty")
            Subject = apps.get_model("academics", "Subject")
            Room = apps.get_model("scheduling", "Room")
            college = College.objects.create(code="UP", name="Upgrade college")
            department = Department.objects.create(code="UP-D", name="Upgrade department", college=college)
            faculty = Faculty.objects.create(employee_id="UP-F", first_name="Original", last_name="Name", home_department=department, employment_type="full_time", base_load_units=Decimal("20"))
            subject = Subject.objects.create(code="UP-S", title="Original subject", units=Decimal("3.5"), owning_department=department)
            room = Room.objects.create(name="Original room", room_type="laboratory", capacity=35, restricted_to_department=department)
            MigrationExecutor(connection).migrate(current)
            from faculty.models import Faculty as CurrentFaculty
            from academics.models import Subject as CurrentSubject
            from scheduling.models import Room as CurrentRoom
            upgraded = CurrentFaculty.objects.get(pk=faculty.pk)
            self.assertEqual(upgraded.first_name, "Original")
            self.assertEqual(upgraded.base_load_units, 20)
            self.assertEqual(upgraded.employment_category.code, "full_time")
            self.assertIsNone(upgraded.maximum_load)
            upgraded_subject = CurrentSubject.objects.get(pk=subject.pk)
            self.assertEqual(upgraded_subject.units, Decimal("3.5"))
            self.assertIsNone(upgraded_subject.lecture_units)
            upgraded_room = CurrentRoom.objects.get(pk=room.pk)
            self.assertEqual(upgraded_room.name, "Original room")
            self.assertEqual(upgraded_room.code, f"LEGACY-{room.pk}")
            self.assertEqual(upgraded_room.owner_department_id, department.pk)
            self.assertEqual(upgraded_room.restricted_to_department_id, department.pk)
            self.assertEqual(upgraded_room.category.code, "laboratory")
        finally:
            MigrationExecutor(connection).migrate(current)

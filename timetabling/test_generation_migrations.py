from datetime import date, time
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ScheduleEntryUpgradeTests(TransactionTestCase):
    def test_existing_entries_become_locked_without_generation_provenance(self):
        executor = MigrationExecutor(connection)
        old_target = [("timetabling", "0001_initial")]
        current = executor.loader.graph.leaf_nodes()
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            College = apps.get_model("core", "College")
            Department = apps.get_model("core", "Department")
            AcademicYear = apps.get_model("academics", "AcademicYear")
            Semester = apps.get_model("academics", "Semester")
            AcademicTerm = apps.get_model("academics", "AcademicTerm")
            Subject = apps.get_model("academics", "Subject")
            Faculty = apps.get_model("faculty", "Faculty")
            Room = apps.get_model("scheduling", "Room")
            SubjectOffering = apps.get_model("workloads", "SubjectOffering")
            Assignment = apps.get_model("workloads", "FacultySubjectAssignment")
            Schedule = apps.get_model("timetabling", "Schedule")
            ScheduleEntry = apps.get_model("timetabling", "ScheduleEntry")

            college = College.objects.create(code="MIG", name="Migration College")
            department = Department.objects.create(
                college=college,
                code="MIG",
                name="Migration Department",
            )
            year = AcademicYear.objects.create(
                label="Migration 2026",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            semester = Semester.objects.create(code="MIG", name="Migration semester")
            term = AcademicTerm.objects.create(
                academic_year=year,
                semester=semester,
                code="MIG",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 5, 31),
            )
            subject = Subject.objects.create(
                code="MIG",
                title="Migration subject",
                units=Decimal("2.00"),
                lecture_units=Decimal("2.00"),
                laboratory_units=Decimal("0.00"),
                lecture_hours=Decimal("2.00"),
                laboratory_hours=Decimal("0.00"),
                owning_department=department,
            )
            faculty = Faculty.objects.create(
                employee_id="MIG",
                first_name="Migration",
                last_name="Faculty",
                home_department=department,
            )
            room = Room.objects.create(
                code="MIG",
                name="Migration Room",
                capacity=30,
                owner_department=department,
            )
            offering = SubjectOffering.objects.create(
                subject=subject,
                academic_term=term,
                department=department,
                lecture_units=Decimal("2.00"),
                laboratory_units=Decimal("0.00"),
                lecture_hours=Decimal("2.00"),
                laboratory_hours=Decimal("0.00"),
            )
            assignment = Assignment.objects.create(
                faculty=faculty,
                subject_offering=offering,
            )
            schedule = Schedule.objects.create(
                academic_term=term,
                department=department,
                name="Migration schedule",
            )
            entry = ScheduleEntry.objects.create(
                schedule=schedule,
                assignment=assignment,
                room=room,
                day_of_week=1,
                start_time=time(9),
                end_time=time(10),
                meeting_type="lecture",
            )

            new_target = [("timetabling", "0002_phase5_generation")]
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            upgraded = new_apps.get_model("timetabling", "ScheduleEntry").objects.get(
                pk=entry.pk
            )
            self.assertIs(upgraded.is_locked, True)
            self.assertIsNone(upgraded.generation_run_id)
        finally:
            MigrationExecutor(connection).migrate(current)


class SchedulingDependencyTriggerTests(TransactionTestCase):
    expected_tables = {
        "academics_academicyear",
        "academics_academicterm",
        "academics_semester",
        "academics_subject",
        "core_college",
        "core_department",
        "faculty_faculty",
        "faculty_facultyqualification",
        "resources_roomtype",
        "resources_building",
        "scheduling_room",
        "workloads_facultyavailability",
        "workloads_facultytermcapacity",
        "workloads_workloadpolicy",
        "workloads_subjectoffering",
        "workloads_facultysubjectassignment",
        "timetabling_classsection",
        "timetabling_offeringrequirement",
        "timetabling_roomunavailability",
        "timetabling_schedule",
        "timetabling_activeschedule",
        "timetabling_scheduleentry",
        "timetabling_schedulingconfiguration",
        "timetabling_assignmentmeetingrequirement",
    }

    def test_every_feasibility_writer_acquires_the_statement_lock(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_class.relname, trigger.tgtype
                FROM pg_trigger AS trigger
                JOIN pg_class AS table_class ON table_class.oid = trigger.tgrelid
                JOIN pg_proc AS function ON function.oid = trigger.tgfoid
                WHERE NOT trigger.tgisinternal
                  AND function.proname = 'timetabling_acquire_scheduling_lock'
                """
            )
            trigger_rows = cursor.fetchall()
            triggers = dict(trigger_rows)

        self.assertEqual(len(trigger_rows), len(self.expected_tables))
        self.assertEqual(set(triggers), self.expected_tables)
        for table_name, trigger_type in triggers.items():
            with self.subTest(table=table_name):
                self.assertEqual(trigger_type & 1, 0, "trigger must be statement-level")
                self.assertTrue(trigger_type & 2, "trigger must run before the statement")
                self.assertTrue(trigger_type & 4, "trigger must cover INSERT")
                self.assertTrue(trigger_type & 8, "trigger must cover DELETE")
                self.assertTrue(trigger_type & 16, "trigger must cover UPDATE")

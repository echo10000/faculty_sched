"""Phase 6 data survives the additive review/versioning upgrade unchanged."""

from datetime import date, time
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class Phase7UpgradeTests(TransactionTestCase):
    def test_existing_draft_and_validated_schedules_remain_unofficial(self):
        old_target = [("timetabling", "0003_scheduling_dependency_lock_triggers")]
        executor = MigrationExecutor(connection)
        current = executor.loader.graph.leaf_nodes()
        executor.migrate(old_target)
        try:
            old_apps = executor.loader.project_state(old_target).apps
            College = old_apps.get_model("core", "College")
            Department = old_apps.get_model("core", "Department")
            AcademicYear = old_apps.get_model("academics", "AcademicYear")
            Semester = old_apps.get_model("academics", "Semester")
            AcademicTerm = old_apps.get_model("academics", "AcademicTerm")
            Subject = old_apps.get_model("academics", "Subject")
            Faculty = old_apps.get_model("faculty", "Faculty")
            Room = old_apps.get_model("scheduling", "Room")
            Offering = old_apps.get_model("workloads", "SubjectOffering")
            Assignment = old_apps.get_model("workloads", "FacultySubjectAssignment")
            Schedule = old_apps.get_model("timetabling", "Schedule")
            ScheduleEntry = old_apps.get_model("timetabling", "ScheduleEntry")

            college = College.objects.create(code="P7M", name="Phase 7 migration college")
            department = Department.objects.create(college=college, code="P7M", name="Phase 7 migration department")
            year = AcademicYear.objects.create(label="Phase 7 migration year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
            semester = Semester.objects.create(code="P7M", name="Phase 7 migration semester")
            term = AcademicTerm.objects.create(academic_year=year, semester=semester, code="P7M", start_date=date(2026, 1, 1), end_date=date(2026, 5, 31))
            subject = Subject.objects.create(code="P7M", title="Phase 7 migration subject", owning_department=department, units=Decimal("1"), lecture_units=Decimal("1"), laboratory_units=Decimal("0"), lecture_hours=Decimal("1"), laboratory_hours=Decimal("0"))
            faculty = Faculty.objects.create(employee_id="P7M", first_name="Migration", last_name="Faculty", home_department=department)
            room = Room.objects.create(code="P7M", name="Migration room", capacity=30, owner_department=department)
            offering = Offering.objects.create(subject=subject, academic_term=term, department=department, lecture_units=1, laboratory_units=0, lecture_hours=1, laboratory_hours=0)
            assignment = Assignment.objects.create(faculty=faculty, subject_offering=offering)
            draft = Schedule.objects.create(name="Existing draft", academic_term=term, department=department, status="draft")
            validated = Schedule.objects.create(name="Existing validated", academic_term=term, department=department, status="validated", validated_signature="old-validation")
            first = ScheduleEntry.objects.create(schedule=draft, assignment=assignment, room=room, day_of_week=1, start_time=time(9), end_time=time(10), meeting_type="lecture", is_locked=True)
            second = ScheduleEntry.objects.create(schedule=validated, assignment=assignment, room=room, day_of_week=2, start_time=time(9), end_time=time(10), meeting_type="lecture", is_locked=True)

            MigrationExecutor(connection).migrate(current)
            new_apps = MigrationExecutor(connection).loader.project_state(current).apps
            NewSchedule = new_apps.get_model("timetabling", "Schedule")
            NewEntry = new_apps.get_model("timetabling", "ScheduleEntry")
            Family = new_apps.get_model("timetabling", "ScheduleFamily")
            upgraded = list(NewSchedule.objects.filter(pk__in=[draft.pk, validated.pk]).order_by("pk"))
            self.assertEqual([row.pk for row in upgraded], [draft.pk, validated.pk])
            self.assertEqual([row.status for row in upgraded], ["draft", "validated"])
            self.assertEqual(upgraded[1].validated_signature, "old-validation")
            self.assertEqual([row.version_number for row in upgraded], [1, 1])
            self.assertEqual([row.name for row in Family.objects.filter(pk__in=[row.family_id for row in upgraded]).order_by("pk")], ["Existing draft", "Existing validated"])
            self.assertNotEqual(upgraded[0].family_id, upgraded[1].family_id)
            self.assertTrue(all(len(row.revision_token) == 32 for row in upgraded))
            self.assertNotEqual(upgraded[0].revision_token, upgraded[1].revision_token)
            self.assertEqual(set(NewEntry.objects.filter(pk__in=[first.pk, second.pk]).values_list("pk", flat=True)), {first.pk, second.pk})
            self.assertEqual(new_apps.get_model("timetabling", "ScheduleWorkflowEvent").objects.count(), 0)
            self.assertEqual(new_apps.get_model("timetabling", "ScheduleApprovalSnapshot").objects.count(), 0)
            self.assertEqual(new_apps.get_model("timetabling", "ActiveSchedule").objects.count(), 0)
            self.assertEqual(new_apps.get_model("timetabling", "OfficialResourceBooking").objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate(current)

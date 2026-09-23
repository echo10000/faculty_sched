from datetime import time
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.db import connection

from faculty.models import FacultyQualification
from timetabling.models import AssignmentMeetingRequirement, ScheduleEntry
from timetabling.tests import TimetableFixture

from .balancing_signature import compute_balancing_source_signature
from .models import (
    FacultySubjectAssignment,
    FacultyTermCapacity,
    SubjectOffering,
    WorkloadPolicy,
    WorkloadRecommendationRun,
)


class BalancingFoundationTests(TimetableFixture):
    def signature(self):
        return compute_balancing_source_signature(
            academic_term=self.term, department=self.department,
        )

    def test_run_defaults_and_role_grants(self):
        run = WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.department,
            initiated_by=self.chair,
        )
        self.assertEqual(run.status, WorkloadRecommendationRun.Status.PENDING)
        self.assertEqual(run.raw_solver_status, "")
        self.assertEqual(run.proposed_assignments, [])
        self.assertEqual(run.input_summary, {})
        self.assertEqual(run.comparison, {})
        self.assertEqual(run.diagnostics, [])
        self.assertEqual(run.solver_stats, {})
        self.assertIsNotNone(run.created_at)
        self.assertIsNotNone(run.updated_at)
        self.assertIsNone(run.accepted_by)
        self.assertIsNone(run.accepted_at)
        self.assertIsNone(run.discarded_at)
        self.assertTrue(self.admin.has_perm("workloads.generate_workloadrecommendation"))
        self.assertTrue(self.dean.has_perm("workloads.generate_workloadrecommendation"))
        self.assertTrue(self.chair.has_perm("workloads.generate_workloadrecommendation"))
        self.assertFalse(self.staff.has_perm("workloads.generate_workloadrecommendation"))
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="workloads",
            codename="generate_workloadrecommendation",
        ))
        self.assertTrue(type(self.staff).objects.get(pk=self.staff.pk).has_perm(
            "workloads.generate_workloadrecommendation",
        ))

    def test_signature_is_stable_scoped_and_tracks_assignment_capacity_policy(self):
        initial = self.signature()
        self.assertEqual(initial, self.signature())
        self.assertEqual(len(initial), 64)
        sibling_offering = self.offerings[self.sibling.pk]
        SubjectOffering.objects.filter(pk=sibling_offering.pk).update(lecture_units=9)
        self.assertEqual(self.signature(), initial)

        FacultySubjectAssignment.objects.filter(pk=self.assignment.pk).update(share=Decimal("0.50"))
        changed = self.signature()
        self.assertNotEqual(changed, initial)

        FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term, maximum_load=7)
        changed_capacity = self.signature()
        self.assertNotEqual(changed_capacity, changed)

        WorkloadPolicy.objects.create(academic_term=self.term, maximum_load=9)
        self.assertNotEqual(self.signature(), changed_capacity)

    def test_signature_tracks_qualification_and_protected_timetable_rows(self):
        initial = self.signature()
        FacultyQualification.objects.create(faculty=self.faculty, subject=self.offering.subject)
        qualified = self.signature()
        self.assertNotEqual(qualified, initial)
        requirement = AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment, meeting_type="lecture",
            meetings_per_week=2, duration_minutes=60,
        )
        required = self.signature()
        self.assertNotEqual(required, qualified)
        entry = ScheduleEntry.objects.create(
            schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=1, start_time=time(9), end_time=time(10),
            meeting_type="lecture", is_locked=True,
        )
        self.assertNotEqual(self.signature(), required)
        ScheduleEntry.objects.filter(pk=entry.pk).update(is_locked=False)
        self.assertNotEqual(self.signature(), required)
        AssignmentMeetingRequirement.objects.filter(pk=requirement.pk).update(duration_minutes=90)
        self.assertNotEqual(self.signature(), required)

    def test_signature_tracks_active_flags(self):
        initial = self.signature()
        type(self.faculty).objects.filter(pk=self.faculty.pk).update(is_active=False)
        self.assertNotEqual(self.signature(), initial)
        type(self.faculty).objects.filter(pk=self.faculty.pk).update(is_active=True)
        self.assertEqual(self.signature(), initial)
        type(self.department).objects.filter(pk=self.department.pk).update(is_active=False)
        self.assertNotEqual(self.signature(), initial)

    def test_signature_tracks_scoped_offering_and_applicable_policy_only(self):
        initial = self.signature()
        WorkloadPolicy.objects.create(
            academic_term=self.term, department=self.sibling,
            maximum_load=3,
        )
        self.assertEqual(self.signature(), initial)
        WorkloadPolicy.objects.create(
            academic_term=self.term, college=self.department.college,
            maximum_load=8,
        )
        policy_signature = self.signature()
        self.assertNotEqual(policy_signature, initial)
        SubjectOffering.objects.filter(pk=self.offering.pk).update(is_active=False)
        self.assertNotEqual(self.signature(), policy_signature)

    def test_dependency_tables_share_phase_five_advisory_trigger(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT c.relname FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                WHERE t.tgname = 'timetabling_scheduling_dependency_lock'
                  AND NOT t.tgisinternal
            """)
            tables = {row[0] for row in cursor.fetchall()}
        self.assertTrue({
            "workloads_workloadpolicy", "workloads_facultytermcapacity",
            "faculty_facultyqualification",
        }.issubset(tables))

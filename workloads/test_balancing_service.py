"""Integration tests for the reviewed workload-balancing lifecycle."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from audit.models import AuditLog
from faculty.models import Faculty
from timetabling.models import AssignmentMeetingRequirement

from .balancing_service import (
    BalancingError, accept_balancing_run, discard_balancing_run,
    request_balancing_run, scoped_balancing_run,
)
from .models import FacultySubjectAssignment, FacultyTermCapacity, SubjectOffering, WorkloadPolicy, WorkloadRecommendationRun
from .tests import TeachingFixture


class BalancingLifecycleTests(TeachingFixture):
    def setUp(self):
        self.second = Faculty.objects.create(
            employee_id="F-BALANCE", first_name="Second", last_name="Professor",
            home_department=self.department, employment_category=self.category,
        )
        WorkloadPolicy.objects.create(
            academic_term=self.term, department=self.department,
            recommended_load=Decimal("3"), maximum_load=Decimal("4"),
            lecture_weight=1, laboratory_weight=1, enforce_maximum=True,
        )
        self.additional = SubjectOffering.objects.create(
            subject=self.offering.subject, academic_term=self.term,
            department=self.department, code="BALANCE", lecture_units=3,
            laboratory_units=0, lecture_hours=3, laboratory_hours=0,
        )
        self.first_assignment = FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=self.offering,
        )
        self.second_assignment = FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=self.additional,
        )

    def request(self, user=None):
        return request_balancing_run(
            user=user or self.chair, academic_term=self.term, department=self.department,
        )

    def test_request_is_proposal_only_and_acceptance_balances_atomically(self):
        before = list(FacultySubjectAssignment.objects.order_by("pk").values_list(
            "faculty_id", "subject_offering_id", "share",
        ))
        run = self.request()
        self.assertEqual(run.status, WorkloadRecommendationRun.Status.PROPOSAL_READY)
        self.assertIn(run.raw_solver_status, ("OPTIMAL", "FEASIBLE"))
        self.assertEqual(before, list(FacultySubjectAssignment.objects.order_by("pk").values_list(
            "faculty_id", "subject_offering_id", "share",
        )))
        self.assertEqual(sum(row["status"] == "REASSIGN" for row in run.proposed_assignments), 1)
        self.assertEqual(run.comparison["summary"]["assignment_changes"], 1)
        self.assertLessEqual(Decimal(run.comparison["summary"]["imbalance_after"]),
                             Decimal(run.comparison["summary"]["imbalance_before"]))
        accepted = accept_balancing_run(user=self.chair, run=run)
        self.assertEqual(accepted.status, WorkloadRecommendationRun.Status.ACCEPTED)
        self.assertEqual(FacultySubjectAssignment.objects.filter(faculty=self.second).count(), 1)
        self.assertEqual(FacultySubjectAssignment.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(action="balancing.accepted", object_id=str(run.pk)).exists())
        self.assertTrue(AuditLog.objects.filter(action="facultysubjectassignment.created", details__balancing_run_id=run.pk).exists())
        with self.assertRaises(BalancingError):
            accept_balancing_run(user=self.chair, run=run)

    def test_stale_change_blocks_all_assignment_writes(self):
        run = self.request()
        WorkloadPolicy.objects.filter(department=self.department, academic_term=self.term).update(recommended_load=2)
        before = list(FacultySubjectAssignment.objects.values_list("pk", "faculty_id", "share"))
        stale = accept_balancing_run(user=self.chair, run=run)
        self.assertEqual(stale.status, WorkloadRecommendationRun.Status.STALE)
        self.assertEqual(before, list(FacultySubjectAssignment.objects.values_list("pk", "faculty_id", "share")))

    def test_assignment_change_makes_proposal_stale(self):
        run = self.request()
        FacultySubjectAssignment.objects.filter(pk=self.second_assignment.pk).update(share=Decimal("0.50"))
        outcome = accept_balancing_run(user=self.chair, run=run)
        self.assertEqual(outcome.status, "STALE")
        self.assertEqual(FacultySubjectAssignment.objects.get(pk=self.second_assignment.pk).share, Decimal("0.50"))

    def test_capacity_override_change_makes_proposal_stale(self):
        run = self.request()
        FacultyTermCapacity.objects.create(
            faculty=self.second, academic_term=self.term,
            recommended_load=Decimal("2"), maximum_load=Decimal("3"),
        )
        self.assertEqual(accept_balancing_run(user=self.chair, run=run).status, "STALE")
        self.assertEqual(FacultySubjectAssignment.objects.filter(faculty=self.second).count(), 0)

    def test_discards_and_rejects_second_terminal_action(self):
        run = self.request()
        self.assertEqual(discard_balancing_run(user=self.chair, run=run).status, "DISCARDED")
        with self.assertRaises(BalancingError):
            accept_balancing_run(user=self.chair, run=run)
        self.assertEqual(FacultySubjectAssignment.objects.filter(faculty=self.second).count(), 0)

    def test_protected_timetable_assignment_stays_fixed(self):
        AssignmentMeetingRequirement.objects.create(
            assignment=self.first_assignment, meeting_type="lecture",
            meetings_per_week=2, duration_minutes=60,
        )
        run = self.request()
        self.assertEqual(run.status, "PROPOSAL_READY")
        protected = next(row for row in run.proposed_assignments if row["offering_id"] == self.offering.pk)
        self.assertEqual(protected["status"], "KEEP")
        accept_balancing_run(user=self.chair, run=run)
        self.assertTrue(FacultySubjectAssignment.objects.filter(pk=self.first_assignment.pk).exists())

    def test_protected_incomplete_offering_is_readiness_error(self):
        FacultySubjectAssignment.objects.filter(pk=self.first_assignment.pk).update(share=Decimal("0.50"))
        AssignmentMeetingRequirement.objects.create(
            assignment=self.first_assignment, meeting_type="lecture",
            meetings_per_week=2, duration_minutes=60,
        )
        run = self.request()
        self.assertEqual(run.status, "INPUT_INVALID")
        self.assertEqual(run.proposed_assignments, [])
        self.assertIn("INCOMPLETE_PROTECTED_OFFERING", [row["code"] for row in run.diagnostics])

    def test_permissions_scope_and_own_run(self):
        with self.assertRaises(PermissionDenied):
            self.request(self.staff)
        other = request_balancing_run(user=self.dean, academic_term=self.term, department=self.sibling)
        with self.assertRaises(Exception) as error:
            scoped_balancing_run(self.chair, other.pk)
        self.assertEqual(type(error.exception).__name__, "Http404")
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="workloads", codename="generate_workloadrecommendation",
        ))
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="academics", codename="view_academicterm",
        ))
        staff = type(self.staff).objects.get(pk=self.staff.pk)
        own = self.request(staff)
        self.assertEqual(scoped_balancing_run(staff, own.pk).pk, own.pk)
        with self.assertRaises(Exception) as error:
            scoped_balancing_run(staff, other.pk)
        self.assertEqual(type(error.exception).__name__, "Http404")

    def test_modified_proposal_is_rejected_without_partial_assignment_change(self):
        run = self.request()
        changed = list(run.proposed_assignments)
        changed[0] = {**changed[0], "reason": "tampered"}
        WorkloadRecommendationRun.objects.filter(pk=run.pk).update(proposed_assignments=changed)
        before = list(FacultySubjectAssignment.objects.values_list("pk", "faculty_id", "share"))
        outcome = accept_balancing_run(user=self.chair, run=run)
        self.assertEqual(outcome.status, "VALIDATION_FAILED")
        self.assertEqual(before, list(FacultySubjectAssignment.objects.values_list("pk", "faculty_id", "share")))

    def test_audit_failure_rolls_back_acceptance(self):
        run = self.request()
        with patch("workloads.balancing_service.record_event", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                accept_balancing_run(user=self.chair, run=run)
        run.refresh_from_db()
        self.assertEqual(run.status, "PROPOSAL_READY")
        self.assertEqual(FacultySubjectAssignment.objects.filter(faculty=self.second).count(), 0)

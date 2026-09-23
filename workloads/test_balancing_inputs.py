from decimal import Decimal

from faculty.models import Faculty, FacultyQualification
from timetabling.models import AssignmentMeetingRequirement
from workloads.models import FacultySubjectAssignment, WorkloadPolicy
from workloads.tests import TeachingFixture

from .balancing_inputs import LOAD_SCALE, prepare_balancing_input


class BalancingInputTests(TeachingFixture):
    def setUp(self):
        self.peer = Faculty.objects.create(
            employee_id="BALANCE-PEER", first_name="Peer", last_name="Teacher",
            home_department=self.department, recommended_load=Decimal("4"),
            maximum_load=Decimal("6"),
        )
        self.policy(recommended_load=Decimal("4"), maximum_load=Decimal("6"))

    def prepare(self):
        return prepare_balancing_input(academic_term=self.term, department=self.department)

    def test_options_use_authoritative_weighted_load_and_complete_shares(self):
        assignment = FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=self.offering, share=Decimal("1"),
        )
        prepared = self.prepare()
        self.assertEqual(prepared.diagnostics, ())
        self.assertEqual(prepared.mutable_offering_ids, (self.offering.pk,))
        self.assertEqual(prepared.current_assignments[0]["assignment_id"], assignment.pk)
        demand, = prepared.solver_input.offerings
        self.assertEqual(len(demand.options), 2)
        self.assertTrue(all(sum(share.share_centis for share in option.shares) == 100 for option in demand.options))
        keep = next(option for option in demand.options if not option.changed)
        self.assertEqual(keep.shares[0].contribution_scaled, int(Decimal("3.50") * LOAD_SCALE))
        self.assertEqual({load.baseline_load_scaled for load in prepared.solver_input.faculty}, {0})

    def test_active_department_scope_and_unknown_qualification(self):
        prepared = self.prepare()
        demand, = prepared.solver_input.offerings
        self.assertEqual({option.shares[0].faculty_id for option in demand.options}, {self.faculty.pk, self.peer.pk})
        self.assertEqual({option.qualification_match_count for option in demand.options}, {0})
        FacultyQualification.objects.create(faculty=self.peer, subject=self.offering.subject)
        demand, = self.prepare().solver_input.offerings
        preferred = next(option for option in demand.options if option.shares[0].faculty_id == self.peer.pk)
        self.assertEqual(preferred.qualification_match_count, 1)
        Faculty.objects.filter(pk=self.peer.pk).update(is_active=False)
        demand, = self.prepare().solver_input.offerings
        self.assertEqual({option.shares[0].faculty_id for option in demand.options}, {self.faculty.pk})

    def test_protected_assignment_is_fixed_and_must_be_complete(self):
        assignment = FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=self.offering, share=Decimal("1"),
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=assignment, meeting_type="lecture", meetings_per_week=2, duration_minutes=60,
        )
        prepared = self.prepare()
        self.assertEqual(prepared.fixed_offering_ids, (self.offering.pk,))
        demand, = prepared.solver_input.offerings
        self.assertEqual(len(demand.options), 1)
        self.assertFalse(demand.options[0].changed)
        FacultySubjectAssignment.objects.filter(pk=assignment.pk).update(share=Decimal("0.50"))
        prepared = self.prepare()
        self.assertIsNone(prepared.solver_input)
        self.assertIn("INCOMPLETE_PROTECTED_OFFERING", {issue["code"] for issue in prepared.diagnostics})

    def test_partial_existing_shares_fail_readiness(self):
        FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=self.offering, share=Decimal("0.50"),
        )
        prepared = self.prepare()
        self.assertIsNone(prepared.solver_input)
        self.assertIn("INCOMPLETE_SHARES", {item["code"] for item in prepared.diagnostics})

    def test_unconfigured_target_and_component_weight_report_readiness(self):
        WorkloadPolicy.objects.filter(department=self.department).update(
            recommended_load=None, maximum_load=None, laboratory_weight=None,
        )
        prepared = self.prepare()
        self.assertIsNone(prepared.solver_input)
        self.assertTrue({"MISSING_TARGET", "MISSING_WEIGHT"}.issubset({issue["code"] for issue in prepared.diagnostics}))

    def test_inactive_or_cross_scope_assigned_faculty_is_rejected(self):
        assignment = FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=self.offering,
        )
        Faculty.objects.filter(pk=self.faculty.pk).update(is_active=False)
        self.assertIn("INVALID_ASSIGNMENT_FACULTY", {item["code"] for item in self.prepare().diagnostics})
        Faculty.objects.filter(pk=self.faculty.pk).update(is_active=True)
        FacultySubjectAssignment.objects.filter(pk=assignment.pk).update(faculty=self.records[self.external.pk]["faculty-management"])
        self.assertIn("INVALID_ASSIGNMENT_FACULTY", {item["code"] for item in self.prepare().diagnostics})

    def test_faculty_assignment_into_other_department_is_rejected(self):
        foreign = FacultySubjectAssignment.objects.create(
            faculty=self.records[self.external.pk]["faculty-management"],
            subject_offering=self.offerings[self.external.pk],
        )
        FacultySubjectAssignment.objects.filter(pk=foreign.pk).update(faculty=self.peer)
        prepared = self.prepare()
        self.assertIsNone(prepared.solver_input)
        self.assertIn("CROSS_SCOPE_ASSIGNMENT", {item["code"] for item in prepared.diagnostics})

    def test_no_active_faculty_or_offerings_report_actionable_errors(self):
        Faculty.objects.filter(home_department=self.department).update(is_active=False)
        self.assertIn("NO_ACTIVE_FACULTY", {item["code"] for item in self.prepare().diagnostics})
        Faculty.objects.filter(home_department=self.department).update(is_active=True)
        self.offering.is_active = False
        self.offering.save()
        self.assertIn("NO_ACTIVE_OFFERINGS", {item["code"] for item in self.prepare().diagnostics})

    def test_hard_maximum_is_passed_to_solver_only_when_enforced(self):
        load = self.prepare().solver_input.faculty[0]
        self.assertIsNone(load.hard_max_scaled)
        WorkloadPolicy.objects.filter(department=self.department).update(enforce_maximum=True)
        load = self.prepare().solver_input.faculty[0]
        self.assertEqual(load.hard_max_scaled, 6 * LOAD_SCALE)

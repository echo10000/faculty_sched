"""Transactional generation lifecycle and stored-proposal regression tests."""

from dataclasses import replace
from datetime import time
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase

from audit.models import AuditLog
from workloads.models import FacultyAvailability

from .generation import (
    InvalidRunTransition, accept_generation, discard_generation,
    finish_generation_if_expected, request_generation,
)
from .generation_inputs import GENERATION_PERMISSIONS, GenerationOverrides
from .generation_validation import validate_generation_contract
from .models import (
    AssignmentMeetingRequirement, ScheduleEntry, ScheduleGenerationRun,
    SchedulingConfiguration,
)
from .solver.contracts import ProposedMeeting, SolverResult, SolverStatistics
from .tests import TimetableFixture


class GenerationServiceTests(TimetableFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.configuration = SchedulingConfiguration.objects.create(
            academic_term=cls.term, department=cls.department,
            allowed_weekdays=[1, 2, 3, 4, 5], earliest_start=time(8),
            latest_end=time(17), slot_increment_minutes=30,
            solver_time_limit_seconds=10, random_seed=17, worker_count=1,
            faculty_preference_weight=0, faculty_gap_weight=0,
            section_gap_weight=0, meeting_distribution_weight=0,
            room_fit_weight=0,
        )
        cls.lecture_requirement = AssignmentMeetingRequirement.objects.create(
            assignment=cls.assignment, meeting_type="lecture",
            meetings_per_week=2, duration_minutes=60,
        )
        cls.laboratory_requirement = AssignmentMeetingRequirement.objects.create(
            assignment=cls.assignment, meeting_type="laboratory",
            meetings_per_week=1, duration_minutes=180,
        )

    def generate(self, *, strategy="FILL_GAPS", overrides=None):
        return request_generation(
            user=self.chair, schedule_id=self.schedule.pk, strategy=strategy,
            overrides=overrides or GenerationOverrides(),
        )

    def ready(self, *, strategy="FILL_GAPS"):
        run = self.generate(strategy=strategy)
        self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
        return run

    def exact_bundle_staff(self):
        permissions = [Permission.objects.get(
            content_type__app_label=code.split(".")[0],
            codename=code.split(".")[1],
        ) for code in GENERATION_PERMISSIONS]
        self.staff.user_permissions.add(*permissions)
        return type(self.staff).objects.get(pk=self.staff.pk)

    def test_staff_can_accept_own_proposal_with_default_history_access(self):
        staff = self.exact_bundle_staff()
        self.assertTrue(staff.has_perm("timetabling.view_schedulegenerationrun"))
        run = request_generation(
            user=staff, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
        self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
        accepted = accept_generation(user=staff, run_id=run.pk)
        self.assertEqual(accepted.status, "ACCEPTED")
        self.assertEqual(ScheduleEntry.objects.filter(generation_run=run).count(),
                         run.proposed_meeting_count)

    def test_staff_can_discard_proposals_within_scope(self):
        staff = self.exact_bundle_staff()
        own = request_generation(
            user=staff, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
        self.assertEqual(own.status, "PROPOSAL_READY", own.diagnostics)
        other = ScheduleGenerationRun.objects.create(
            schedule=self.schedule, academic_term=self.term,
            department=self.department, requested_by=self.chair,
            strategy="FILL_GAPS", status="PROPOSAL_READY",
        )
        self.assertEqual(discard_generation(user=staff, run_id=other.pk).status, "DISCARDED")
        self.assertEqual(discard_generation(user=staff, run_id=own.pk).status,
                         "DISCARDED")
        other.refresh_from_db()
        self.assertEqual(other.status, "DISCARDED")

    def test_readiness_failure_keeps_solver_unimported(self):
        self.configuration.delete()
        with patch.dict("sys.modules", {"timetabling.solver.engine": None}):
            run = self.generate()
        self.assertEqual(run.status, "INPUT_INVALID")
        self.assertIsNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.solver_status, "")
        self.assertEqual(run.proposed_meetings, [])

    def test_request_audit_failure_rolls_back_run(self):
        with patch("timetabling.generation.record_event", side_effect=RuntimeError("audit")):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.generate()
        self.assertFalse(ScheduleGenerationRun.objects.exists())

    def test_capability_and_foreign_scope_fail_before_request_creation(self):
        from .models import Schedule
        with self.assertRaises(PermissionDenied):
            request_generation(
                user=type(self.staff).objects.create_user(username="denied-generation"), schedule_id=self.schedule.pk,
                strategy="FILL_GAPS", overrides=GenerationOverrides(),
            )
        foreign = Schedule.objects.create(
            name="Foreign", department=self.external, academic_term=self.term,
        )
        with self.assertRaises(Http404):
            request_generation(
                user=self.chair, schedule_id=foreign.pk,
                strategy="FILL_GAPS", overrides=GenerationOverrides(),
            )
        self.assertFalse(ScheduleGenerationRun.objects.exists())

    def test_preview_and_acceptance_preserve_snapshot_and_set_provenance(self):
        run = self.ready()
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.solver_status, "OPTIMAL")
        self.assertEqual(run.proposed_meeting_count, len(run.proposed_meetings))
        self.assertEqual(ScheduleEntry.objects.filter(generation_run=run).count(), 0)
        snapshot = (run.configuration_snapshot.copy(), run.input_summary.copy(),
                    run.source_signature, list(run.proposed_meetings))
        accepted = accept_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(accepted.status, "ACCEPTED")
        self.assertEqual(accepted.accepted_meeting_count, run.proposed_meeting_count)
        self.assertEqual(ScheduleEntry.objects.filter(generation_run=run).count(),
                         run.proposed_meeting_count)
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run, is_locked=True).exists())
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exclude(created_by=self.chair).exists())
        self.assertEqual((accepted.configuration_snapshot, accepted.input_summary,
                          accepted.source_signature, accepted.proposed_meetings), snapshot)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "draft")
        with self.assertRaises(InvalidRunTransition):
            discard_generation(user=self.chair, run_id=run.pk)

    def test_stale_source_rejects_acceptance_without_mutation(self):
        run = self.ready()
        FacultyAvailability.objects.create(
            faculty=self.faculty, academic_term=self.term, day_of_week=1,
            start_time=time(8), end_time=time(9), availability_type="unavailable",
        )
        stale = accept_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(stale.status, "STALE")
        self.assertEqual(stale.proposed_meetings, run.proposed_meetings)
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())
        self.assertIsNone(stale.accepted_at)
        self.assertFalse(AuditLog.objects.filter(action="generation.accepted").exists())

    def test_discard_is_terminal_and_retains_preview(self):
        run = self.ready()
        discarded = discard_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(discarded.status, "DISCARDED")
        self.assertIsNotNone(discarded.discarded_at)
        self.assertEqual(discarded.proposed_meetings, run.proposed_meetings)
        with self.assertRaises(InvalidRunTransition):
            accept_generation(user=self.chair, run_id=run.pk)

    def test_raw_statuses_keep_exact_solver_status(self):
        for raw, target in (("INFEASIBLE", "INFEASIBLE"),
                            ("MODEL_INVALID", "FAILED"), ("UNKNOWN", "FAILED")):
            with self.subTest(raw=raw):
                result = SolverResult(raw_status=raw, statistics=SolverStatistics(wall_time_seconds=0.1))
                with patch("timetabling.solver.engine.solve", return_value=result):
                    run = self.generate()
                self.assertEqual((run.solver_status, run.status), (raw, target))
                self.assertEqual(run.proposed_meetings, [])

    def test_contract_rejects_corrupted_row_and_missing_occurrence(self):
        run = self.ready()
        from .generation_inputs import prepare_generation_input
        prepared = prepare_generation_input(
            user=self.chair, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
        rows = [dict(item) for item in run.proposed_meetings]
        rows[0]["assignment_id"] = True
        codes = {item.code for item in validate_generation_contract(
            run=run, prepared=prepared, proposal_rows=rows, user=self.chair,
        )}
        self.assertIn("PROPOSAL_SCHEMA", codes)
        self.assertIn("PROPOSAL_DEMAND", codes)
        codes = {item.code for item in validate_generation_contract(
            run=run, prepared=prepared, proposal_rows=run.proposed_meetings[:-1],
            user=self.chair,
        )}
        self.assertIn("PROPOSAL_DEMAND", codes)

    def test_candidate_membership_checked_at_both_boundaries(self):
        from .generation_inputs import prepare_generation_input
        run = self.ready()
        prepared = prepare_generation_input(
            user=self.chair, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
        row = run.proposed_meetings[0]
        policy = prepared.solver_input.policy
        start_slot = (int(row["start_time"][:2]) * 60 + int(row["start_time"][3:])
                      - policy.earliest_minute) // policy.slot_increment_minutes
        end_slot = (int(row["end_time"][:2]) * 60 + int(row["end_time"][3:])
                    - policy.earliest_minute) // policy.slot_increment_minutes
        removed = (
            (row["meeting_requirement_id"], row["occurrence_index"]),
            row["assignment_id"], row["room_id"], row["day_of_week"],
            start_slot, end_slot, row["meeting_type"],
        )
        limited_input = replace(
            prepared.solver_input,
            candidates=tuple(candidate for candidate in prepared.solver_input.candidates
                if (candidate.demand_key, candidate.assignment_id, candidate.room_id,
                    candidate.day_of_week, candidate.start_slot, candidate.end_slot,
                    candidate.meeting_type) != removed),
        )
        limited = replace(prepared, solver_input=limited_input)
        self.assertIn("PROPOSAL_DEMAND", {item.code for item in validate_generation_contract(
            run=run, prepared=limited, proposal_rows=run.proposed_meetings,
            user=self.chair,
        )})

        proposed = tuple(ProposedMeeting(
            assignment_id=item["assignment_id"],
            meeting_requirement_id=item["meeting_requirement_id"],
            occurrence_index=item["occurrence_index"], room_id=item["room_id"],
            day_of_week=item["day_of_week"],
            start_slot=(int(item["start_time"][:2]) * 60 + int(item["start_time"][3:])
                        - policy.earliest_minute) // policy.slot_increment_minutes,
            end_slot=(int(item["end_time"][:2]) * 60 + int(item["end_time"][3:])
                      - policy.earliest_minute) // policy.slot_increment_minutes,
            meeting_type=item["meeting_type"],
        ) for item in run.proposed_meetings)
        pending = ScheduleGenerationRun.objects.create(
            schedule=self.schedule, academic_term=self.term,
            department=self.department, requested_by=self.chair,
            strategy="FILL_GAPS", status="RUNNING",
            configuration_snapshot=run.configuration_snapshot,
            input_summary=run.input_summary,
            source_signature=run.source_signature,
        )
        result = SolverResult(raw_status="FEASIBLE", proposals=proposed)
        finalized = finish_generation_if_expected(pending.pk, result, limited, self.chair)
        self.assertEqual(finalized.status, "VALIDATION_FAILED")
        self.assertEqual(finalized.proposed_meetings, [])

        with patch("timetabling.generation.prepare_generation_input", return_value=limited):
            rejected = accept_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(rejected.status, "VALIDATION_FAILED")
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())

    def test_locked_authorization_and_scope_failures_leave_ready_run_unchanged(self):
        run = self.ready()
        with patch("timetabling.generation.require_generation_access",
                   side_effect=[None, None, None, None, PermissionDenied("revoked")]):
            with self.assertRaises(PermissionDenied):
                accept_generation(user=self.chair, run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "PROPOSAL_READY")
        self.assertFalse(AuditLog.objects.filter(action="generation.failed").exists())
        from .generation_inputs import get_generation_run as real_get
        def late_missing(user, run_id, *, lock=False):
            if lock:
                raise Http404()
            return real_get(user, run_id, lock=lock)
        with patch("timetabling.generation.get_generation_run", side_effect=late_missing):
            with self.assertRaises(Http404):
                accept_generation(user=self.chair, run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "PROPOSAL_READY")

    def test_captured_override_round_trip(self):
        run = self.generate(overrides=GenerationOverrides(solver_time_limit_seconds=3))
        self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
        self.assertEqual(run.configuration_snapshot["solver_time_limit_seconds"], 3)
        accepted = accept_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(accepted.status, "ACCEPTED")

    def test_acceptance_audit_failure_rolls_back_entries_and_run(self):
        run = self.ready()
        with patch("timetabling.generation.record_event", side_effect=RuntimeError("audit")):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                accept_generation(user=self.chair, run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "PROPOSAL_READY")
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())

    def test_replace_deletes_only_unlocked_and_preserves_locked(self):
        locked = ScheduleEntry.objects.create(
            schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=1, start_time=time(8), end_time=time(9),
            meeting_type="lecture", is_locked=True,
        )
        unlocked = ScheduleEntry.objects.create(
            schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=2, start_time=time(8), end_time=time(11),
            meeting_type="laboratory", is_locked=False,
        )
        run = self.ready(strategy="REPLACE_UNLOCKED")
        accepted = accept_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(accepted.status, "ACCEPTED")
        self.assertTrue(ScheduleEntry.objects.filter(pk=locked.pk).exists())
        self.assertFalse(ScheduleEntry.objects.filter(pk=unlocked.pk).exists())
        self.assertEqual(ScheduleEntry.objects.filter(generation_run=run).count(),
                         accepted.accepted_meeting_count)
        event = AuditLog.objects.get(action="schedule.entries_replaced")
        self.assertEqual(event.details["deleted_entry_ids"], [unlocked.pk])

    def test_acceptance_revalidates_corrupted_stored_proposal(self):
        run = self.ready()
        rows = [dict(item) for item in run.proposed_meetings]
        rows[0]["room_id"] = 99999999
        ScheduleGenerationRun.objects.filter(pk=run.pk).update(proposed_meetings=rows)
        invalid = accept_generation(user=self.chair, run_id=run.pk)
        self.assertEqual(invalid.status, "VALIDATION_FAILED")
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())
        self.assertEqual(invalid.proposed_meetings, rows)

    def test_accepted_generation_does_not_change_workload(self):
        from workloads.calculation import calculate_workload
        before = calculate_workload(self.faculty, self.term)
        run = self.ready()
        accept_generation(user=self.chair, run_id=run.pk)
        after = calculate_workload(self.faculty, self.term)
        self.assertEqual(before["teaching_units"], after["teaching_units"])
        self.assertEqual(before["teaching_hours"], after["teaching_hours"])

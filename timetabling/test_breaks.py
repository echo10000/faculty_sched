"""Lunch is hard; actual consecutive faculty rest is a separate soft preference."""
from dataclasses import replace
from datetime import time

from django.test import SimpleTestCase
from .test_generation_objectives import SolverInputFactory
from .solver.engine import solve
from .solver.candidates import build_candidates
from .solver.contracts import CandidateLimits, RoomOption
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from .tests import TimetableFixture
from .models import SchedulingConfiguration, AssignmentMeetingRequirement, ScheduleEntry, ScheduleApprovalSnapshot
from .generation_inputs import prepare_generation_input, GenerationOverrides
from .conflicts import detect_entry_conflicts, get_schedule_conflicts


class BreakIntegrationTests(TimetableFixture):
    def setUp(self):
        self.config = SchedulingConfiguration.objects.create(academic_term=self.term, department=self.department,
            allowed_weekdays=[1, 2], earliest_start=time(8), latest_end=time(17),
            slot_increment_minutes=30, solver_time_limit_seconds=5, random_seed=17, worker_count=1)
        self.policy(recommended_load=12, maximum_load=18)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,
            meeting_type="lecture", meetings_per_week=2, duration_minutes=60)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,
            meeting_type="laboratory", meetings_per_week=1, duration_minutes=180)

    def enable_lunch(self, **changes):
        self.config.lunch_break_enabled = True
        self.config.lunch_start, self.config.lunch_end = time(12), time(13)
        for key, value in changes.items():
            setattr(self.config, key, value)
        self.config.save()

    def prepare(self, **kwargs):
        return prepare_generation_input(user=self.staff, schedule_id=self.schedule.pk,
            strategy=kwargs.pop("strategy", "FILL_GAPS"), overrides=GenerationOverrides(), **kwargs)

    def codes(self, entry):
        return {item.code: item.severity for item in detect_entry_conflicts(entry, user=self.staff)}

    def test_legacy_configuration_has_both_rules_disabled(self):
        self.assertFalse(self.config.lunch_break_enabled)
        self.assertIsNone(self.config.lunch_start)
        self.assertEqual(self.config.faculty_rest_minutes, 0)
        prepared = self.prepare()
        self.assertIsNotNone(prepared.solver_input)
        self.assertTrue(any(item.start_slot == 8 for item in prepared.solver_input.candidates))
        self.assertNotIn("LUNCH_BREAK_OVERLAP", self.codes(self.candidate()))

    def test_lunch_model_validation_and_database_guards(self):
        for changes in ({"lunch_break_enabled": True},
                {"lunch_break_enabled": True, "lunch_start": time(13), "lunch_end": time(12)},
                {"faculty_rest_minutes": 45}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                SchedulingConfiguration.objects.filter(pk=self.config.pk).update(**changes)
        self.config.lunch_break_enabled = True
        with self.assertRaises(ValidationError):
            self.config.full_clean()

    def test_manual_lunch_boundaries_and_crossing_meetings(self):
        self.enable_lunch()
        for start, end, blocked in ((time(11), time(12), False), (time(13), time(14), False),
                (time(11, 30), time(12, 30), True), (time(12), time(13), True),
                (time(12, 30), time(13, 30), True), (time(11), time(14), True)):
            with self.subTest(start=start, end=end):
                entry = self.candidate()
                entry.start_time, entry.end_time = start, end
                self.assertEqual("LUNCH_BREAK_OVERLAP" in self.codes(entry), blocked)

    def test_lunch_is_scoped_to_the_configured_department_and_term(self):
        entry = self.candidate()
        entry.start_time, entry.end_time = time(12), time(13)
        self.enable_lunch()
        self.schedule.academic_term = self.later
        self.assertNotIn("LUNCH_BREAK_OVERLAP", self.codes(entry))
        self.schedule.academic_term, self.schedule.department = self.term, self.external
        self.assertNotIn("LUNCH_BREAK_OVERLAP", self.codes(entry))

    def test_lunch_and_rest_are_captured_and_change_dependency_signature(self):
        from .signatures import dependency_signature
        before = dependency_signature(self.schedule)
        self.enable_lunch(faculty_rest_minutes=60)
        prepared = self.prepare()
        self.assertNotEqual(before, prepared.source_signature)
        self.assertEqual(prepared.configuration_snapshot["lunch_start"], "12:00")
        self.assertEqual(prepared.solver_input.policy.faculty_rest_minutes, 60)
        for item in prepared.solver_input.candidates:
            self.assertFalse(item.start_slot < 10 and item.end_slot > 8)

    def test_locked_lunch_violation_is_found_and_never_deleted(self):
        entry = self.candidate()
        entry.start_time, entry.end_time = time(12), time(13)
        entry.save()
        self.enable_lunch()
        prepared = self.prepare(strategy="REPLACE_UNLOCKED")
        self.assertIn("LUNCH_BREAK_OVERLAP", {item.code for item in prepared.issues})
        self.assertIsNone(prepared.solver_input)
        self.assertTrue(ScheduleEntry.objects.filter(pk=entry.pk, start_time=time(12), is_locked=True).exists())

    def other_section_meeting(self, start=time(9), end=time(10)):
        from .models import ClassSection, OfferingRequirement
        from workloads.models import SubjectOffering, FacultySubjectAssignment
        section = ClassSection.objects.create(academic_term=self.term, department=self.department, code="REST-B")
        offering = SubjectOffering.objects.create(subject=self.offering.subject, department=self.department,
            academic_term=self.term, code="OTHER-SECTION", lecture_units=2, laboratory_units=1,
            lecture_hours=2, laboratory_hours=3)
        OfferingRequirement.objects.create(subject_offering=offering, section=section)
        assignment = FacultySubjectAssignment.objects.create(faculty=self.faculty, subject_offering=offering)
        return ScheduleEntry.objects.create(schedule=self.schedule, assignment=assignment, room=self.room,
            day_of_week=1, start_time=start, end_time=end, meeting_type="lecture", is_locked=False)

    def test_section_generation_checks_other_section_lunch_and_preserves_its_entries(self):
        entry = self.other_section_meeting(time(12), time(13))
        self.enable_lunch()
        prepared = self.prepare(strategy="REPLACE_UNLOCKED", section_id=self.section.pk)
        self.assertIn(entry.pk, prepared.retained_entry_ids)
        self.assertIn("LUNCH_BREAK_OVERLAP", {item.code for item in prepared.issues})
        self.assertTrue(ScheduleEntry.objects.filter(pk=entry.pk).exists())

    def test_rest_uses_other_section_actual_times_and_is_only_a_warning(self):
        self.other_section_meeting()
        self.config.faculty_rest_minutes = 30
        self.config.save()
        entry = self.candidate()
        entry.start_time, entry.end_time = time(10), time(11)
        self.assertEqual(self.codes(entry)["FACULTY_REST_PREFERENCE"], "WARNING")
        entry.start_time, entry.end_time = time(10, 30), time(11, 30)
        self.assertNotIn("FACULTY_REST_PREFERENCE", self.codes(entry))
        prepared = self.prepare(section_id=self.section.pk)
        self.assertIsNotNone(prepared.solver_input)
        self.assertTrue(any(not fixed.counts_for_distribution for fixed in prepared.solver_input.fixed_meetings))

    def test_manual_save_rejects_lunch_without_writing(self):
        from .mutations import save_entry
        self.enable_lunch()
        entry, form, conflicts = save_entry(user=self.staff, schedule_id=self.schedule.pk, data={
            "assignment": self.assignment.pk, "room": self.room.pk, "day_of_week": 1,
            "start_time": "11:30", "end_time": "12:30", "meeting_type": "lecture"})
        self.assertIsNone(entry)
        self.assertIn("LUNCH_BREAK_OVERLAP", {item.code for item in conflicts})
        self.assertEqual(self.schedule.entries.count(), 0)

    def test_lunch_changes_make_proposal_stale_before_acceptance(self):
        from .generation import request_generation, accept_generation
        run = request_generation(user=self.staff, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertEqual(run.status, "PROPOSAL_READY")
        self.enable_lunch()
        accepted = accept_generation(user=self.staff, run_id=run.pk)
        self.assertEqual(accepted.status, "STALE")
        self.assertEqual(self.schedule.entries.count(), 0)

    def test_generation_acceptance_enforces_lunch_and_keeps_rest_soft(self):
        from .generation import request_generation, accept_generation
        self.enable_lunch(faculty_rest_minutes=60)
        run = request_generation(user=self.staff, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertEqual(run.status, "PROPOSAL_READY")
        self.assertIn("faculty_rest", run.penalty_breakdown)
        self.assertEqual(accept_generation(user=self.staff, run_id=run.pk).status, "ACCEPTED")
        self.assertNotIn("LUNCH_BREAK_OVERLAP", {item.code for item in get_schedule_conflicts(self.schedule)})

    def test_tampered_lunch_proposal_cannot_be_accepted(self):
        from .generation import request_generation, accept_generation
        self.enable_lunch()
        run = request_generation(user=self.staff, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertEqual(run.status, "PROPOSAL_READY")
        row = next(item for item in run.proposed_meetings if item["meeting_type"] == "lecture")
        row["start_time"], row["end_time"] = "12:00", "13:00"
        run.save(update_fields=["proposed_meetings"])
        self.assertEqual(accept_generation(user=self.staff, run_id=run.pk).status, "VALIDATION_FAILED")
        self.assertEqual(self.schedule.entries.count(), 0)

    def complete_schedule(self):
        for start, end, day, kind in ((12, 13, 1, "lecture"), (14, 15, 1, "lecture"), (9, 12, 2, "laboratory")):
            ScheduleEntry.objects.create(schedule=self.schedule, assignment=self.assignment, room=self.room,
                day_of_week=day, start_time=time(start), end_time=time(end), meeting_type=kind)

    def test_final_publication_checks_lunch_after_validation(self):
        from .mutations import validate_schedule
        from .workflow import finalize_schedule
        self.complete_schedule()
        validate_schedule(user=self.staff, schedule_id=self.schedule.pk)
        self.enable_lunch()
        report = validate_schedule(user=self.staff, schedule_id=self.schedule.pk)
        self.assertIn("LUNCH_BREAK_OVERLAP", {item.code for item in report["conflicts"]})
        self.schedule.refresh_from_db()
        with self.assertRaises(ValidationError):
            finalize_schedule(user=self.staff, schedule_id=self.schedule.pk,
                revision_token=self.schedule.revision_token, acknowledged_warnings=("ROOM_CAPACITY_WARNING",))
        self.assertFalse(ScheduleApprovalSnapshot.objects.exists())

    def test_enabling_lunch_never_rewrites_published_snapshots(self):
        from .mutations import validate_schedule
        from .workflow import finalize_schedule
        self.complete_schedule()
        report = validate_schedule(user=self.staff, schedule_id=self.schedule.pk)
        self.schedule.refresh_from_db()
        warnings = {item.code for item in report["conflicts"] if item.severity == "WARNING"}
        finalize_schedule(user=self.staff, schedule_id=self.schedule.pk,
            revision_token=self.schedule.revision_token, acknowledged_warnings=warnings)
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=self.schedule)
        self.enable_lunch()
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "approved")
        self.assertEqual(self.schedule.entries.count(), 3)
        self.assertEqual(ScheduleApprovalSnapshot.objects.get(pk=snapshot.pk).payload, snapshot.payload)
        self.assertIn("LUNCH_BREAK_OVERLAP", {item.code for item in get_schedule_conflicts(self.schedule)})

    def test_configuration_form_has_clear_break_controls_and_scoped_links(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("timetabling:configurations-edit", args=[self.config.pk]), {
            "academic_term": self.term.pk, "department": self.department.pk,
            "workflow_schedule": self.schedule.pk, "workflow_stage": 4})
        self.assertContains(response, "Prefer at least 30 minutes")
        self.assertContains(response, "Enable daily lunch break")
        self.assertContains(response, "Hard rule")
        self.assertContains(response, "Return to scheduling workspace")

    def test_configuration_editor_identifies_saved_lunch_violations(self):
        entry = self.candidate()
        entry.start_time, entry.end_time = time(12), time(13)
        entry.save()
        self.enable_lunch()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("timetabling:configurations-edit", args=[self.config.pk]))
        self.assertContains(response, "Existing meetings overlapping lunch")
        self.assertContains(response, "12:00")
        self.assertEqual(self.schedule.entries.count(), 1)

    def test_lunch_covering_operating_window_returns_no_valid_placements(self):
        self.enable_lunch(lunch_start=time(8), lunch_end=time(17))
        prepared = self.prepare()
        self.assertIn("ZERO_CANDIDATES", {item.code for item in prepared.issues})
        self.assertIsNone(prepared.solver_input)
        self.assertEqual(self.schedule.entries.count(), 0)

    def test_configuration_form_preserves_input_on_missing_lunch_end(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("timetabling:configurations-edit", args=[self.config.pk]), {
            "allowed_weekdays": [1, 2], "earliest_start": "08:00", "latest_end": "17:00",
            "slot_increment_minutes": 30, "solver_time_limit_seconds": 5, "random_seed": 17,
            "worker_count": 1, "lunch_break_enabled": "on", "lunch_start": "12:15",
            "faculty_rest_minutes": 30, **{name: 0 for name in SchedulingConfiguration.WEIGHT_FIELDS}})
        self.assertEqual(response.status_code, 200)
        self.assertIn("lunch_end", response.context["form"].errors)
        self.assertContains(response, 'value="12:15"')
        self.config.refresh_from_db()
        self.assertFalse(self.config.lunch_break_enabled)

    def test_configuration_changes_keep_workflow_context_and_existing_meetings(self):
        entry = self.candidate()
        entry.start_time, entry.end_time = time(12), time(13)
        entry.save()
        self.client.force_login(self.staff)
        url = reverse("timetabling:configurations-edit", args=[self.config.pk]) + (
            f"?academic_term={self.term.pk}&department={self.department.pk}&workflow_schedule={self.schedule.pk}&workflow_stage=4")
        response = self.client.post(url, {
            "allowed_weekdays": [1, 2], "earliest_start": "08:00", "latest_end": "17:00",
            "slot_increment_minutes": 30, "solver_time_limit_seconds": 5, "random_seed": 17,
            "worker_count": 1, "lunch_break_enabled": "on", "lunch_start": "12:00", "lunch_end": "13:00",
            "faculty_rest_minutes": 60, **{name: 0 for name in SchedulingConfiguration.WEIGHT_FIELDS}})
        self.assertEqual(response.status_code, 302)
        self.assertIn("workflow_schedule=", response.url)
        self.config.refresh_from_db()
        self.assertEqual(self.config.faculty_rest_minutes, 60)
        self.assertTrue(self.config.lunch_break_enabled)
        self.assertTrue(ScheduleEntry.objects.filter(pk=entry.pk, start_time=time(12)).exists())

    def test_unauthorized_configuration_update_cannot_enable_lunch(self):
        from django.test import override_settings
        from django.contrib.auth.models import Permission
        user = self.make_user("break-viewer", "staff", department=self.department)
        user.user_permissions.add(Permission.objects.get(codename="view_schedulingconfiguration"))
        with override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"]):
            self.client.force_login(user)
            response = self.client.post(reverse("timetabling:configurations-edit", args=[self.config.pk]),
                {"lunch_break_enabled": "on", "lunch_start": "12:00", "lunch_end": "13:00"})
            self.assertEqual(response.status_code, 403)
        self.config.refresh_from_db()
        self.assertFalse(self.config.lunch_break_enabled)

    def test_generator_post_cannot_override_saved_break_policy(self):
        from .forms import GenerationRequestForm
        form = GenerationRequestForm({"schedule": self.schedule.pk, "strategy": "FILL_GAPS",
            "lunch_break_enabled": "on", "faculty_rest_minutes": 60}, user=self.staff)
        self.assertTrue(form.is_valid(), form.errors)
        prepared = prepare_generation_input(user=self.staff, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=form.cleaned_data["overrides"])
        self.assertIsNone(prepared.solver_input.policy.lunch_start_minute)
        self.assertEqual(prepared.solver_input.policy.faculty_rest_minutes, 0)

    def test_legacy_configuration_submission_preserves_enabled_policy(self):
        from .forms import SchedulingConfigurationForm
        self.enable_lunch(faculty_rest_minutes=60)
        form = SchedulingConfigurationForm({"allowed_weekdays": [1, 2], "earliest_start": "08:00",
            "latest_end": "17:00", "slot_increment_minutes": 30, "solver_time_limit_seconds": 5,
            "worker_count": 1, "random_seed": 17,
            **{name: 0 for name in SchedulingConfiguration.WEIGHT_FIELDS}}, user=self.staff, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertTrue(saved.lunch_break_enabled)
        self.assertEqual(saved.lunch_start, time(12))
        self.assertEqual(saved.faculty_rest_minutes, 60)

    def test_other_section_rest_is_visible_in_section_proposal_feedback(self):
        from .generation import request_generation
        self.other_section_meeting(time(9), time(10))
        self.enable_lunch(faculty_rest_minutes=60)
        run = request_generation(user=self.staff, schedule_id=self.schedule.pk, section_id=self.section.pk,
            strategy="REPLACE_UNLOCKED", overrides=GenerationOverrides())
        self.assertEqual(run.status, "PROPOSAL_READY")
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:generation-run-detail", args=[run.pk]))
        self.assertContains(page, "Faculty rest preference")
        self.assertContains(page, "Reported short consecutive faculty gaps")
        self.assertEqual(run.configuration_snapshot["generation_section_id"], self.section.pk)


class RestSolverTests(SolverInputFactory, SimpleTestCase):
    def rest_input(self, *, minutes=30, **kwargs):
        data = self.solver_input(**kwargs)
        return replace(data, policy=replace(data.policy, faculty_rest_minutes=minutes))

    def test_rest_precedes_compactness_without_changing_gap_meaning(self):
        from .solver.contracts import ObjectiveWeights
        demand = self.demand(section_id=401)
        data = self.rest_input(demands=(demand,), fixed_meetings=(self.fixed(),),
            candidates=(self.candidate(demand, start_slot=1, end_slot=2),
                        self.candidate(demand, start_slot=2, end_slot=3)),
            weights=ObjectiveWeights(faculty_gap=100000))
        result = solve(data)
        self.assertEqual(result.proposals[0].start_slot, 2)
        self.assertEqual(result.penalties.faculty_rest, 0)
        self.assertEqual(result.penalties.faculty_gap, 1)
        legacy = solve(replace(data, policy=replace(data.policy, faculty_rest_minutes=0)))
        self.assertEqual(legacy.proposals[0].start_slot, 1)
        self.assertEqual(legacy.penalties.faculty_gap, 0)

    def test_thirty_and_sixty_minute_boundaries_and_back_to_back(self):
        demand = self.demand(section_id=401)
        for rest, start, violations in ((30, 1, 1), (30, 2, 0), (60, 2, 1), (60, 3, 0)):
            with self.subTest(rest=rest, start=start):
                result = solve(self.rest_input(minutes=rest, demands=(demand,),
                    candidates=(self.candidate(demand, start_slot=start, end_slot=start + 1),),
                    fixed_meetings=(self.fixed(),)))
                self.assertEqual(result.raw_status, "OPTIMAL")
                self.assertEqual(result.penalties.faculty_rest, violations)
                self.assertEqual(bool(result.issues), bool(violations))

    def test_large_daily_idle_total_does_not_hide_a_short_consecutive_gap(self):
        first, second = self.demand(), self.demand(meeting_requirement_id=201, section_id=401)
        result = solve(self.rest_input(demands=(first, second), candidates=(
            self.candidate(first, start_slot=0, end_slot=1),
            self.candidate(second, start_slot=6, end_slot=7)),
            fixed_meetings=(self.fixed(assignment_id=101, start_slot=5, end_slot=6),)))
        self.assertGreater(result.penalties.faculty_gap, 3)
        self.assertEqual(result.penalties.faculty_rest, 1)

    def test_other_days_and_other_faculty_do_not_count_as_consecutive(self):
        demand = self.demand()
        result = solve(self.rest_input(demands=(demand,), candidates=(
            self.candidate(demand, day_of_week=2, start_slot=1, end_slot=2),),
            fixed_meetings=(self.fixed(), self.fixed(faculty_id=999, day_of_week=2))))
        self.assertEqual(result.penalties.faculty_rest, 0)

    def test_fixed_and_other_section_peers_use_actual_end_not_rounded_slot(self):
        demand = self.demand(section_id=401)
        # Peer closes at 08:40, but collision protection rounds its end to 09:00.
        fixed = replace(self.fixed(end_slot=2, counts_for_distribution=False, assignment_id=None),
                        actual_start_microsecond=8 * 3600 * 1000000,
                        actual_end_microsecond=(8 * 3600 + 40 * 60) * 1000000)
        result = solve(self.rest_input(demands=(demand,), candidates=(
            self.candidate(demand, start_slot=3, end_slot=4),), fixed_meetings=(fixed,)))
        self.assertEqual(result.penalties.faculty_rest, 0)  # Actual gap is 50 min.
        data = self.rest_input(minutes=60, demands=(demand,), candidates=(
            self.candidate(demand, start_slot=3, end_slot=4),), fixed_meetings=(fixed,))
        self.assertEqual(solve(data).penalties.faculty_rest, 1)

    def test_fixed_fixed_short_gap_is_reported_even_when_nothing_can_move(self):
        result = solve(self.rest_input(fixed_meetings=(self.fixed(),
            self.fixed(assignment_id=101, start_slot=1, end_slot=2))))
        self.assertEqual(result.raw_status, "OPTIMAL")
        self.assertEqual(result.penalties.faculty_rest, 1)

    def test_microsecond_boundary_is_not_rounded_into_a_satisfied_preference(self):
        demand = self.demand()
        fixed = replace(self.fixed(end_slot=3, counts_for_distribution=False, assignment_id=None),
            actual_start_microsecond=8 * 3600 * 1000000,
            actual_end_microsecond=9 * 3600 * 1000000 + 1)
        data = self.rest_input(demands=(demand,), fixed_meetings=(fixed,), candidates=(
            self.candidate(demand, start_slot=3, end_slot=4),))
        self.assertEqual(solve(data).penalties.faculty_rest, 1)
        fixed = replace(fixed, actual_end_microsecond=9 * 3600 * 1000000)
        self.assertEqual(solve(replace(data, fixed_meetings=(fixed,))).penalties.faculty_rest, 0)

    def test_unrelated_protected_faculty_does_not_add_rest_shortfalls(self):
        demand = self.demand()
        peers = (self.fixed(faculty_id=999, section_id=999, counts_for_distribution=False, assignment_id=None),
            self.fixed(faculty_id=999, section_id=999, start_slot=1, end_slot=2, counts_for_distribution=False, assignment_id=None))
        result = solve(self.rest_input(demands=(demand,), fixed_meetings=peers,
            candidates=(self.candidate(demand),)))
        self.assertEqual(result.penalties.faculty_rest, 0)

    def test_soft_preference_cannot_turn_a_hard_feasible_schedule_infeasible(self):
        demand = self.demand()
        result = solve(self.rest_input(minutes=60, demands=(demand,),
            fixed_meetings=(self.fixed(),), candidates=(self.candidate(demand, start_slot=1, end_slot=2),)))
        self.assertTrue(result.proposals)
        self.assertEqual(result.penalties.faculty_rest, 1)

    def test_lunch_is_hard_even_for_a_direct_solver_input(self):
        demand = self.demand()
        data = self.solver_input(demands=(demand,), candidates=(self.candidate(demand, start_slot=2, end_slot=3),))
        data = replace(data, policy=replace(data.policy, lunch_start_minute=9 * 60, lunch_end_minute=10 * 60))
        result = solve(data)
        self.assertEqual(result.raw_status, "INFEASIBLE")
        self.assertFalse(result.proposals)


class LunchCandidateTests(SolverInputFactory, SimpleTestCase):
    def test_half_open_lunch_filter_includes_crossing_and_off_grid_bounds(self):
        policy = replace(self.policy(), latest_minute=17 * 60,
                         lunch_start_minute=12 * 60 + 15, lunch_end_minute=13 * 60 + 15)
        demand = self.demand(duration_slots=2)
        result = build_candidates(policy=policy, demands=(demand,), rooms=(RoomOption(500, None, 40),),
            unavailable=(), room_closures=(), fixed_meetings=(), preferred=(),
            limits=CandidateLimits(10, 10000, 100000))
        self.assertTrue(result.candidates)
        for item in result.candidates:
            start, end = 480 + item.start_slot * 30, 480 + item.end_slot * 30
            self.assertFalse(start < 795 and end > 735)
        starts = {item.start_slot for item in result.candidates}
        self.assertIn(6, starts)  # 11:00-12:00
        self.assertNotIn(7, starts)  # 11:30-12:30
        self.assertIn(11, starts)  # 13:30-14:30

from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.http import Http404
from django.test import TransactionTestCase, override_settings

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from accounts.models import AdminProfile
from core.models import College, Department
from faculty.models import EmploymentCategory, Faculty
from resources.models import Building, RoomType
from scheduling.models import Room
from workloads.models import (
    FacultyAvailability,
    FacultySubjectAssignment,
    SubjectOffering,
)

from .generation_inputs import (
    GENERATION_PERMISSIONS,
    GenerationOverrides,
    generation_run_queryset,
    get_generation_run,
    prepare_generation_input,
)
from .models import (
    AssignmentMeetingRequirement,
    ClassSection,
    OfferingRequirement,
    RoomUnavailability,
    Schedule,
    ScheduleEntry,
    ScheduleGenerationRun,
    SchedulingConfiguration,
)
from .solver.contracts import CandidateBuildResult, ReadinessIssue
from .tests import TimetableFixture


class GenerationInputTests(TimetableFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_subject = Subject.objects.create(
            code="OTHER",
            title="Other subject",
            units=Decimal("1"),
            lecture_units=Decimal("1"),
            laboratory_units=Decimal("0"),
            lecture_hours=Decimal("1"),
            laboratory_hours=Decimal("0"),
            owning_department=cls.department,
        )
        cls.foreign_schedule = Schedule.objects.create(
            name="Protected foreign schedule",
            department=cls.external,
            academic_term=cls.term,
        )

    def setUp(self):
        self.configuration = SchedulingConfiguration.objects.create(
            academic_term=self.term,
            department=self.department,
            allowed_weekdays=[1, 2, 3, 4, 5],
            earliest_start=time(8),
            latest_end=time(17),
            slot_increment_minutes=30,
            solver_time_limit_seconds=10,
            random_seed=17,
            worker_count=1,
            faculty_preference_weight=1,
            faculty_gap_weight=2,
            section_gap_weight=3,
            meeting_distribution_weight=4,
            room_fit_weight=5,
        )
        self.lecture_requirement = AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )
        self.laboratory_requirement = AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="laboratory",
            meetings_per_week=1,
            duration_minutes=180,
        )

    def build(self, *, user=None, strategy="FILL_GAPS", overrides=None):
        return prepare_generation_input(
            user=user or self.chair,
            schedule_id=self.schedule.pk,
            strategy=strategy,
            overrides=overrides or GenerationOverrides(),
        )

    def codes(self, prepared):
        return {issue.code for issue in prepared.issues}

    def entry(self, **changes):
        values = {
            "schedule": self.schedule,
            "assignment": self.assignment,
            "room": self.room,
            "day_of_week": 1,
            "start_time": time(8),
            "end_time": time(9),
            "meeting_type": "lecture",
        }
        values.update(changes)
        return ScheduleEntry.objects.create(**values)

    def foreign_peer(self):
        section = ClassSection.objects.create(
            academic_term=self.term,
            department=self.external,
            code="PRIVATE",
        )
        offering = self.offerings[self.external.pk]
        OfferingRequirement.objects.create(subject_offering=offering, section=section)
        assignment = FacultySubjectAssignment.objects.create(
            faculty=self.records[self.external.pk]["faculty-management"],
            subject_offering=offering,
        )
        return ScheduleEntry.objects.create(
            schedule=self.foreign_schedule,
            assignment=assignment,
            room=self.room,
            day_of_week=1,
            start_time=time(9),
            end_time=time(10),
            meeting_type="lecture",
        )

    def test_builder_includes_active_offering_without_assignment(self):
        orphan = SubjectOffering.objects.create(
            subject=self.other_subject, academic_term=self.term, department=self.department,
            code="ORPHAN", lecture_units=1, laboratory_units=0,
            lecture_hours=1, laboratory_hours=0,
        )
        prepared = prepare_generation_input(
            user=self.chair, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
        self.assertIn("ASSIGNMENT_REQUIRED", {issue.code for issue in prepared.issues})
        self.assertIn(orphan.pk, prepared.input_summary["offering_ids"])

    def test_builder_rejects_incomplete_shares_before_candidate_generation(self):
        self.assignment.share = Decimal("0.50")
        self.assignment.save()
        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build()
        self.assertIn("ASSIGNMENT_SHARES_INCOMPLETE", {issue.code for issue in prepared.issues})
        candidates.assert_not_called()

    def test_builder_rejects_excess_shares_before_candidate_generation(self):
        second_faculty = Faculty.objects.create(
            employee_id="EXCESS-SHARE",
            first_name="Excess",
            last_name="Share",
            home_department=self.department,
            employment_category=self.faculty.employment_category,
        )
        second_assignment = FacultySubjectAssignment.objects.create(
            faculty=second_faculty,
            subject_offering=self.offering,
            share=Decimal("0.50"),
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=second_assignment,
            meeting_type="lecture",
            meetings_per_week=1,
            duration_minutes=60,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=second_assignment,
            meeting_type="laboratory",
            meetings_per_week=1,
            duration_minutes=90,
        )

        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build()

        self.assertIn("ASSIGNMENT_SHARES_EXCESS", self.codes(prepared))
        candidates.assert_not_called()

    def test_foreign_schedule_id_is_404_after_capability_gate(self):
        with self.assertRaises(Http404):
            prepare_generation_input(
                user=self.chair, schedule_id=self.foreign_schedule.pk,
                strategy="FILL_GAPS", overrides=GenerationOverrides(),
            )

    def test_invalid_strategy_is_rejected_before_capability_or_orm_access(self):
        with self.assertNumQueries(0), self.assertRaises(ValueError):
            prepare_generation_input(
                user=AnonymousUser(),
                schedule_id=999999,
                strategy="FORGED",
                overrides=GenerationOverrides(),
            )

    def test_permission_gate_runs_before_schedule_lookup(self):
        with self.assertRaises(PermissionDenied):
            prepare_generation_input(
                user=self.staff,
                schedule_id=999999,
                strategy="FILL_GAPS",
                overrides=GenerationOverrides(),
            )

    def test_replace_unlocked_requires_delete_permission(self):
        permissions = []
        for qualified_name in GENERATION_PERMISSIONS:
            app_label, codename = qualified_name.split(".", 1)
            permissions.append(
                Permission.objects.get(
                    content_type__app_label=app_label,
                    codename=codename,
                )
            )
        self.staff.user_permissions.add(*permissions)

        self.assertIsNotNone(self.build(user=self.staff).solver_input)
        with self.assertRaises(PermissionDenied):
            self.build(user=self.staff, strategy="REPLACE_UNLOCKED")

    def test_missing_and_invalid_configuration_stop_before_candidates(self):
        self.configuration.delete()
        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            missing = self.build()
        self.assertIn("CONFIGURATION_MISSING", self.codes(missing))
        candidates.assert_not_called()

        self.configuration = SchedulingConfiguration.objects.create(
            academic_term=self.term,
            department=self.department,
            allowed_weekdays=[1, 2],
            earliest_start=time(8),
            latest_end=time(17),
            slot_increment_minutes=30,
            solver_time_limit_seconds=10,
            random_seed=17,
            worker_count=1,
        )
        SchedulingConfiguration.objects.filter(pk=self.configuration.pk).update(
            allowed_weekdays=[]
        )
        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            invalid = self.build()
        self.assertIn("CONFIGURATION_INVALID", self.codes(invalid))
        candidates.assert_not_called()

    def test_invalid_override_uses_configuration_validation_without_mutating_row(self):
        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build(
                overrides=GenerationOverrides(solver_time_limit_seconds=301)
            )
        self.assertIn("CONFIGURATION_INVALID", self.codes(prepared))
        candidates.assert_not_called()
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.solver_time_limit_seconds, 10)

    def test_effective_configuration_snapshot_is_normalized_and_json_compatible(self):
        self.configuration.allowed_weekdays = [5, 1, 3]
        self.configuration.save()

        prepared = self.build(
            overrides=GenerationOverrides(
                solver_time_limit_seconds=25,
                faculty_preference_weight=10,
                faculty_gap_weight=20,
                section_gap_weight=30,
                meeting_distribution_weight=40,
                room_fit_weight=50,
            )
        )

        self.assertEqual(
            prepared.configuration_snapshot,
            {
                "configuration_id": self.configuration.pk,
                "academic_term_id": self.term.pk,
                "department_id": self.department.pk,
                "allowed_weekdays": [1, 3, 5],
                "earliest_start": "08:00",
                "latest_end": "17:00",
                "slot_increment_minutes": 30,
                "solver_time_limit_seconds": 25,
                "random_seed": 17,
                "worker_count": 1,
                "faculty_preference_weight": 10,
                "faculty_gap_weight": 20,
                "section_gap_weight": 30,
                "meeting_distribution_weight": 40,
                "room_fit_weight": 50,
            },
        )
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.solver_time_limit_seconds, 10)

    def test_inactive_calendar_subject_faculty_and_section_are_readiness_errors(self):
        cases = (
            (type(self.term), self.term.pk),
            (type(self.offering.subject), self.offering.subject_id),
            (type(self.faculty), self.faculty.pk),
            (type(self.section), self.section.pk),
        )
        for model, pk in cases:
            with self.subTest(model=model.__name__):
                model.objects.filter(pk=pk).update(is_active=False)
                with patch("timetabling.generation_inputs.build_candidates") as candidates:
                    prepared = self.build()
                self.assertIn("INACTIVE_RESOURCE", self.codes(prepared))
                candidates.assert_not_called()
                model.objects.filter(pk=pk).update(is_active=True)

    def test_inactive_offering_is_excluded_from_solver_scope(self):
        SubjectOffering.objects.filter(pk=self.offering.pk).update(is_active=False)

        prepared = self.build()

        self.assertNotIn(self.offering.pk, prepared.input_summary["offering_ids"])
        self.assertEqual(prepared.solver_input.demands, ())

    def test_term_and_organization_mismatches_are_detected(self):
        later_section = ClassSection.objects.create(
            academic_term=self.later,
            department=self.department,
            code="LATER-SECTION",
        )
        OfferingRequirement.objects.filter(pk=self.requirement.pk).update(
            section=later_section
        )
        self.assertIn("TERM_MISMATCH", self.codes(self.build()))

        OfferingRequirement.objects.filter(pk=self.requirement.pk).update(
            section=self.section
        )
        Subject.objects.filter(pk=self.offering.subject_id).update(
            owning_department=self.sibling
        )
        self.assertIn("ORGANIZATION_MISMATCH", self.codes(self.build()))

    def test_missing_or_inactive_section_stops_generation(self):
        self.requirement.delete()
        self.assertIn("SECTION_REQUIRED", self.codes(self.build()))

        self.requirement = OfferingRequirement.objects.create(
            subject_offering=self.offering,
            section=self.section,
        )
        ClassSection.objects.filter(pk=self.section.pk).update(is_active=False)
        self.assertIn("INACTIVE_RESOURCE", self.codes(self.build()))

    def test_missing_meeting_requirement_is_reported_for_positive_component(self):
        self.laboratory_requirement.delete()

        prepared = self.build()

        self.assertIn("MEETING_REQUIREMENT_MISSING", self.codes(prepared))
        self.assertIsNone(prepared.solver_input)

    def test_contradictory_meeting_requirement_is_rejected(self):
        AssignmentMeetingRequirement.objects.filter(
            pk=self.lecture_requirement.pk
        ).update(duration_minutes=90)

        prepared = self.build()

        self.assertIn("MEETING_REQUIREMENT_CONTRADICTORY", self.codes(prepared))

    def test_requirement_for_zero_hour_component_is_contradictory(self):
        SubjectOffering.objects.filter(pk=self.offering.pk).update(
            laboratory_hours=Decimal("0")
        )

        prepared = self.build()

        self.assertIn("MEETING_REQUIREMENT_CONTRADICTORY", self.codes(prepared))

    def test_exact_minutes_with_off_grid_duration_is_rejected(self):
        AssignmentMeetingRequirement.objects.filter(
            pk=self.lecture_requirement.pk
        ).update(meetings_per_week=3, duration_minutes=40)

        prepared = self.build()

        self.assertIn("MEETING_REQUIREMENT_GRID_MISMATCH", self.codes(prepared))
        self.assertNotIn("MEETING_REQUIREMENT_CONTRADICTORY", self.codes(prepared))

    def test_invalid_fixed_entry_does_not_consume_an_occurrence(self):
        entry = self.entry(end_time=time(8, 30))

        prepared = self.build()

        self.assertIn("FIXED_ENTRY_INVALID_DURATION", self.codes(prepared))
        self.assertIn(
            [self.lecture_requirement.pk, 0],
            prepared.input_summary["remaining_demands"],
        )
        self.assertIn(entry.pk, prepared.retained_entry_ids)

    def test_fixed_entry_without_requirement_is_rejected(self):
        self.laboratory_requirement.delete()
        self.entry(
            end_time=time(11),
            meeting_type="laboratory",
        )

        prepared = self.build()

        self.assertIn("FIXED_ENTRY_NO_REQUIREMENT", self.codes(prepared))

    def test_excess_fixed_entries_are_rejected(self):
        self.entry(start_time=time(8), end_time=time(9))
        self.entry(start_time=time(9), end_time=time(10))
        self.entry(start_time=time(10), end_time=time(11))

        prepared = self.build()

        self.assertIn("FIXED_ENTRY_EXCESS", self.codes(prepared))

    def test_canonical_fixed_conflicts_are_retained_but_incomplete_counts_are_not(self):
        self.entry(start_time=time(8), end_time=time(9))
        self.entry(start_time=time(8, 30), end_time=time(9, 30))

        prepared = self.build()

        self.assertIn("FACULTY_OVERLAP", self.codes(prepared))
        self.assertNotIn("MEETING_REQUIREMENT_COUNT", self.codes(prepared))

    def test_no_eligible_room_is_detected_before_candidate_expansion(self):
        self.requirement.capacity_is_hard = True
        self.requirement.save()
        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build()

        self.assertIn("NO_ELIGIBLE_ROOM", self.codes(prepared))
        candidates.assert_not_called()

    def test_inactive_room_category_and_building_are_removed_and_reported(self):
        building = Building.objects.create(code="OLD", name="Old building")
        Room.objects.filter(pk=self.room.pk).update(building=building)
        RoomType.objects.filter(pk=self.room_type.pk).update(is_active=False)
        Building.objects.filter(pk=building.pk).update(is_active=False)

        prepared = self.build()

        self.assertIn("INACTIVE_RESOURCE", self.codes(prepared))
        self.assertNotIn(self.room.pk, prepared.input_summary["eligible_room_ids"])

    def test_zero_candidate_demands_return_no_solver_input(self):
        for weekday in self.configuration.allowed_weekdays:
            FacultyAvailability.objects.create(
                faculty=self.faculty,
                academic_term=self.term,
                day_of_week=weekday,
                start_time=time(8),
                end_time=time(17),
                availability_type=FacultyAvailability.Kind.UNAVAILABLE,
            )

        prepared = self.build()

        self.assertIn("ZERO_CANDIDATES", self.codes(prepared))
        self.assertIsNone(prepared.solver_input)
        counts = prepared.input_summary["candidate_counts"]
        self.assertEqual(len(counts), 3)
        self.assertTrue(all(row[2] == 0 for row in counts))

    def test_solver_input_preserves_immutable_candidate_counts(self):
        prepared = self.build()

        counts = prepared.solver_input.candidate_counts
        self.assertIsInstance(counts, tuple)
        self.assertEqual(
            prepared.input_summary["candidate_counts"],
            [[key[0], key[1], count] for key, count in counts],
        )
        with self.assertRaises(TypeError):
            counts[0] = ((self.lecture_requirement.pk, 0), 999)

    def test_available_intervals_are_informational(self):
        FacultyAvailability.objects.create(
            faculty=self.faculty,
            academic_term=self.term,
            day_of_week=1,
            start_time=time(8),
            end_time=time(17),
            availability_type=FacultyAvailability.Kind.AVAILABLE,
        )

        prepared = self.build()

        self.assertIsNotNone(prepared.solver_input)
        self.assertNotIn("ZERO_CANDIDATES", self.codes(prepared))

    def test_warning_only_readiness_still_returns_solver_input(self):
        self.entry()

        prepared = self.build()

        warning = next(
            issue for issue in prepared.issues
            if issue.code == "ROOM_CAPACITY_WARNING"
        )
        self.assertEqual(warning.severity, "WARNING")
        self.assertIsNotNone(prepared.solver_input)

    def test_candidate_warning_does_not_hide_zero_candidate_errors(self):
        candidate_counts = tuple(
            sorted(
                (
                    ((self.lecture_requirement.pk, 0), 0),
                    ((self.lecture_requirement.pk, 1), 0),
                    ((self.laboratory_requirement.pk, 0), 0),
                )
            )
        )
        warning_result = CandidateBuildResult(
            candidates=(),
            candidate_counts=candidate_counts,
            issues=(
                ReadinessIssue(
                    "CANDIDATE_NOTE",
                    "WARNING",
                    "Candidate preprocessing returned a nonblocking note.",
                ),
            ),
        )

        with patch(
            "timetabling.generation_inputs.build_candidates",
            return_value=warning_result,
        ):
            prepared = self.build()

        self.assertIn("CANDIDATE_NOTE", self.codes(prepared))
        self.assertEqual(
            sum(issue.code == "ZERO_CANDIDATES" for issue in prepared.issues),
            3,
        )
        self.assertIsNone(prepared.solver_input)

    def test_fill_gaps_and_replace_unlocked_partition_and_match_occurrences(self):
        locked = self.entry(start_time=time(8), end_time=time(9), is_locked=True)
        unlocked = self.entry(
            start_time=time(9),
            end_time=time(10),
            is_locked=False,
        )

        fill = self.build()
        replace = self.build(strategy="REPLACE_UNLOCKED")

        self.assertEqual(fill.retained_entry_ids, (locked.pk, unlocked.pk))
        self.assertEqual(fill.replace_entry_ids, ())
        self.assertNotIn(
            [self.lecture_requirement.pk, 0],
            fill.input_summary["remaining_demands"],
        )
        self.assertNotIn(
            [self.lecture_requirement.pk, 1],
            fill.input_summary["remaining_demands"],
        )
        self.assertEqual(replace.retained_entry_ids, (locked.pk,))
        self.assertEqual(replace.replace_entry_ids, (unlocked.pk,))
        self.assertNotIn(
            [self.lecture_requirement.pk, 0],
            replace.input_summary["remaining_demands"],
        )
        self.assertIn(
            [self.lecture_requirement.pk, 1],
            replace.input_summary["remaining_demands"],
        )

    def test_catalog_room_type_code_is_resolved_to_active_room_type_id(self):
        Subject.objects.filter(pk=self.offering.subject_id).update(
            required_room_type=self.room_type.code
        )

        prepared = self.build()

        self.assertIsNotNone(prepared.solver_input)
        demand = next(
            item for item in prepared.solver_input.demands
            if item.meeting_requirement_id == self.lecture_requirement.pk
        )
        self.assertEqual(demand.required_room_type_id, self.room_type.pk)
        self.assertTrue(demand.room_type_mandatory)

    def test_unknown_mandatory_catalog_room_type_rejects_uncategorized_room(self):
        Subject.objects.filter(pk=self.offering.subject_id).update(
            required_room_type="UNKNOWN-TYPE"
        )
        uncategorized = Room.objects.create(
            code="NO-TYPE",
            name="Uncategorized eligible room",
            capacity=100,
            owner_department=self.department,
        )

        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build()

        self.assertIn(uncategorized.pk, prepared.input_summary["eligible_room_ids"])
        self.assertIn("NO_ELIGIBLE_ROOM", self.codes(prepared))
        self.assertIsNone(prepared.solver_input)
        candidates.assert_not_called()

    def test_policy_drops_configured_weekday_absent_from_term(self):
        AcademicTerm.objects.filter(pk=self.term.pk).update(
            start_date=date(2026, 5, 31),
            end_date=date(2026, 5, 31),
        )
        self.configuration.allowed_weekdays = [1, 7]
        self.configuration.save()

        prepared = self.build()

        self.assertEqual(prepared.configuration_snapshot["allowed_weekdays"], [1, 7])
        self.assertEqual(prepared.solver_input.policy.allowed_weekdays, (7,))

    def test_off_grid_hard_and_preferred_availability_use_conservative_rounding(self):
        FacultyAvailability.objects.create(
            faculty=self.faculty,
            academic_term=self.term,
            day_of_week=1,
            start_time=time(8, 10),
            end_time=time(8, 40),
            availability_type=FacultyAvailability.Kind.UNAVAILABLE,
        )
        FacultyAvailability.objects.create(
            faculty=self.faculty,
            academic_term=self.term,
            day_of_week=2,
            start_time=time(8, 10),
            end_time=time(10, 20),
            availability_type=FacultyAvailability.Kind.PREFERRED,
        )

        prepared = self.build()

        lecture_candidates = [
            item for item in prepared.solver_input.candidates
            if item.demand_key == (self.lecture_requirement.pk, 0)
            and item.room_id == self.room.pk
        ]
        self.assertFalse(
            [item for item in lecture_candidates if item.day_of_week == 1 and item.start_slot in (0, 1)]
        )
        penalties = {
            item.start_slot: item.preferred_penalty
            for item in lecture_candidates
            if item.day_of_week == 2 and item.start_slot in (0, 1, 2, 3)
        }
        self.assertEqual(penalties, {0: 1, 1: 0, 2: 0, 3: 1})

    def test_off_grid_room_closure_blocks_every_overlapping_grid_slot(self):
        RoomUnavailability.objects.create(
            room=self.room,
            academic_term=self.term,
            day_of_week=1,
            start_time=time(8, 10),
            end_time=time(8, 40),
        )

        prepared = self.build()

        candidates = [
            item for item in prepared.solver_input.candidates
            if item.demand_key == (self.lecture_requirement.pk, 0)
            and item.day_of_week == 1
            and item.room_id == self.room.pk
        ]
        self.assertFalse([item for item in candidates if item.start_slot in (0, 1)])

    def test_protected_peer_constrains_input_without_snapshot_or_diagnostic_identity(self):
        peer = self.foreign_peer()
        self.entry(start_time=time(9), end_time=time(10))

        prepared = self.build()

        self.assertEqual(prepared.input_summary["peer_occupancy_count"], 1)
        self.assertNotIn("peer_entry_ids", prepared.input_summary)
        self.assertNotIn("peer_schedule_ids", prepared.input_summary)
        self.assertNotIn(self.foreign_schedule.name, str(prepared.issues))
        self.assertNotIn(
            self.offerings[self.external.pk].subject.code,
            str(prepared.issues),
        )
        self.assertTrue(
            any(
                fixed.assignment_id is None
                and not fixed.counts_for_distribution
                for fixed in (prepared.solver_input.fixed_meetings if prepared.solver_input else ())
            )
            or "ROOM_OVERLAP" in self.codes(prepared)
        )
        self.assertNotIn(peer.pk, prepared.retained_entry_ids)
        self.assertNotIn(peer.pk, prepared.replace_entry_ids)

    def test_peer_calendar_intersection_must_contain_entry_weekday(self):
        sunday_only = AcademicTerm.objects.create(
            academic_year=self.term.academic_year,
            semester=self.term.semester,
            code="SUN-ONLY",
            start_date=date(2026, 5, 31),
            end_date=date(2026, 5, 31),
        )
        peer_schedule = Schedule.objects.create(
            name="Sunday intersection",
            department=self.department,
            academic_term=sunday_only,
        )
        peer = self.entry(day_of_week=1)
        ScheduleEntry.objects.filter(pk=peer.pk).update(schedule=peer_schedule)

        prepared = self.build()

        self.assertEqual(prepared.input_summary["peer_occupancy_count"], 0)

    def test_forged_room_id_never_reaches_solver_dtos(self):
        entry = self.entry()
        foreign_room = self.records[self.external.pk]["rooms"]
        ScheduleEntry.objects.filter(pk=entry.pk).update(room=foreign_room)

        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build()

        self.assertNotIn(
            foreign_room.pk,
            prepared.input_summary["eligible_room_ids"],
        )
        self.assertIn("PROTECTED_ENTRY", self.codes(prepared))
        self.assertIsNone(prepared.solver_input)
        candidates.assert_not_called()

    def test_forged_assignment_id_never_reaches_solver_dtos(self):
        foreign_assignment = FacultySubjectAssignment.objects.create(
            faculty=self.records[self.external.pk]["faculty-management"],
            subject_offering=self.offerings[self.external.pk],
        )
        entry = self.entry()
        ScheduleEntry.objects.filter(pk=entry.pk).update(
            assignment=foreign_assignment
        )

        with patch("timetabling.generation_inputs.build_candidates") as candidates:
            prepared = self.build()

        self.assertNotIn(
            foreign_assignment.pk,
            prepared.input_summary["assignment_ids"],
        )
        self.assertIn("PROTECTED_ENTRY", self.codes(prepared))
        self.assertIsNone(prepared.solver_input)
        candidates.assert_not_called()

    @override_settings(
        SCHEDULER_MAX_CANDIDATES=1,
        SCHEDULER_MAX_SLOT_LITERALS=2_000_000,
        SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS=10,
    )
    def test_candidate_builder_limit_issue_is_preserved(self):
        prepared = self.build()

        self.assertIn("MODEL_SIZE_LIMIT", self.codes(prepared))
        self.assertIsNone(prepared.solver_input)


class GenerationRunScopeTests(TimetableFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sibling_schedule = Schedule.objects.create(
            academic_term=cls.term,
            department=cls.sibling,
            name="Sibling schedule",
        )
        cls.foreign_schedule = Schedule.objects.create(
            academic_term=cls.term,
            department=cls.external,
            name="Foreign schedule",
        )
        cls.local_run = cls.make_run(cls.schedule, cls.chair)
        cls.sibling_run = cls.make_run(cls.sibling_schedule, cls.dean)
        cls.foreign_run = cls.make_run(cls.foreign_schedule, cls.admin)

    @classmethod
    def make_run(cls, schedule, user):
        return ScheduleGenerationRun.objects.create(
            schedule=schedule,
            academic_term=schedule.academic_term,
            department=schedule.department,
            requested_by=user,
            strategy=ScheduleGenerationRun.Strategy.FILL_GAPS,
        )

    def test_run_queryset_uses_schedule_department_for_chair_dean_and_admin(self):
        ScheduleGenerationRun.objects.filter(pk=self.local_run.pk).update(
            department=self.external
        )

        self.assertEqual(
            set(generation_run_queryset(self.chair).values_list("pk", flat=True)),
            {self.local_run.pk},
        )
        self.assertEqual(
            set(generation_run_queryset(self.dean).values_list("pk", flat=True)),
            {self.local_run.pk, self.sibling_run.pk},
        )
        self.assertEqual(
            set(generation_run_queryset(self.admin).values_list("pk", flat=True)),
            {self.local_run.pk, self.sibling_run.pk, self.foreign_run.pk},
        )

    def test_get_generation_run_scopes_before_primary_key_lookup(self):
        with self.assertRaises(Http404):
            get_generation_run(self.chair, self.foreign_run.pk)
        self.assertEqual(
            get_generation_run(self.chair, self.local_run.pk).pk,
            self.local_run.pk,
        )
        with transaction.atomic():
            self.assertEqual(
                get_generation_run(self.chair, self.local_run.pk, lock=True).pk,
                self.local_run.pk,
            )


class DuplicateRequirementInputTests(TransactionTestCase):
    """Exercise defensive duplicate detection against imported/corrupt rows."""

    def setUp(self):
        college = College.objects.create(code="DUP", name="Duplicate College")
        self.department = Department.objects.create(
            college=college,
            code="DUP",
            name="Duplicate Department",
        )
        self.user = get_user_model().objects.create_user(username="duplicate-chair")
        AdminProfile.objects.create(
            user=self.user,
            role=AdminProfile.Role.DEPT_CHAIR,
            department=self.department,
        )
        category = EmploymentCategory.objects.create(
            code="duplicate-faculty",
            name="Duplicate faculty category",
        )
        faculty = Faculty.objects.create(
            employee_id="DUP-F",
            first_name="Duplicate",
            last_name="Teacher",
            home_department=self.department,
            employment_category=category,
        )
        room_type = RoomType.objects.create(code="duplicate-room", name="Duplicate room")
        Room.objects.create(
            code="DUP-R",
            name="Duplicate room",
            capacity=50,
            owner_department=self.department,
            category=room_type,
        )
        year = AcademicYear.objects.create(
            label="Duplicate year",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        semester = Semester.objects.create(code="DUP", name="Duplicate semester")
        self.term = AcademicTerm.objects.create(
            academic_year=year,
            semester=semester,
            code="DUP",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 5, 31),
        )
        subject = Subject.objects.create(
            code="DUP-S",
            title="Duplicate subject",
            units=Decimal("3"),
            lecture_units=Decimal("2"),
            laboratory_units=Decimal("1"),
            lecture_hours=Decimal("2"),
            laboratory_hours=Decimal("3"),
            owning_department=self.department,
        )
        offering = SubjectOffering.objects.create(
            subject=subject,
            academic_term=self.term,
            department=self.department,
            lecture_units=2,
            laboratory_units=1,
            lecture_hours=2,
            laboratory_hours=3,
        )
        section = ClassSection.objects.create(
            academic_term=self.term,
            department=self.department,
            code="DUP",
            expected_size=40,
        )
        OfferingRequirement.objects.create(
            subject_offering=offering,
            section=section,
        )
        self.assignment = FacultySubjectAssignment.objects.create(
            faculty=faculty,
            subject_offering=offering,
        )
        self.schedule = Schedule.objects.create(
            academic_term=self.term,
            department=self.department,
            name="Duplicate schedule",
        )
        SchedulingConfiguration.objects.create(
            academic_term=self.term,
            department=self.department,
            allowed_weekdays=[1, 2, 3, 4, 5],
            earliest_start=time(8),
            latest_end=time(17),
            slot_increment_minutes=30,
            solver_time_limit_seconds=10,
            random_seed=17,
            worker_count=1,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="laboratory",
            meetings_per_week=1,
            duration_minutes=180,
        )

    def test_duplicate_meeting_requirement_is_rejected_defensively(self):
        table = AssignmentMeetingRequirement._meta.db_table
        constraint = "assignment_meeting_type_unique"
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT "{constraint}"')
        duplicate = AssignmentMeetingRequirement.objects.bulk_create(
            [
                AssignmentMeetingRequirement(
                    assignment=self.assignment,
                    meeting_type="lecture",
                    meetings_per_week=2,
                    duration_minutes=60,
                )
            ]
        )[0]
        try:
            prepared = prepare_generation_input(
                user=self.user,
                schedule_id=self.schedule.pk,
                strategy="FILL_GAPS",
                overrides=GenerationOverrides(),
            )
        finally:
            AssignmentMeetingRequirement.objects.filter(pk=duplicate.pk).delete()
            with connection.cursor() as cursor:
                cursor.execute(
                    f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" '
                    "UNIQUE (assignment_id, meeting_type)"
                )

        self.assertIn(
            "MEETING_REQUIREMENT_DUPLICATE",
            {issue.code for issue in prepared.issues},
        )
        self.assertIsNone(prepared.solver_input)

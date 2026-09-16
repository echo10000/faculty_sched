from dataclasses import FrozenInstanceError
from importlib import reload
from unittest.mock import patch

from django.test import SimpleTestCase

from timetabling.solver import candidates, contracts
from timetabling.solver.candidates import build_candidates
from timetabling.solver.contracts import (
    CandidateBuildResult,
    CandidateLimits,
    CandidatePlacement,
    FixedMeeting,
    MeetingDemand,
    ObjectiveWeights,
    PenaltyBreakdown,
    ProposedMeeting,
    ReadinessIssue,
    RoomOption,
    SchedulingPolicy,
    SolverInput,
    SolverResult,
    SolverStatistics,
    WeeklyBlock,
)


class CandidateBuilderTests(SimpleTestCase):
    demand_key = (30, 0)

    def policy(self, **changes):
        values = {
            "earliest_minute": 8 * 60,
            "latest_minute": 10 * 60,
            "slot_increment_minutes": 30,
            "allowed_weekdays": (1,),
            "solver_time_limit_seconds": 10,
            "random_seed": 17,
            "worker_count": 1,
            "weights": ObjectiveWeights(),
        }
        values.update(changes)
        return SchedulingPolicy(**values)

    def demand(self, **changes):
        values = {
            "assignment_id": 20,
            "meeting_requirement_id": self.demand_key[0],
            "occurrence_index": self.demand_key[1],
            "faculty_id": 10,
            "section_id": 40,
            "meeting_type": "lecture",
            "duration_slots": 2,
            "expected_size": 20,
            "required_room_type_id": 5,
            "room_type_mandatory": False,
            "capacity_is_hard": False,
        }
        values.update(changes)
        return MeetingDemand(**values)

    def room(self, room_id, **changes):
        values = {"room_id": room_id, "room_type_id": 5, "capacity": 30}
        values.update(changes)
        return RoomOption(**values)

    def fixed(self, **changes):
        values = {
            "assignment_id": 90,
            "faculty_id": 91,
            "section_id": 92,
            "room_id": 93,
            "day_of_week": 1,
            "start_slot": 1,
            "end_slot": 2,
            "meeting_type": "lecture",
            "counts_for_distribution": False,
        }
        values.update(changes)
        return FixedMeeting(**values)

    def build(self, **changes):
        values = {
            "policy": self.policy(),
            "demands": (self.demand(),),
            "rooms": (self.room(1),),
            "unavailable": (),
            "room_closures": (),
            "fixed_meetings": (),
            "preferred": (),
            "limits": CandidateLimits(10, 100_000, 2_000_000),
        }
        values.update(changes)
        return build_candidates(**values)

    def test_candidates_are_sparse_ordered_and_may_end_at_latest_end(self):
        result = self.build(rooms=(self.room(2), self.room(1)))

        self.assertEqual(
            [(item.start_slot, item.end_slot, item.room_id) for item in result.candidates],
            [
                (0, 2, 1),
                (0, 2, 2),
                (1, 3, 1),
                (1, 3, 2),
                (2, 4, 1),
                (2, 4, 2),
            ],
        )
        self.assertEqual(result.candidate_counts, {self.demand_key: 6})
        self.assertEqual(result.issues, ())

    def test_demand_and_weekday_input_order_does_not_change_candidate_order(self):
        later = self.demand(
            assignment_id=21,
            meeting_requirement_id=31,
            occurrence_index=1,
            duration_slots=4,
        )
        earlier = self.demand(
            assignment_id=19,
            meeting_requirement_id=29,
            duration_slots=4,
        )

        result = self.build(
            policy=self.policy(allowed_weekdays=(2, 1)),
            demands=(later, earlier),
        )

        self.assertEqual(
            [(item.demand_key, item.day_of_week) for item in result.candidates],
            [((29, 0), 1), ((29, 0), 2), ((31, 1), 1), ((31, 1), 2)],
        )

    def test_unavailable_intervals_are_half_open_and_adjacency_is_legal(self):
        result = self.build(unavailable=(WeeklyBlock(10, 1, 0, 2),))

        self.assertEqual(
            [(item.start_slot, item.end_slot) for item in result.candidates],
            [(2, 4)],
        )
        self.assertEqual(result.candidate_counts[self.demand_key], 1)

    def test_room_closures_remove_only_matching_room_day_overlaps(self):
        result = self.build(
            policy=self.policy(allowed_weekdays=(1, 2)),
            rooms=(self.room(1), self.room(2)),
            room_closures=(
                WeeklyBlock(1, 1, 1, 3),
                WeeklyBlock(2, 3, 0, 4),
                WeeklyBlock(999, 1, 0, 4),
            ),
        )

        self.assertEqual(
            [(item.day_of_week, item.start_slot, item.room_id) for item in result.candidates],
            [
                (1, 0, 2),
                (1, 1, 2),
                (1, 2, 2),
                (2, 0, 1),
                (2, 0, 2),
                (2, 1, 1),
                (2, 1, 2),
                (2, 2, 1),
                (2, 2, 2),
            ],
        )

    def test_fixed_faculty_room_and_section_occupancy_each_remove_overlaps(self):
        collisions = {
            "faculty": self.fixed(faculty_id=10),
            "room": self.fixed(room_id=1),
            "section": self.fixed(section_id=40),
        }

        for dimension, fixed in collisions.items():
            with self.subTest(dimension=dimension):
                result = self.build(
                    demands=(self.demand(duration_slots=1),),
                    fixed_meetings=(fixed,),
                )
                self.assertEqual(
                    [item.start_slot for item in result.candidates],
                    [0, 2, 3],
                )

    def test_mandatory_room_type_and_hard_capacity_are_hard_filters(self):
        result = self.build(
            demands=(
                self.demand(room_type_mandatory=True, capacity_is_hard=True),
            ),
            rooms=(
                self.room(1, room_type_id=6, capacity=30),
                self.room(2, room_type_id=5, capacity=19),
                self.room(3, room_type_id=5, capacity=20),
            ),
        )

        self.assertEqual({item.room_id for item in result.candidates}, {3})

    def test_preference_and_room_fit_penalties_follow_local_rules(self):
        result = self.build(
            rooms=(
                self.room(1, room_type_id=6, capacity=50),
                self.room(2, room_type_id=5, capacity=18),
            ),
            preferred=(WeeklyBlock(10, 1, 1, 3),),
        )

        penalties = {
            (item.start_slot, item.room_id): (
                item.preferred_penalty,
                item.room_fit_penalty,
            )
            for item in result.candidates
        }
        self.assertEqual(
            penalties,
            {
                (0, 1): (1, 31),
                (0, 2): (1, 0),
                (1, 1): (0, 31),
                (1, 2): (0, 0),
                (2, 1): (1, 31),
                (2, 2): (1, 0),
            },
        )

    def test_missing_preference_or_expected_size_adds_no_penalty(self):
        result = self.build(
            demands=(self.demand(expected_size=None, required_room_type_id=None),),
            rooms=(self.room(1, room_type_id=None, capacity=200),),
        )

        self.assertEqual(
            {(item.preferred_penalty, item.room_fit_penalty) for item in result.candidates},
            {(0, 0)},
        )

    def test_demand_that_cannot_fit_has_a_zero_candidate_count(self):
        result = self.build(demands=(self.demand(duration_slots=5),))

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.candidate_counts, {self.demand_key: 0})
        self.assertEqual(result.issues, ())

    def test_candidate_count_cap_discards_partial_expansion(self):
        result = self.build(limits=CandidateLimits(10, 1, 100))

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.issues,
            (
                ReadinessIssue(
                    "MODEL_SIZE_LIMIT",
                    "ERROR",
                    "Candidate model exceeds the configured deployment limit.",
                ),
            ),
        )

    def test_slot_literal_cap_discards_partial_expansion(self):
        result = self.build(limits=CandidateLimits(10, 100, 1))

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.issues[0].code, "MODEL_SIZE_LIMIT")

    def test_elapsed_cap_uses_the_injected_monotonic_clock(self):
        readings = iter((100.0, 101.1))

        result = self.build(
            clock=lambda: next(readings),
            limits=CandidateLimits(1, 100, 100),
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.issues,
            (
                ReadinessIssue(
                    "PREPROCESSING_TIMEOUT",
                    "ERROR",
                    "Candidate preprocessing exceeded its configured time limit.",
                ),
            ),
        )

    def test_contracts_are_frozen_slotted_dataclasses(self):
        contract_types = (
            ObjectiveWeights,
            SchedulingPolicy,
            MeetingDemand,
            FixedMeeting,
            RoomOption,
            WeeklyBlock,
            CandidatePlacement,
            CandidateLimits,
            CandidateBuildResult,
            SolverInput,
            ProposedMeeting,
            ReadinessIssue,
            PenaltyBreakdown,
            SolverStatistics,
            SolverResult,
        )
        for contract_type in contract_types:
            with self.subTest(contract=contract_type.__name__):
                self.assertTrue(contract_type.__dataclass_params__.frozen)
                self.assertIn("__slots__", contract_type.__dict__)

        demand = self.demand()
        with self.assertRaises(FrozenInstanceError):
            demand.duration_slots = 3

    def test_solver_modules_import_without_django(self):
        real_import = __import__

        def reject_django(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "django" or name.startswith("django."):
                raise AssertionError(f"unexpected Django import: {name}")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=reject_django):
            reload(contracts)
            reload(candidates)

from dataclasses import asdict
from importlib import reload
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from ortools.sat.python import cp_model

from timetabling.solver import engine
from timetabling.solver.contracts import (
    CandidatePlacement,
    FixedMeeting,
    MeetingDemand,
    ObjectiveWeights,
    SchedulingPolicy,
    SolverInput,
)
from timetabling.solver.engine import solve


class FakeSolver:
    def __init__(self, status, *, selected_indexes=()):
        self.status = status
        self.selected_indexes = set(selected_indexes)
        self.parameters = SimpleNamespace()
        self.wall_time = 1.25
        self.num_branches = 7
        self.num_conflicts = 3
        self.best_objective_bound = 0.0
        self.value_reads = 0

    def solve(self, model):
        self.model = model
        return self.status

    def boolean_value(self, variable):
        index = self.value_reads
        self.value_reads += 1
        return index in self.selected_indexes


class HardConstraintSolverTests(SimpleTestCase):
    def policy(self, **changes):
        values = {
            "earliest_minute": 8 * 60,
            "latest_minute": 12 * 60,
            "slot_increment_minutes": 30,
            "allowed_weekdays": (1, 2),
            "solver_time_limit_seconds": 9,
            "random_seed": 17,
            "worker_count": 1,
            "weights": ObjectiveWeights(),
        }
        values.update(changes)
        return SchedulingPolicy(**values)

    def demand(self, **changes):
        values = {
            "assignment_id": 100,
            "meeting_requirement_id": 200,
            "occurrence_index": 0,
            "faculty_id": 300,
            "section_id": 400,
            "meeting_type": "lecture",
            "duration_slots": 2,
            "expected_size": 20,
            "required_room_type_id": None,
            "room_type_mandatory": False,
            "capacity_is_hard": False,
        }
        values.update(changes)
        return MeetingDemand(**values)

    def candidate(self, demand, **changes):
        values = {
            "demand_key": demand.key,
            "assignment_id": demand.assignment_id,
            "faculty_id": demand.faculty_id,
            "section_id": demand.section_id,
            "room_id": 500,
            "day_of_week": 1,
            "start_slot": 0,
            "end_slot": 2,
            "meeting_type": demand.meeting_type,
            "preferred_penalty": 0,
            "room_fit_penalty": 0,
        }
        values.update(changes)
        return CandidatePlacement(**values)

    def solver_input(
        self,
        *,
        demands=(),
        candidates=(),
        fixed_meetings=(),
        candidate_counts=None,
        policy=None,
    ):
        if candidate_counts is None:
            candidate_counts = tuple(
                (
                    demand.key,
                    sum(
                        candidate.demand_key == demand.key
                        for candidate in candidates
                    ),
                )
                for demand in demands
            )
        return SolverInput(
            policy=policy or self.policy(),
            demands=tuple(demands),
            candidates=tuple(candidates),
            fixed_meetings=tuple(fixed_meetings),
            candidate_counts=candidate_counts,
        )

    def solve_with_fake(self, data, status, *, selected_indexes=()):
        fake = FakeSolver(status, selected_indexes=selected_indexes)
        with patch.object(engine.cp_model, "CpSolver", return_value=fake):
            result = solve(data)
        return result, fake

    def demand_identity_input(self):
        first = self.demand()
        second = self.demand(
            occurrence_index=1,
            faculty_id=301,
            section_id=401,
        )
        third = self.demand(
            meeting_requirement_id=201,
            faculty_id=302,
            section_id=402,
        )
        return self.solver_input(
            demands=(first, second, third),
            candidates=(
                self.candidate(first, room_id=500, start_slot=0, end_slot=2),
                self.candidate(first, room_id=501, start_slot=2, end_slot=4),
                self.candidate(second, room_id=502, start_slot=0, end_slot=2),
                self.candidate(second, room_id=503, start_slot=2, end_slot=4),
                self.candidate(third, room_id=504, start_slot=0, end_slot=2),
                self.candidate(third, room_id=505, start_slot=2, end_slot=4),
            ),
        )

    def test_solver_selects_exactly_one_candidate_for_each_demand(self):
        data = self.demand_identity_input()

        result = solve(data)

        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(len(result.proposals), 3)
        self.assertEqual(
            {proposal.demand_key for proposal in result.proposals},
            {demand.key for demand in data.demands},
        )

    def test_faculty_room_and_section_slot_buckets_cannot_overlap(self):
        cases = {
            "faculty": (
                self.demand(faculty_id=300, section_id=400),
                self.demand(
                    assignment_id=101,
                    meeting_requirement_id=201,
                    faculty_id=300,
                    section_id=401,
                ),
                500,
                501,
            ),
            "room": (
                self.demand(faculty_id=300, section_id=400),
                self.demand(
                    assignment_id=101,
                    meeting_requirement_id=201,
                    faculty_id=301,
                    section_id=401,
                ),
                500,
                500,
            ),
            "section": (
                self.demand(faculty_id=300, section_id=400),
                self.demand(
                    assignment_id=101,
                    meeting_requirement_id=201,
                    faculty_id=301,
                    section_id=400,
                ),
                500,
                501,
            ),
        }

        for dimension, (first, second, first_room, second_room) in cases.items():
            with self.subTest(dimension=dimension):
                data = self.solver_input(
                    demands=(first, second),
                    candidates=(
                        self.candidate(
                            first,
                            room_id=first_room,
                            start_slot=0,
                            end_slot=2,
                        ),
                        self.candidate(
                            second,
                            room_id=second_room,
                            start_slot=1,
                            end_slot=3,
                        ),
                    ),
                )

                result = solve(data)

                self.assertEqual(result.raw_status, "INFEASIBLE")
                self.assertEqual(result.proposals, ())

    def test_adjacent_multi_slot_candidates_are_legal(self):
        first = self.demand()
        second = self.demand(
            assignment_id=101,
            meeting_requirement_id=201,
        )
        data = self.solver_input(
            demands=(first, second),
            candidates=(
                self.candidate(first, start_slot=0, end_slot=2),
                self.candidate(second, start_slot=2, end_slot=4),
            ),
        )

        result = solve(data)

        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(len(result.proposals), 2)

    def test_equal_slots_on_different_days_are_legal(self):
        first = self.demand()
        second = self.demand(
            assignment_id=101,
            meeting_requirement_id=201,
        )
        data = self.solver_input(
            demands=(first, second),
            candidates=(
                self.candidate(first, day_of_week=1),
                self.candidate(second, day_of_week=2),
            ),
        )

        result = solve(data)

        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})

    def test_equal_day_slots_with_distinct_resources_are_legal(self):
        first = self.demand()
        second = self.demand(
            assignment_id=101,
            meeting_requirement_id=201,
            faculty_id=301,
            section_id=401,
        )
        data = self.solver_input(
            demands=(first, second),
            candidates=(
                self.candidate(first, room_id=500),
                self.candidate(second, room_id=501),
            ),
        )

        result = solve(data)

        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})

    def test_demand_without_candidates_is_infeasible(self):
        demand = self.demand()

        result = solve(self.solver_input(demands=(demand,)))

        self.assertEqual(result.raw_status, "INFEASIBLE")
        self.assertEqual(result.proposals, ())

    def test_empty_input_is_optimal_with_no_proposals(self):
        result = solve(self.solver_input())

        self.assertEqual(result.raw_status, "OPTIMAL")
        self.assertEqual(result.proposals, ())
        self.assertEqual(result.statistics.generated_meeting_count, 0)

    def test_fixed_meetings_are_scored_after_candidate_prefiltering(self):
        demand = self.demand()
        candidate = self.candidate(demand, start_slot=2, end_slot=4)
        fixed = FixedMeeting(
            assignment_id=999,
            faculty_id=candidate.faculty_id,
            section_id=candidate.section_id,
            room_id=candidate.room_id,
            day_of_week=candidate.day_of_week,
            start_slot=0,
            end_slot=2,
            meeting_type="lecture",
            counts_for_distribution=False,
        )

        result = solve(
            self.solver_input(
                demands=(demand,),
                candidates=(candidate,),
                fixed_meetings=(fixed,),
            )
        )

        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(result.statistics.variable_count, 13)
        self.assertEqual(result.penalties.faculty_gap, 0)
        self.assertEqual(result.penalties.section_gap, 0)

    def test_maps_each_cp_sat_status_without_collapsing_it(self):
        demand = self.demand()
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand),),
        )
        statuses = (
            (cp_model.OPTIMAL, "OPTIMAL", 1),
            (cp_model.FEASIBLE, "FEASIBLE", 1),
            (cp_model.INFEASIBLE, "INFEASIBLE", 0),
            (cp_model.MODEL_INVALID, "MODEL_INVALID", 0),
            (cp_model.UNKNOWN, "UNKNOWN", 0),
        )

        for solver_status, expected, proposal_count in statuses:
            with self.subTest(status=expected):
                result, _ = self.solve_with_fake(
                    data,
                    solver_status,
                    selected_indexes=(0,),
                )

                self.assertEqual(result.raw_status, expected)
                self.assertEqual(len(result.proposals), proposal_count)

    def test_non_success_statuses_never_read_solution_values(self):
        demand = self.demand()
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand),),
        )

        for status in (cp_model.INFEASIBLE, cp_model.MODEL_INVALID, cp_model.UNKNOWN):
            with self.subTest(status=status):
                result, fake = self.solve_with_fake(
                    data,
                    status,
                    selected_indexes=(0,),
                )

                self.assertEqual(result.proposals, ())
                self.assertIsNone(result.objective_value)
                self.assertIsNone(result.best_bound)
                self.assertEqual(
                    asdict(result.penalties),
                    {
                        "faculty_preference": 0,
                        "faculty_gap": 0,
                        "section_gap": 0,
                        "meeting_distribution": 0,
                        "room_fit": 0,
                    },
                )
                self.assertEqual(fake.value_reads, 0)

    def test_success_reconstructs_every_proposed_meeting_in_candidate_order(self):
        first = self.demand()
        second = self.demand(
            assignment_id=101,
            meeting_requirement_id=201,
            occurrence_index=1,
            faculty_id=301,
            section_id=401,
            meeting_type="laboratory",
        )
        data = self.solver_input(
            demands=(first, second),
            candidates=(
                self.candidate(first, room_id=500, start_slot=0, end_slot=2),
                self.candidate(first, room_id=501, day_of_week=2, start_slot=2, end_slot=4),
                self.candidate(second, room_id=502, day_of_week=2, start_slot=4, end_slot=6),
            ),
        )

        result, _ = self.solve_with_fake(
            data,
            cp_model.FEASIBLE,
            selected_indexes=(1, 2),
        )

        self.assertEqual(
            tuple(
                (
                    proposal.assignment_id,
                    proposal.meeting_requirement_id,
                    proposal.occurrence_index,
                    proposal.room_id,
                    proposal.day_of_week,
                    proposal.start_slot,
                    proposal.end_slot,
                    proposal.meeting_type,
                )
                for proposal in result.proposals
            ),
            (
                (100, 200, 0, 501, 2, 2, 4, "lecture"),
                (101, 201, 1, 502, 2, 4, 6, "laboratory"),
            ),
        )

    def test_success_rejects_an_incomplete_proposal_set(self):
        data = self.demand_identity_input()

        with self.assertRaisesRegex(RuntimeError, "complete proposal"):
            self.solve_with_fake(
                data,
                cp_model.FEASIBLE,
                selected_indexes=(0,),
            )

    def test_solver_parameters_and_approved_statistics_are_reported(self):
        demand = self.demand()
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand),),
            policy=self.policy(
                solver_time_limit_seconds=13,
                random_seed=29,
                worker_count=4,
            ),
        )

        result, fake = self.solve_with_fake(
            data,
            cp_model.OPTIMAL,
            selected_indexes=(0,),
        )

        self.assertEqual(fake.parameters.max_time_in_seconds, 13)
        self.assertEqual(fake.parameters.random_seed, 29)
        self.assertEqual(fake.parameters.num_search_workers, 4)
        self.assertEqual(
            asdict(result.statistics),
            {
                "wall_time_seconds": 1.25,
                "branches": 7,
                "conflicts": 3,
                "candidate_count": 1,
                "variable_count": 13,
                "generated_meeting_count": 1,
                "random_seed": 29,
                "worker_count": 4,
                "time_limit_seconds": 13,
            },
        )
        self.assertEqual(result.objective_value, 0)
        self.assertEqual(result.best_bound, 0.0)
        self.assertEqual(
            asdict(result.penalties),
            {
                "faculty_preference": 0,
                "faculty_gap": 0,
                "section_gap": 0,
                "meeting_distribution": 0,
                "room_fit": 0,
            },
        )
        self.assertEqual(result.issues, ())

    def test_hard_model_uses_candidates_instead_of_diagnostic_counts(self):
        demand = self.demand()
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand),),
            candidate_counts=((demand.key, 0),),
        )

        result = solve(data)

        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})

    def test_engine_imports_without_django(self):
        real_import = __import__

        def reject_django(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "django" or name.startswith("django."):
                raise AssertionError(f"unexpected Django import: {name}")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=reject_django):
            reload(engine)

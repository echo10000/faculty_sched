from dataclasses import asdict
from importlib import import_module, reload
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


class SolverInputFactory:
    def policy(self, weights=None):
        return SchedulingPolicy(
            earliest_minute=8 * 60,
            latest_minute=12 * 60,
            slot_increment_minutes=30,
            allowed_weekdays=(1, 2),
            solver_time_limit_seconds=10,
            random_seed=37,
            worker_count=1,
            weights=weights or ObjectiveWeights(),
        )

    def demand(self, **changes):
        values = {
            "assignment_id": 100,
            "meeting_requirement_id": 200,
            "occurrence_index": 0,
            "faculty_id": 300,
            "section_id": 400,
            "meeting_type": "lecture",
            "duration_slots": 1,
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
            "end_slot": 1,
            "meeting_type": demand.meeting_type,
            "preferred_penalty": 0,
            "room_fit_penalty": 0,
        }
        values.update(changes)
        return CandidatePlacement(**values)

    def fixed(self, **changes):
        values = {
            "assignment_id": 100,
            "faculty_id": 300,
            "section_id": 400,
            "room_id": 501,
            "day_of_week": 1,
            "start_slot": 0,
            "end_slot": 1,
            "meeting_type": "lecture",
            "counts_for_distribution": True,
        }
        values.update(changes)
        return FixedMeeting(**values)

    def solver_input(
        self,
        *,
        demands=(),
        candidates=(),
        fixed_meetings=(),
        weights=None,
        candidate_counts=None,
    ):
        if candidate_counts is None:
            candidate_counts = tuple(
                (
                    demand.key,
                    sum(candidate.demand_key == demand.key for candidate in candidates),
                )
                for demand in demands
            )
        return SolverInput(
            policy=self.policy(weights),
            demands=tuple(demands),
            candidates=tuple(candidates),
            fixed_meetings=tuple(fixed_meetings),
            candidate_counts=candidate_counts,
        )

    def known_penalty_input(self, weights=None):
        demand = self.demand()
        return self.solver_input(
            demands=(demand,),
            candidates=(
                self.candidate(
                    demand,
                    start_slot=2,
                    end_slot=3,
                    preferred_penalty=3,
                    room_fit_penalty=11,
                ),
            ),
            fixed_meetings=(self.fixed(),),
            weights=weights,
        )


class ObjectiveSolverTests(SolverInputFactory, SimpleTestCase):

    def test_preference_weight_moves_meeting_into_preferred_period(self):
        demand = self.demand()
        candidates = (
            self.candidate(demand, preferred_penalty=1, room_fit_penalty=0),
            self.candidate(
                demand,
                room_id=501,
                day_of_week=2,
                preferred_penalty=0,
                room_fit_penalty=1,
            ),
        )

        room_only = solve(
            self.solver_input(
                demands=(demand,),
                candidates=candidates,
                weights=ObjectiveWeights(room_fit=1),
            )
        )
        preference = solve(
            self.solver_input(
                demands=(demand,),
                candidates=candidates,
                weights=ObjectiveWeights(faculty_preference=2, room_fit=1),
            )
        )

        self.assertEqual(room_only.penalties.faculty_preference, 1)
        self.assertEqual(preference.penalties.faculty_preference, 0)
        self.assertEqual(room_only.proposals[0].day_of_week, 1)
        self.assertEqual(preference.proposals[0].day_of_week, 2)

    def test_faculty_without_preferred_records_has_zero_preference_penalty(self):
        demand = self.demand()
        result = solve(
            self.solver_input(
                demands=(demand,),
                candidates=(
                    self.candidate(demand, room_fit_penalty=11),
                    self.candidate(
                        demand,
                        room_id=501,
                        day_of_week=2,
                        room_fit_penalty=0,
                    ),
                ),
                weights=ObjectiveWeights(faculty_preference=101, room_fit=1),
            )
        )

        self.assertEqual(result.penalties.faculty_preference, 0)
        self.assertEqual(result.penalties.room_fit, 0)
        self.assertEqual(result.proposals[0].day_of_week, 2)

    def test_gap_distribution_and_room_fit_components_are_exact(self):
        result = solve(
            self.known_penalty_input(
                ObjectiveWeights(
                    faculty_preference=2,
                    faculty_gap=3,
                    section_gap=5,
                    meeting_distribution=7,
                    room_fit=11,
                )
            )
        )

        self.assertEqual(
            asdict(result.penalties),
            {
                "faculty_preference": 3,
                "faculty_gap": 1,
                "section_gap": 1,
                "meeting_distribution": 1,
                "room_fit": 11,
            },
        )
        self.assertEqual(result.objective_value, 142)

    def test_fixed_multi_slot_occupancy_counts_every_occupied_slot(self):
        demand = self.demand()
        cases = ((2, 0), (3, 1))

        for candidate_start, expected_gap in cases:
            with self.subTest(candidate_start=candidate_start):
                result = solve(
                    self.solver_input(
                        demands=(demand,),
                        candidates=(
                            self.candidate(
                                demand,
                                start_slot=candidate_start,
                                end_slot=candidate_start + 1,
                            ),
                        ),
                        fixed_meetings=(self.fixed(start_slot=0, end_slot=2),),
                    )
                )

                self.assertEqual(result.penalties.faculty_gap, expected_gap)
                self.assertEqual(result.penalties.section_gap, expected_gap)

    def test_peer_occupancy_outside_window_affects_gaps_not_distribution(self):
        demand = self.demand()
        result = solve(
            self.solver_input(
                demands=(demand,),
                candidates=(self.candidate(demand, start_slot=2, end_slot=3),),
                fixed_meetings=(
                    self.fixed(
                        assignment_id=None,
                        start_slot=-2,
                        end_slot=0,
                        meeting_type=None,
                        counts_for_distribution=False,
                    ),
                ),
            )
        )

        self.assertEqual(result.penalties.faculty_gap, 2)
        self.assertEqual(result.penalties.section_gap, 2)
        self.assertEqual(result.penalties.meeting_distribution, 0)

    def test_faculty_and_section_gap_groupings_are_independent(self):
        demand = self.demand()
        result = solve(
            self.solver_input(
                demands=(demand,),
                candidates=(self.candidate(demand, start_slot=2, end_slot=3),),
                fixed_meetings=(
                    self.fixed(
                        assignment_id=None,
                        section_id=401,
                        start_slot=0,
                        end_slot=1,
                        meeting_type=None,
                        counts_for_distribution=False,
                    ),
                    self.fixed(
                        assignment_id=None,
                        faculty_id=301,
                        room_id=502,
                        start_slot=4,
                        end_slot=5,
                        meeting_type=None,
                        counts_for_distribution=False,
                    ),
                ),
            )
        )

        self.assertEqual(result.penalties.faculty_gap, 1)
        self.assertEqual(result.penalties.section_gap, 1)

    def test_distribution_weight_chooses_an_unused_day_and_excludes_peer(self):
        demand = self.demand(occurrence_index=1)
        candidates = (
            self.candidate(demand, day_of_week=1, start_slot=1, end_slot=2),
            self.candidate(demand, room_id=502, day_of_week=2),
        )
        retained = self.fixed()
        peer = self.fixed(
            assignment_id=None,
            faculty_id=301,
            section_id=401,
            room_id=503,
            meeting_type=None,
            counts_for_distribution=False,
        )

        chosen = solve(
            self.solver_input(
                demands=(demand,),
                candidates=candidates,
                fixed_meetings=(retained, peer),
                weights=ObjectiveWeights(meeting_distribution=1),
            )
        )
        forced = solve(
            self.solver_input(
                demands=(demand,),
                candidates=(candidates[0],),
                fixed_meetings=(retained, peer),
                weights=ObjectiveWeights(meeting_distribution=1),
            )
        )

        self.assertEqual(chosen.proposals[0].day_of_week, 2)
        self.assertEqual(chosen.penalties.meeting_distribution, 0)
        self.assertEqual(forced.penalties.meeting_distribution, 1)

    def test_room_fit_weight_selects_the_exact_lower_cost(self):
        demand = self.demand()
        result = solve(
            self.solver_input(
                demands=(demand,),
                candidates=(
                    self.candidate(demand, room_fit_penalty=11),
                    self.candidate(
                        demand,
                        room_id=501,
                        day_of_week=2,
                        room_fit_penalty=0,
                    ),
                ),
                weights=ObjectiveWeights(room_fit=1),
            )
        )

        self.assertEqual(result.proposals[0].room_id, 501)
        self.assertEqual(result.penalties.room_fit, 0)

    def test_zero_weights_keep_unweighted_components_but_remove_total_cost(self):
        result = solve(self.known_penalty_input(ObjectiveWeights()))

        self.assertEqual(
            asdict(result.penalties),
            {
                "faculty_preference": 3,
                "faculty_gap": 1,
                "section_gap": 1,
                "meeting_distribution": 1,
                "room_fit": 11,
            },
        )
        self.assertEqual(result.objective_value, 0)
        self.assertEqual(result.best_bound, 0.0)

    def test_safe_objective_boundary_is_computed_with_python_integers(self):
        demand = self.demand()
        safe_limit = cp_model.INT_MAX // 2
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand, room_fit_penalty=1),),
            weights=ObjectiveWeights(room_fit=safe_limit),
        )

        bounds = engine._objective_bounds(data)

        self.assertEqual(bounds.room_fit, 1)
        self.assertEqual(bounds.weighted_upper, safe_limit)

    def test_invalid_objective_coefficients_are_rejected_before_modeling(self):
        demand = self.demand()
        invalid_inputs = (
            self.solver_input(
                demands=(demand,),
                candidates=(self.candidate(demand, preferred_penalty=-1),),
            ),
            self.solver_input(
                demands=(demand,),
                candidates=(
                    self.candidate(demand, room_fit_penalty=cp_model.INT_MAX + 1),
                ),
            ),
            self.solver_input(
                demands=(demand,),
                candidates=(self.candidate(demand),),
                weights=ObjectiveWeights(faculty_gap=-1),
            ),
        )

        for data in invalid_inputs:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    solve(data)

    def test_retained_distribution_requires_explicit_identity(self):
        demand = self.demand()
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand, start_slot=2, end_slot=3),),
            fixed_meetings=(self.fixed(assignment_id=None),),
        )

        with self.assertRaises(ValueError):
            solve(data)

    def test_candidate_overlapping_fixed_occupancy_is_rejected(self):
        demand = self.demand()
        data = self.solver_input(
            demands=(demand,),
            candidates=(self.candidate(demand),),
            fixed_meetings=(self.fixed(),),
        )

        with self.assertRaises(ValueError):
            solve(data)

    def test_solver_component_totals_match_reconstructed_breakdown(self):
        data = self.known_penalty_input(
            ObjectiveWeights(
                faculty_preference=2,
                faculty_gap=3,
                section_gap=5,
                meeting_distribution=7,
                room_fit=11,
            )
        )
        model, _candidate_variables, totals = engine._build_model(data)
        solver = cp_model.CpSolver()
        solver.parameters.random_seed = data.policy.random_seed
        solver.parameters.num_search_workers = data.policy.worker_count

        status = solver.solve(model)
        result = solve(data)

        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(
            asdict(result.penalties),
            {
                "faculty_preference": solver.value(totals.faculty_preference),
                "faculty_gap": solver.value(totals.faculty_gap),
                "section_gap": solver.value(totals.section_gap),
                "meeting_distribution": solver.value(totals.meeting_distribution),
                "room_fit": solver.value(totals.room_fit),
            },
        )


class DiagnosticTests(SolverInputFactory, SimpleTestCase):
    def diagnostics(self):
        return import_module("timetabling.solver.diagnostics")

    def issue_codes(self, data, raw_status="INFEASIBLE"):
        return tuple(
            issue.code
            for issue in self.diagnostics().diagnose_unsolved(data, raw_status)
        )

    def test_status_issues_use_exact_messages_and_reject_success(self):
        data = self.solver_input()
        expected = {
            "INFEASIBLE": (
                "SOLVER_INFEASIBLE",
                "The solver proved that no complete timetable satisfies all hard constraints.",
            ),
            "MODEL_INVALID": (
                "SOLVER_MODEL_INVALID",
                "The solver rejected the generated model. Review the scheduling configuration and model diagnostics.",
            ),
            "UNKNOWN": (
                "SOLVER_UNKNOWN",
                "No complete solution was returned within the configured solver time bound.",
            ),
        }

        for raw_status, (code, message) in expected.items():
            with self.subTest(raw_status=raw_status):
                issues = self.diagnostics().diagnose_unsolved(data, raw_status)
                self.assertEqual(len(issues), 1)
                self.assertEqual(asdict(issues[0]), {
                    "code": code,
                    "severity": "ERROR",
                    "message": message,
                })

        for raw_status in ("OPTIMAL", "FEASIBLE", "NOT_A_STATUS"):
            with self.subTest(raw_status=raw_status):
                with self.assertRaises(ValueError):
                    self.diagnostics().diagnose_unsolved(data, raw_status)

    def all_finding_input(self):
        missing = self.demand(
            assignment_id=1,
            meeting_requirement_id=201,
            faculty_id=11,
            section_id=21,
        )
        first = self.demand(
            assignment_id=2,
            meeting_requirement_id=202,
            faculty_id=12,
            section_id=22,
            duration_slots=2,
        )
        second = self.demand(
            assignment_id=3,
            meeting_requirement_id=203,
            faculty_id=12,
            section_id=22,
            duration_slots=2,
        )
        penalty = 2_147_483_647
        candidates = (
            self.candidate(
                first,
                room_id=31,
                start_slot=0,
                end_slot=2,
                room_fit_penalty=penalty,
            ),
            self.candidate(
                second,
                room_id=31,
                start_slot=0,
                end_slot=2,
                room_fit_penalty=penalty,
            ),
        )
        fixed = self.fixed(
            assignment_id=None,
            faculty_id=91_001,
            section_id=91_002,
            room_id=91_003,
            day_of_week=2,
            start_slot=4,
            end_slot=5,
            meeting_type=None,
            counts_for_distribution=False,
        )
        return self.solver_input(
            demands=(missing, first, second),
            candidates=candidates,
            fixed_meetings=(fixed, fixed),
            weights=ObjectiveWeights(room_fit=1_073_741_825),
            candidate_counts=((missing.key, 0), (first.key, 1), (second.key, 1)),
        )

    def test_findings_have_stable_conservative_order_and_generic_heading(self):
        issues = self.diagnostics().diagnose_unsolved(
            self.all_finding_input(), "MODEL_INVALID"
        )

        self.assertEqual(
            tuple(issue.code for issue in issues),
            (
                "SOLVER_MODEL_INVALID",
                "POTENTIAL_BLOCKING_CONDITIONS",
                "ZERO_CANDIDATES",
                "FIXED_OCCUPANCY_CONFLICT",
                "FACULTY_WINDOW_CAPACITY",
                "SECTION_WINDOW_CAPACITY",
                "ROOM_WINDOW_CAPACITY",
                "OBJECTIVE_RANGE_LIMIT",
            ),
        )
        self.assertEqual(
            issues[1].message,
            "Potential blocking conditions detected.",
        )
        self.assertTrue(all(issue.severity == "WARNING" for issue in issues[1:]))

    def test_diagnostic_messages_never_expose_resource_identity_or_claim_a_core(self):
        issues = self.diagnostics().diagnose_unsolved(
            self.all_finding_input(), "MODEL_INVALID"
        )
        messages = " ".join(issue.message for issue in issues).lower()

        for protected_identity in ("91001", "91002", "91003"):
            self.assertNotIn(protected_identity, messages)
        self.assertNotIn("unsat core", messages)
        self.assertNotIn("root cause", messages)
        self.assertNotIn("minimal", messages)

    def test_missing_candidate_count_is_malformed_contract_data(self):
        demand = self.demand()
        data = self.solver_input(demands=(demand,), candidate_counts=())

        with self.assertRaises(KeyError):
            self.diagnostics().diagnose_unsolved(data, "INFEASIBLE")

    def test_fixed_overlap_finding_is_generic_and_deduplicated(self):
        fixed = self.fixed(
            assignment_id=None,
            meeting_type=None,
            counts_for_distribution=False,
        )
        data = self.solver_input(fixed_meetings=(fixed, fixed))

        issues = self.diagnostics().diagnose_unsolved(data, "INFEASIBLE")

        self.assertEqual(
            tuple(issue.code for issue in issues),
            (
                "SOLVER_INFEASIBLE",
                "POTENTIAL_BLOCKING_CONDITIONS",
                "FIXED_OCCUPANCY_CONFLICT",
            ),
        )
        self.assertEqual(
            issues[-1].message,
            "Fixed occupancy overlaps for at least one faculty, room, or section.",
        )

    def test_faculty_section_and_room_capacity_findings_are_independent(self):
        cases = {}

        faculty_first = self.demand(meeting_requirement_id=211, section_id=411)
        faculty_second = self.demand(
            assignment_id=101,
            meeting_requirement_id=212,
            faculty_id=faculty_first.faculty_id,
            section_id=412,
        )
        cases["FACULTY_WINDOW_CAPACITY"] = self.solver_input(
            demands=(faculty_first, faculty_second),
            candidates=(
                self.candidate(faculty_first, room_id=511),
                self.candidate(faculty_second, room_id=512),
            ),
        )

        section_first = self.demand(
            meeting_requirement_id=221,
            faculty_id=321,
            section_id=421,
        )
        section_second = self.demand(
            assignment_id=101,
            meeting_requirement_id=222,
            faculty_id=322,
            section_id=421,
        )
        cases["SECTION_WINDOW_CAPACITY"] = self.solver_input(
            demands=(section_first, section_second),
            candidates=(
                self.candidate(section_first, room_id=521),
                self.candidate(section_second, room_id=522),
            ),
        )

        room_first = self.demand(
            meeting_requirement_id=231,
            faculty_id=331,
            section_id=431,
        )
        room_second = self.demand(
            assignment_id=101,
            meeting_requirement_id=232,
            faculty_id=332,
            section_id=432,
        )
        cases["ROOM_WINDOW_CAPACITY"] = self.solver_input(
            demands=(room_first, room_second),
            candidates=(
                self.candidate(room_first, room_id=531),
                self.candidate(room_second, room_id=531),
            ),
        )

        for expected_code, data in cases.items():
            with self.subTest(expected_code=expected_code):
                self.assertEqual(
                    self.issue_codes(data),
                    (
                        "SOLVER_INFEASIBLE",
                        "POTENTIAL_BLOCKING_CONDITIONS",
                        expected_code,
                    ),
                )

    def test_hall_style_collision_is_not_misreported_as_a_detected_cause(self):
        room_sets = ((541,), (541, 542), (541, 543), (541, 542, 543), (544, 545))
        demands = tuple(
            self.demand(
                assignment_id=140 + index,
                meeting_requirement_id=240 + index,
                faculty_id=340 + index,
                section_id=440 + index,
            )
            for index in range(len(room_sets))
        )
        candidates = tuple(
            self.candidate(demand, room_id=room_id)
            for demand, rooms in zip(demands, room_sets, strict=True)
            for room_id in rooms
        )
        data = self.solver_input(demands=demands, candidates=candidates)

        result = solve(data)

        self.assertEqual(result.raw_status, "INFEASIBLE")
        self.assertEqual(
            tuple(issue.code for issue in result.issues),
            ("SOLVER_INFEASIBLE",),
        )

    def test_objective_overflow_returns_raw_model_invalid_without_values(self):
        first = self.demand(meeting_requirement_id=251)
        second = self.demand(
            assignment_id=151,
            meeting_requirement_id=252,
            faculty_id=351,
            section_id=451,
        )
        penalty = 2_147_483_647
        data = self.solver_input(
            demands=(first, second),
            candidates=(
                self.candidate(first, room_id=551, room_fit_penalty=penalty),
                self.candidate(second, room_id=552, room_fit_penalty=penalty),
            ),
            weights=ObjectiveWeights(room_fit=1_073_741_825),
        )

        result = solve(data)

        self.assertEqual(result.raw_status, "MODEL_INVALID")
        self.assertEqual(result.proposals, ())
        self.assertIsNone(result.objective_value)
        self.assertIsNone(result.best_bound)
        self.assertEqual(
            tuple(issue.code for issue in result.issues),
            (
                "SOLVER_MODEL_INVALID",
                "POTENTIAL_BLOCKING_CONDITIONS",
                "OBJECTIVE_RANGE_LIMIT",
            ),
        )

    def test_engine_and_diagnostics_import_without_django(self):
        diagnostics = self.diagnostics()
        real_import = __import__

        def reject_django(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "django" or name.startswith("django."):
                raise AssertionError(f"unexpected Django import: {name}")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=reject_django):
            reload(engine)
            reload(diagnostics)

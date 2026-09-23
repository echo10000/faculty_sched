"""Small, deterministic tests for the pure workload assignment optimizer."""

from unittest import TestCase

from workloads.balancing_solver import (
    AssignmentOption,
    BalancingInput,
    FacultyLoad,
    FacultyShare,
    OfferingDemand,
    solve_balancing,
)


def option(offering_id, faculty_id, load, *, changed=False, matches=0):
    return AssignmentOption(
        offering_id=offering_id,
        shares=(FacultyShare(faculty_id, 100, load),),
        changed=changed,
        qualification_match_count=matches,
    )


class BalancingSolverTests(TestCase):
    def test_every_offering_receives_one_complete_assignment(self):
        faculty = (FacultyLoad(1, 0, 1000), FacultyLoad(2, 0, 1000))
        split = AssignmentOption(
            10,
            (FacultyShare(1, 40, 400), FacultyShare(2, 60, 600)),
            changed=False,
        )
        data = BalancingInput(
            faculty,
            (
                OfferingDemand(10, (split, option(10, 1, 1000, changed=True))),
                OfferingDemand(11, (option(11, 2, 500),)),
            ),
        )

        result = solve_balancing(data)

        self.assertEqual(result.raw_status, "OPTIMAL")
        self.assertEqual(tuple(item.offering_id for item in result.chosen_options), (10, 11))
        self.assertEqual(sum(share.share_centis for share in result.chosen_options[0].shares), 100)
        self.assertIsNotNone(result.objective_value)
        self.assertIsNotNone(result.best_bound)
        self.assertEqual(result.statistics.option_count, 3)

    def test_normalized_deviation_accounts_for_different_targets(self):
        data = BalancingInput(
            (FacultyLoad(1, 0, 1000), FacultyLoad(2, 0, 2000)),
            (OfferingDemand(10, (option(10, 1, 1000), option(10, 2, 1000))),),
        )

        result = solve_balancing(data)

        self.assertEqual(result.chosen_options[0].shares[0].faculty_id, 1)

    def test_hard_maximum_is_never_exceeded(self):
        data = BalancingInput(
            (FacultyLoad(1, 900, 1000, 1000), FacultyLoad(2, 0, 1000, 1000)),
            (OfferingDemand(10, (option(10, 1, 300), option(10, 2, 300))),),
        )

        result = solve_balancing(data)

        self.assertEqual(result.chosen_options[0].shares[0].faculty_id, 2)

    def test_substantial_balance_improvement_outweighs_change_penalty(self):
        data = BalancingInput(
            (FacultyLoad(1, 800, 1000), FacultyLoad(2, 200, 1000)),
            (OfferingDemand(10, (option(10, 1, 400), option(10, 2, 400, changed=True))),),
        )

        result = solve_balancing(data)

        self.assertEqual(result.chosen_options[0].shares[0].faculty_id, 2)

    def test_existing_assignment_wins_an_equal_balance(self):
        data = BalancingInput(
            (FacultyLoad(1, 400, 1000), FacultyLoad(2, 400, 1000)),
            (OfferingDemand(10, (option(10, 1, 200), option(10, 2, 200, changed=True))),),
        )

        result = solve_balancing(data)

        self.assertEqual(result.chosen_options[0].shares[0].faculty_id, 1)

    def test_recorded_qualification_breaks_a_primary_score_tie(self):
        data = BalancingInput(
            (FacultyLoad(1, 400, 1000), FacultyLoad(2, 400, 1000)),
            (OfferingDemand(10, (option(10, 1, 200), option(10, 2, 200, matches=1))),),
        )

        result = solve_balancing(data)

        self.assertEqual(result.chosen_options[0].shares[0].faculty_id, 2)

    def test_infeasible_result_has_no_fabricated_proposal_or_objective(self):
        data = BalancingInput(
            (FacultyLoad(1, 900, 1000, 1000), FacultyLoad(2, 900, 1000, 1000)),
            (OfferingDemand(10, (option(10, 1, 200), option(10, 2, 200))),),
        )

        result = solve_balancing(data)

        self.assertEqual(result.raw_status, "INFEASIBLE")
        self.assertEqual(result.chosen_options, ())
        self.assertIsNone(result.objective_value)
        self.assertIsNone(result.best_bound)

    def test_fixed_seed_and_single_worker_produce_repeatable_choice(self):
        data = BalancingInput(
            (FacultyLoad(1, 400, 1000), FacultyLoad(2, 400, 1000)),
            (OfferingDemand(10, (option(10, 1, 200, changed=True), option(10, 2, 200, changed=True))),),
            random_seed=17,
        )

        choices = [solve_balancing(data).chosen_options for _ in range(3)]

        self.assertEqual(choices[0], choices[1])
        self.assertEqual(choices[1], choices[2])

    def test_share_integrity_and_unknown_faculty_are_rejected(self):
        faculty = (FacultyLoad(1, 0, 1000),)
        invalid_share = AssignmentOption(10, (FacultyShare(1, 99, 200),), False)
        unknown_faculty = option(10, 2, 200)

        with self.assertRaisesRegex(ValueError, "sum to 100"):
            solve_balancing(BalancingInput(faculty, (OfferingDemand(10, (invalid_share,)),)))
        with self.assertRaisesRegex(ValueError, "unknown faculty"):
            solve_balancing(BalancingInput(faculty, (OfferingDemand(10, (unknown_faculty,)),)))

"""Pure CP-SAT adapter for hard constraints and exact timetable objectives."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from .contracts import (
    CandidatePlacement,
    PenaltyBreakdown,
    ProposedMeeting,
    SolverInput,
    SolverResult,
    SolverStatistics,
)


_STATUS_NAMES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}
_SUCCESS_STATUSES = frozenset(("OPTIMAL", "FEASIBLE"))
_OBJECTIVE_FIELDS = (
    "faculty_preference",
    "faculty_gap",
    "section_gap",
    "meeting_distribution",
    "room_fit",
)


@dataclass(frozen=True, slots=True)
class _ObjectiveBounds:
    faculty_preference: int
    faculty_gap: int
    section_gap: int
    meeting_distribution: int
    room_fit: int
    weighted_upper: int


@dataclass(frozen=True, slots=True)
class _ObjectiveVariables:
    faculty_preference: cp_model.IntVar
    faculty_gap: cp_model.IntVar
    section_gap: cp_model.IntVar
    meeting_distribution: cp_model.IntVar
    room_fit: cp_model.IntVar


def _require_nonnegative_int64(value: int, label: str) -> None:
    if not isinstance(value, int) or value < 0 or value > cp_model.INT_MAX:
        raise ValueError(f"{label} must be a nonnegative signed 64-bit integer")


def _distribution_fixed_counts(data: SolverInput) -> Counter[tuple[int, str, int]]:
    counts: Counter[tuple[int, str, int]] = Counter()
    for fixed in data.fixed_meetings:
        if not fixed.counts_for_distribution:
            continue
        if fixed.assignment_id is None or fixed.meeting_type is None:
            raise ValueError(
                "fixed meetings counted for distribution require assignment and type"
            )
        counts[(fixed.assignment_id, fixed.meeting_type, fixed.day_of_week)] += 1
    return counts


def _resource_gap_bound(data: SolverInput, resource_field: str) -> int:
    slots_by_group: dict[tuple[int, int], set[int]] = defaultdict(set)
    for candidate in data.candidates:
        group = (getattr(candidate, resource_field), candidate.day_of_week)
        slots_by_group[group].update(range(candidate.start_slot, candidate.end_slot))
    for fixed in data.fixed_meetings:
        group = (getattr(fixed, resource_field), fixed.day_of_week)
        slots_by_group[group].update(range(fixed.start_slot, fixed.end_slot))
    return sum(max(slots) - min(slots) for slots in slots_by_group.values() if slots)


def _objective_bounds(data: SolverInput) -> _ObjectiveBounds:
    """Return tight component bounds and the exact Python-integer weighted bound."""

    weights = data.policy.weights
    for field in _OBJECTIVE_FIELDS:
        _require_nonnegative_int64(getattr(weights, field), f"{field} weight")

    preference_by_demand: dict[tuple[int, int], list[int]] = defaultdict(list)
    room_fit_by_demand: dict[tuple[int, int], list[int]] = defaultdict(list)
    for candidate in data.candidates:
        _require_nonnegative_int64(
            candidate.preferred_penalty, "candidate preference penalty"
        )
        _require_nonnegative_int64(
            candidate.room_fit_penalty, "candidate room-fit penalty"
        )
        preference_by_demand[candidate.demand_key].append(
            candidate.preferred_penalty
        )
        room_fit_by_demand[candidate.demand_key].append(candidate.room_fit_penalty)

    preference = sum(
        max(preference_by_demand.get(demand.key, (0,))) for demand in data.demands
    )
    room_fit = sum(
        max(room_fit_by_demand.get(demand.key, (0,))) for demand in data.demands
    )
    faculty_gap = _resource_gap_bound(data, "faculty_id")
    section_gap = _resource_gap_bound(data, "section_id")

    retained_by_type: Counter[tuple[int, str]] = Counter()
    for (assignment_id, meeting_type, _day), count in _distribution_fixed_counts(
        data
    ).items():
        retained_by_type[(assignment_id, meeting_type)] += count
    remaining_by_type = Counter(
        (demand.assignment_id, demand.meeting_type) for demand in data.demands
    )
    distribution = sum(
        max(0, retained_by_type[pair] + remaining_by_type[pair] - 1)
        for pair in retained_by_type.keys() | remaining_by_type.keys()
    )

    component_bounds = {
        "faculty_preference": preference,
        "faculty_gap": faculty_gap,
        "section_gap": section_gap,
        "meeting_distribution": distribution,
        "room_fit": room_fit,
    }
    for field, bound in component_bounds.items():
        if bound > cp_model.INT_MAX:
            raise ValueError(f"{field} bound exceeds CP-SAT's integer domain")
    weighted_upper = sum(
        getattr(weights, field) * component_bounds[field]
        for field in _OBJECTIVE_FIELDS
    )
    return _ObjectiveBounds(**component_bounds, weighted_upper=weighted_upper)


def _fixed_slot_sets(
    data: SolverInput,
) -> tuple[
    set[tuple[int, int, int]],
    set[tuple[int, int, int]],
    set[tuple[int, int, int]],
]:
    faculty_slots: set[tuple[int, int, int]] = set()
    room_slots: set[tuple[int, int, int]] = set()
    section_slots: set[tuple[int, int, int]] = set()
    for fixed in data.fixed_meetings:
        for slot in range(fixed.start_slot, fixed.end_slot):
            faculty_slots.add((fixed.faculty_id, fixed.day_of_week, slot))
            room_slots.add((fixed.room_id, fixed.day_of_week, slot))
            section_slots.add((fixed.section_id, fixed.day_of_week, slot))
    return faculty_slots, room_slots, section_slots


def _validate_candidates_avoid_fixed_occupancy(data: SolverInput) -> None:
    faculty_slots, room_slots, section_slots = _fixed_slot_sets(data)
    for candidate in data.candidates:
        for slot in range(candidate.start_slot, candidate.end_slot):
            if (
                (candidate.faculty_id, candidate.day_of_week, slot) in faculty_slots
                or (candidate.room_id, candidate.day_of_week, slot) in room_slots
                or (candidate.section_id, candidate.day_of_week, slot) in section_slots
            ):
                raise ValueError("candidate placement overlaps fixed occupancy")


def _add_collision_constraints(
    model: cp_model.CpModel,
    variables: tuple[cp_model.IntVar, ...],
    candidates: tuple[CandidatePlacement, ...],
) -> None:
    faculty_buckets: dict[tuple[int, int, int], list[cp_model.IntVar]] = defaultdict(list)
    room_buckets: dict[tuple[int, int, int], list[cp_model.IntVar]] = defaultdict(list)
    section_buckets: dict[tuple[int, int, int], list[cp_model.IntVar]] = defaultdict(list)

    for variable, candidate in zip(variables, candidates, strict=True):
        for slot in range(candidate.start_slot, candidate.end_slot):
            faculty_buckets[(candidate.faculty_id, candidate.day_of_week, slot)].append(
                variable
            )
            room_buckets[(candidate.room_id, candidate.day_of_week, slot)].append(variable)
            section_buckets[(candidate.section_id, candidate.day_of_week, slot)].append(
                variable
            )

    for buckets in (faculty_buckets, room_buckets, section_buckets):
        for bucket_variables in buckets.values():
            if len(bucket_variables) >= 2:
                model.add_at_most_one(bucket_variables)


def _build_hard_model(
    data: SolverInput,
) -> tuple[cp_model.CpModel, tuple[cp_model.IntVar, ...]]:
    model = cp_model.CpModel()
    variables = tuple(
        model.new_bool_var(f"p_{index}")
        for index, _candidate in enumerate(data.candidates)
    )
    candidate_indexes_by_demand: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, candidate in enumerate(data.candidates):
        candidate_indexes_by_demand[candidate.demand_key].append(index)

    for demand in data.demands:
        model.add_exactly_one(
            variables[index]
            for index in candidate_indexes_by_demand[demand.key]
        )

    _add_collision_constraints(model, variables, data.candidates)
    return model, variables


def _add_candidate_total(
    model: cp_model.CpModel,
    variables: tuple[cp_model.IntVar, ...],
    coefficients: tuple[int, ...],
    upper_bound: int,
    name: str,
) -> cp_model.IntVar:
    total = model.new_int_var(0, upper_bound, name)
    model.add(
        total
        == sum(
            (
                coefficient * variable
                for coefficient, variable in zip(
                    coefficients, variables, strict=True
                )
            ),
            0,
        )
    )
    return total


def _resource_slot_maps(
    data: SolverInput,
    resource_field: str,
) -> tuple[
    dict[tuple[int, int, int], list[int]],
    dict[tuple[int, int], set[int]],
]:
    candidate_indexes: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    fixed_slots: dict[tuple[int, int], set[int]] = defaultdict(set)
    for index, candidate in enumerate(data.candidates):
        resource_id = getattr(candidate, resource_field)
        for slot in range(candidate.start_slot, candidate.end_slot):
            candidate_indexes[(resource_id, candidate.day_of_week, slot)].append(index)
    for fixed in data.fixed_meetings:
        group = (getattr(fixed, resource_field), fixed.day_of_week)
        fixed_slots[group].update(range(fixed.start_slot, fixed.end_slot))
    return candidate_indexes, fixed_slots


def _add_gap_total(
    model: cp_model.CpModel,
    variables: tuple[cp_model.IntVar, ...],
    data: SolverInput,
    resource_field: str,
    upper_bound: int,
    prefix: str,
) -> cp_model.IntVar:
    candidate_indexes, fixed_slots = _resource_slot_maps(data, resource_field)
    slots_by_group: dict[tuple[int, int], set[int]] = defaultdict(set)
    for resource_id, day_of_week, slot in candidate_indexes:
        slots_by_group[(resource_id, day_of_week)].add(slot)
    for group, slots in fixed_slots.items():
        slots_by_group[group].update(slots)

    gap_variables: list[cp_model.IntVar] = []
    for group_index, group in enumerate(sorted(slots_by_group)):
        universe = slots_by_group[group]
        if not universe:
            continue
        low = min(universe)
        high = max(universe)
        occupancy = []
        for slot in range(low, high + 1):
            if slot in fixed_slots.get(group, ()):
                occupancy.append(1)
            else:
                indexes = candidate_indexes.get((*group, slot), ())
                occupancy.append(sum((variables[index] for index in indexes), 0))

        first = model.new_int_var(low, high + 1, f"{prefix}_first_{group_index}")
        last = model.new_int_var(low - 1, high, f"{prefix}_last_{group_index}")
        gap = model.new_int_var(0, high - low, f"{prefix}_gap_{group_index}")
        first_candidates = tuple(
            (high + 1) - (high + 1 - slot) * occupied
            for slot, occupied in zip(range(low, high + 1), occupancy, strict=True)
        )
        last_candidates = tuple(
            (low - 1) + (slot - low + 1) * occupied
            for slot, occupied in zip(range(low, high + 1), occupancy, strict=True)
        )
        model.add_min_equality(first, first_candidates)
        model.add_max_equality(last, last_candidates)
        model.add_max_equality(gap, (0, last - first + 1 - sum(occupancy, 0)))
        gap_variables.append(gap)

    total = model.new_int_var(0, upper_bound, f"{prefix}_total")
    model.add(total == sum(gap_variables, 0))
    return total


def _add_distribution_total(
    model: cp_model.CpModel,
    variables: tuple[cp_model.IntVar, ...],
    data: SolverInput,
    upper_bound: int,
) -> cp_model.IntVar:
    candidate_indexes: dict[tuple[int, str, int], list[int]] = defaultdict(list)
    for index, candidate in enumerate(data.candidates):
        candidate_indexes[
            (candidate.assignment_id, candidate.meeting_type, candidate.day_of_week)
        ].append(index)
    fixed_counts = _distribution_fixed_counts(data)

    distribution_variables: list[cp_model.IntVar] = []
    for group_index, group in enumerate(
        sorted(candidate_indexes.keys() | fixed_counts.keys())
    ):
        indexes = candidate_indexes.get(group, ())
        distinct_demands = {data.candidates[index].demand_key for index in indexes}
        fixed_count = fixed_counts[group]
        local_upper = max(0, fixed_count + len(distinct_demands) - 1)
        distribution = model.new_int_var(
            0, local_upper, f"distribution_{group_index}"
        )
        occurrence_count = fixed_count + sum(
            (variables[index] for index in indexes), 0
        )
        model.add_max_equality(distribution, (0, occurrence_count - 1))
        distribution_variables.append(distribution)

    total = model.new_int_var(0, upper_bound, "distribution_total")
    model.add(total == sum(distribution_variables, 0))
    return total


def _build_model(
    data: SolverInput,
) -> tuple[
    cp_model.CpModel,
    tuple[cp_model.IntVar, ...],
    _ObjectiveVariables,
]:
    bounds = _objective_bounds(data)
    _validate_candidates_avoid_fixed_occupancy(data)
    model, variables = _build_hard_model(data)
    preference = _add_candidate_total(
        model,
        variables,
        tuple(candidate.preferred_penalty for candidate in data.candidates),
        bounds.faculty_preference,
        "preference_total",
    )
    faculty_gap = _add_gap_total(
        model,
        variables,
        data,
        "faculty_id",
        bounds.faculty_gap,
        "faculty",
    )
    section_gap = _add_gap_total(
        model,
        variables,
        data,
        "section_id",
        bounds.section_gap,
        "section",
    )
    distribution = _add_distribution_total(
        model,
        variables,
        data,
        bounds.meeting_distribution,
    )
    room_fit = _add_candidate_total(
        model,
        variables,
        tuple(candidate.room_fit_penalty for candidate in data.candidates),
        bounds.room_fit,
        "room_fit_total",
    )
    totals = _ObjectiveVariables(
        faculty_preference=preference,
        faculty_gap=faculty_gap,
        section_gap=section_gap,
        meeting_distribution=distribution,
        room_fit=room_fit,
    )
    weights = data.policy.weights
    model.minimize(
        weights.faculty_preference * totals.faculty_preference
        + weights.faculty_gap * totals.faculty_gap
        + weights.section_gap * totals.section_gap
        + weights.meeting_distribution * totals.meeting_distribution
        + weights.room_fit * totals.room_fit
    )
    return model, variables, totals


def _status_name(status: cp_model.CpSolverStatus) -> str:
    return _STATUS_NAMES[status]


def _proposal_for(candidate: CandidatePlacement) -> ProposedMeeting:
    meeting_requirement_id, occurrence_index = candidate.demand_key
    return ProposedMeeting(
        assignment_id=candidate.assignment_id,
        meeting_requirement_id=meeting_requirement_id,
        occurrence_index=occurrence_index,
        room_id=candidate.room_id,
        day_of_week=candidate.day_of_week,
        start_slot=candidate.start_slot,
        end_slot=candidate.end_slot,
        meeting_type=candidate.meeting_type,
    )


def _extract_complete_selection(
    data: SolverInput,
    variables: tuple[cp_model.IntVar, ...],
    solver: cp_model.CpSolver,
) -> tuple[tuple[ProposedMeeting, ...], tuple[CandidatePlacement, ...]]:
    selected = tuple(
        candidate
        for candidate, variable in zip(data.candidates, variables, strict=True)
        if solver.boolean_value(variable)
    )
    proposals = tuple(_proposal_for(candidate) for candidate in selected)
    expected_keys = Counter(demand.key for demand in data.demands)
    proposal_keys = Counter(proposal.demand_key for proposal in proposals)
    if len(proposals) != len(data.demands) or proposal_keys != expected_keys:
        raise RuntimeError("CP-SAT returned a success status without a complete proposal set")
    return proposals, selected


def _occupied_slots(
    data: SolverInput,
    selected: tuple[CandidatePlacement, ...],
    resource_field: str,
) -> dict[tuple[int, int], set[int]]:
    occupied: dict[tuple[int, int], set[int]] = defaultdict(set)
    for candidate in selected:
        occupied[(getattr(candidate, resource_field), candidate.day_of_week)].update(
            range(candidate.start_slot, candidate.end_slot)
        )
    for fixed in data.fixed_meetings:
        occupied[(getattr(fixed, resource_field), fixed.day_of_week)].update(
            range(fixed.start_slot, fixed.end_slot)
        )
    return occupied


def _gap_penalty(occupied: dict[tuple[int, int], set[int]]) -> int:
    return sum(
        max(slots) - min(slots) + 1 - len(slots)
        for slots in occupied.values()
        if slots
    )


def _penalty_breakdown(
    data: SolverInput,
    selected: tuple[CandidatePlacement, ...],
) -> PenaltyBreakdown:
    distribution_counts = _distribution_fixed_counts(data)
    for candidate in selected:
        distribution_counts[
            (candidate.assignment_id, candidate.meeting_type, candidate.day_of_week)
        ] += 1
    return PenaltyBreakdown(
        faculty_preference=sum(
            candidate.preferred_penalty for candidate in selected
        ),
        faculty_gap=_gap_penalty(_occupied_slots(data, selected, "faculty_id")),
        section_gap=_gap_penalty(_occupied_slots(data, selected, "section_id")),
        meeting_distribution=sum(
            max(0, count - 1) for count in distribution_counts.values()
        ),
        room_fit=sum(candidate.room_fit_penalty for candidate in selected),
    )


def _weighted_objective(data: SolverInput, penalties: PenaltyBreakdown) -> int:
    weights = data.policy.weights
    return sum(
        getattr(weights, field) * getattr(penalties, field)
        for field in _OBJECTIVE_FIELDS
    )


def _statistics(
    data: SolverInput,
    model: cp_model.CpModel,
    solver: cp_model.CpSolver,
    proposals: tuple[ProposedMeeting, ...],
) -> SolverStatistics:
    return SolverStatistics(
        wall_time_seconds=solver.wall_time,
        branches=solver.num_branches,
        conflicts=solver.num_conflicts,
        candidate_count=len(data.candidates),
        variable_count=len(model.proto.variables),
        generated_meeting_count=len(proposals),
        random_seed=data.policy.random_seed,
        worker_count=data.policy.worker_count,
        time_limit_seconds=data.policy.solver_time_limit_seconds,
    )


def solve(data: SolverInput) -> SolverResult:
    """Solve the timetable model and return only complete solutions."""

    model, variables, _totals = _build_model(data)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = data.policy.solver_time_limit_seconds
    solver.parameters.random_seed = data.policy.random_seed
    solver.parameters.num_search_workers = data.policy.worker_count

    raw_status = _status_name(solver.solve(model))
    if raw_status in _SUCCESS_STATUSES:
        proposals, selected = _extract_complete_selection(data, variables, solver)
        penalties = _penalty_breakdown(data, selected)
        objective_value = _weighted_objective(data, penalties)
        best_bound = float(solver.best_objective_bound)
        issues = ()
    else:
        from .diagnostics import diagnose_unsolved

        proposals = ()
        penalties = PenaltyBreakdown()
        objective_value = None
        best_bound = None
        issues = diagnose_unsolved(data, raw_status)
    return SolverResult(
        raw_status=raw_status,
        proposals=proposals,
        objective_value=objective_value,
        best_bound=best_bound,
        penalties=penalties,
        statistics=_statistics(data, model, solver, proposals),
        issues=issues,
    )

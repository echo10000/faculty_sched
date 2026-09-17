"""Pure CP-SAT hard-constraint adapter for timetable candidates."""

from __future__ import annotations

from collections import Counter, defaultdict

from ortools.sat.python import cp_model

from .contracts import (
    CandidatePlacement,
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


def _extract_complete_proposals(
    data: SolverInput,
    variables: tuple[cp_model.IntVar, ...],
    solver: cp_model.CpSolver,
) -> tuple[ProposedMeeting, ...]:
    proposals = tuple(
        _proposal_for(candidate)
        for candidate, variable in zip(data.candidates, variables, strict=True)
        if solver.boolean_value(variable)
    )
    expected_keys = Counter(demand.key for demand in data.demands)
    proposal_keys = Counter(proposal.demand_key for proposal in proposals)
    if len(proposals) != len(data.demands) or proposal_keys != expected_keys:
        raise RuntimeError("CP-SAT returned a success status without a complete proposal set")
    return proposals


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
    """Solve the hard timetable model and return only complete solutions."""

    model, variables = _build_hard_model(data)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = data.policy.solver_time_limit_seconds
    solver.parameters.random_seed = data.policy.random_seed
    solver.parameters.num_search_workers = data.policy.worker_count

    raw_status = _status_name(solver.solve(model))
    proposals = (
        _extract_complete_proposals(data, variables, solver)
        if raw_status in _SUCCESS_STATUSES
        else ()
    )
    return SolverResult(
        raw_status=raw_status,
        proposals=proposals,
        objective_value=None,
        best_bound=None,
        statistics=_statistics(data, model, solver, proposals),
    )

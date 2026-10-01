"""Conservative, identity-safe findings for unsolved timetable models."""

from __future__ import annotations

from collections import Counter, defaultdict

from ortools.sat.python import cp_model

from .contracts import ReadinessIssue, SolverInput
from .engine import _objective_bounds


__all__ = ("diagnose_unsolved",)


_STATUS_ISSUES = {
    "INFEASIBLE": ReadinessIssue(
        "SOLVER_INFEASIBLE",
        "ERROR",
        "The solver proved that no complete timetable satisfies all hard constraints.",
    ),
    "MODEL_INVALID": ReadinessIssue(
        "SOLVER_MODEL_INVALID",
        "ERROR",
        "The solver rejected the generated model. Review the scheduling configuration and model diagnostics.",
    ),
    "UNKNOWN": ReadinessIssue(
        "SOLVER_UNKNOWN",
        "ERROR",
        "No complete solution was returned within the configured solver time bound.",
    ),
}
_HEADING = ReadinessIssue(
    "POTENTIAL_BLOCKING_CONDITIONS",
    "WARNING",
    "Potential blocking conditions detected.",
)
_ZERO_CANDIDATES = ReadinessIssue(
    "ZERO_CANDIDATES",
    "WARNING",
    "At least one remaining meeting demand has no valid placement.",
)
_FIXED_OCCUPANCY_CONFLICT = ReadinessIssue(
    "FIXED_OCCUPANCY_CONFLICT",
    "WARNING",
    "Fixed occupancy overlaps for at least one faculty, room, or section.",
)
_FACULTY_WINDOW_CAPACITY = ReadinessIssue(
    "FACULTY_WINDOW_CAPACITY",
    "WARNING",
    "The candidate windows provide fewer non-overlapping faculty slots than the remaining meetings require.",
)
_SECTION_WINDOW_CAPACITY = ReadinessIssue(
    "SECTION_WINDOW_CAPACITY",
    "WARNING",
    "The candidate windows provide fewer non-overlapping section slots than the remaining meetings require.",
)
_ROOM_WINDOW_CAPACITY = ReadinessIssue(
    "ROOM_WINDOW_CAPACITY",
    "WARNING",
    "The candidate room windows provide fewer non-overlapping room slots than the remaining meetings require.",
)
_OBJECTIVE_RANGE_LIMIT = ReadinessIssue(
    "OBJECTIVE_RANGE_LIMIT",
    "WARNING",
    "The configured objective weights and candidate penalties exceed CP-SAT's safe integer range.",
)


def _has_zero_candidate_demand(data: SolverInput) -> bool:
    counts = dict(data.candidate_counts)
    has_zero = False
    for demand in sorted(data.demands, key=lambda item: item.key):
        if counts[demand.key] == 0:
            has_zero = True
    return has_zero


def _has_fixed_occupancy_conflict(data: SolverInput) -> bool:
    faculty_slots: set[tuple[int, int, int]] = set()
    room_slots: set[tuple[int, int, int]] = set()
    section_slots: set[tuple[int, int, int]] = set()
    for fixed in data.fixed_meetings:
        for slot in range(fixed.start_slot, fixed.end_slot):
            faculty_key = (fixed.faculty_id, fixed.day_of_week, slot)
            room_key = (fixed.room_id, fixed.day_of_week, slot)
            section_key = (fixed.section_id, fixed.day_of_week, slot)
            if (
                faculty_key in faculty_slots
                or room_key in room_slots
                or section_key in section_slots
            ):
                return True
            faculty_slots.add(faculty_key)
            room_slots.add(room_key)
            section_slots.add(section_key)
    return False


def _resource_capacity_shortfall(data: SolverInput, resource_field: str) -> bool:
    required: Counter[int] = Counter()
    demand_by_key = {}
    for demand in data.demands:
        resource_id = getattr(demand, resource_field)
        required[resource_id] += demand.duration_slots
        demand_by_key[demand.key] = demand

    available: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for candidate in data.candidates:
        demand = demand_by_key[candidate.demand_key]
        resource_id = getattr(demand, resource_field)
        available[resource_id].update(
            (candidate.day_of_week, slot)
            for slot in range(candidate.start_slot, candidate.end_slot)
        )
    return any(
        required_slots > len(available[resource_id])
        for resource_id, required_slots in required.items()
    )


def _room_capacity_shortfall(data: SolverInput) -> bool:
    candidates_by_demand = defaultdict(list)
    coverage_by_demand: dict[
        tuple[int, int], set[tuple[int, int, int]]
    ] = defaultdict(set)
    rooms_by_demand: dict[tuple[int, int], set[int]] = defaultdict(set)
    for candidate in data.candidates:
        candidates_by_demand[candidate.demand_key].append(candidate)
        rooms_by_demand[candidate.demand_key].add(candidate.room_id)
        coverage_by_demand[candidate.demand_key].update(
            (candidate.room_id, candidate.day_of_week, slot)
            for slot in range(candidate.start_slot, candidate.end_slot)
        )

    relevant_demands = tuple(
        demand for demand in data.demands if candidates_by_demand[demand.key]
    )
    global_required = sum(demand.duration_slots for demand in relevant_demands)
    global_coverage: set[tuple[int, int, int]] = set()
    for demand in relevant_demands:
        global_coverage.update(coverage_by_demand[demand.key])
    if global_required > len(global_coverage):
        return True

    demands_by_signature = defaultdict(list)
    for demand in relevant_demands:
        signature = tuple(sorted(rooms_by_demand[demand.key]))
        if signature:
            demands_by_signature[signature].append(demand)
    for demands in demands_by_signature.values():
        required = sum(demand.duration_slots for demand in demands)
        coverage: set[tuple[int, int, int]] = set()
        for demand in demands:
            coverage.update(coverage_by_demand[demand.key])
        if required > len(coverage):
            return True
    return False


def diagnose_unsolved(
    input_data: SolverInput,
    raw_status: str,
) -> tuple[ReadinessIssue, ...]:
    """Return bounded necessary-condition findings without claiming an unsat core."""

    try:
        status_issue = _STATUS_ISSUES[raw_status]
    except KeyError as error:
        raise ValueError(f"unsupported unsolved solver status: {raw_status}") from error

    findings = []
    if _has_zero_candidate_demand(input_data):
        findings.append(_ZERO_CANDIDATES)
    if _has_fixed_occupancy_conflict(input_data):
        findings.append(_FIXED_OCCUPANCY_CONFLICT)
    if _resource_capacity_shortfall(input_data, "faculty_id"):
        findings.append(_FACULTY_WINDOW_CAPACITY)
    if _resource_capacity_shortfall(input_data, "section_id"):
        findings.append(_SECTION_WINDOW_CAPACITY)
    if _room_capacity_shortfall(input_data):
        findings.append(_ROOM_WINDOW_CAPACITY)
    if _objective_bounds(input_data).weighted_upper > cp_model.INT_MAX // 2:
        findings.append(_OBJECTIVE_RANGE_LIMIT)

    if findings:
        return status_issue, _HEADING, *findings
    return (status_issue,)

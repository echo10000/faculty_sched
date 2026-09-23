"""Pure CP-SAT optimizer for proposed faculty-to-offering assignments.

The scoped ORM adapter owns eligibility and authoritative workload arithmetic.
It supplies integer-scaled loads with included offerings removed from each
faculty baseline, then supplies complete, legal assignment options. This
module never reads or changes Django state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model


_UTILIZATION_SCALE = 1000  # One unit is one tenth of a percentage point.
_BALANCE_WEIGHT = 100
_CHANGE_PENALTY = 2500  # A change needs >2.5 percentage points of improvement.
_MAX_SECONDS = 10
_STATUSES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}


@dataclass(frozen=True, slots=True)
class FacultyLoad:
    faculty_id: int
    baseline_load_scaled: int
    target_load_scaled: int
    hard_max_scaled: int | None = None


@dataclass(frozen=True, slots=True)
class FacultyShare:
    faculty_id: int
    share_centis: int
    contribution_scaled: int


@dataclass(frozen=True, slots=True)
class AssignmentOption:
    offering_id: int
    shares: tuple[FacultyShare, ...]
    changed: bool
    qualification_match_count: int = 0


@dataclass(frozen=True, slots=True)
class OfferingDemand:
    offering_id: int
    options: tuple[AssignmentOption, ...]


@dataclass(frozen=True, slots=True)
class BalancingInput:
    faculty: tuple[FacultyLoad, ...]
    offerings: tuple[OfferingDemand, ...]
    time_limit_seconds: int = _MAX_SECONDS
    random_seed: int = 2026


@dataclass(frozen=True, slots=True)
class BalancingStatistics:
    wall_time_seconds: float = 0.0
    branches: int = 0
    conflicts: int = 0
    option_count: int = 0
    variable_count: int = 0
    random_seed: int = 0
    worker_count: int = 1
    time_limit_seconds: int = 0


@dataclass(frozen=True, slots=True)
class BalancingResult:
    raw_status: str
    chosen_options: tuple[AssignmentOption, ...] = ()
    objective_value: int | None = None
    best_bound: float | None = None
    statistics: BalancingStatistics = field(default_factory=BalancingStatistics)


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= cp_model.INT_MAX:
        raise ValueError(f"{label} must be an integer from {minimum} to {cp_model.INT_MAX}")
    return value


def _validate(data: BalancingInput) -> tuple[dict[int, FacultyLoad], dict[int, int]]:
    if not isinstance(data, BalancingInput):
        raise ValueError("data must be BalancingInput")
    if not isinstance(data.faculty, tuple) or not isinstance(data.offerings, tuple):
        raise ValueError("faculty and offerings must be immutable tuples")
    _integer(data.time_limit_seconds, "time limit", minimum=1)
    if data.time_limit_seconds > _MAX_SECONDS:
        raise ValueError("time limit must not exceed 10 seconds")
    _integer(data.random_seed, "random seed")
    if data.random_seed > 2**31 - 1:
        raise ValueError("random seed exceeds the supported range")

    faculty_by_id: dict[int, FacultyLoad] = {}
    load_upper: dict[int, int] = {}
    for member in data.faculty:
        _integer(member.faculty_id, "faculty ID", minimum=1)
        _integer(member.baseline_load_scaled, "baseline load")
        _integer(member.target_load_scaled, "target load", minimum=1)
        if member.hard_max_scaled is not None:
            _integer(member.hard_max_scaled, "hard maximum")
        if member.faculty_id in faculty_by_id:
            raise ValueError("faculty IDs must be unique")
        faculty_by_id[member.faculty_id] = member
        load_upper[member.faculty_id] = member.baseline_load_scaled

    offering_ids: set[int] = set()
    for demand in data.offerings:
        _integer(demand.offering_id, "offering ID", minimum=1)
        if demand.offering_id in offering_ids:
            raise ValueError("offering IDs must be unique")
        offering_ids.add(demand.offering_id)
        if not isinstance(demand.options, tuple):
            raise ValueError("offering options must be an immutable tuple")
        max_contribution: dict[int, int] = {}
        for candidate in demand.options:
            if candidate.offering_id != demand.offering_id:
                raise ValueError("option offering ID does not match its demand")
            if type(candidate.changed) is not bool:
                raise ValueError("changed must be a boolean")
            if not isinstance(candidate.shares, tuple) or not candidate.shares:
                raise ValueError("option shares must be a nonempty immutable tuple")
            _integer(candidate.qualification_match_count, "qualification match count")
            if candidate.qualification_match_count > len(candidate.shares):
                raise ValueError("qualification matches cannot exceed faculty shares")
            share_total = 0
            seen: set[int] = set()
            for share in candidate.shares:
                _integer(share.faculty_id, "share faculty ID", minimum=1)
                _integer(share.share_centis, "share centis", minimum=1)
                _integer(share.contribution_scaled, "share contribution")
                if share.share_centis > 100:
                    raise ValueError("share centis cannot exceed 100")
                if share.faculty_id not in faculty_by_id:
                    raise ValueError("option refers to unknown faculty")
                if share.faculty_id in seen:
                    raise ValueError("option cannot repeat a faculty member")
                seen.add(share.faculty_id)
                share_total += share.share_centis
                max_contribution[share.faculty_id] = max(
                    max_contribution.get(share.faculty_id, 0),
                    share.contribution_scaled,
                )
            if share_total != 100:
                raise ValueError("option shares must sum to 100 centis")
        for faculty_id, amount in max_contribution.items():
            load_upper[faculty_id] += amount
            if load_upper[faculty_id] > cp_model.INT_MAX:
                raise ValueError("possible faculty load exceeds CP-SAT integer range")

    # DivisionEquality uses a scaled numerator. Check its domain and the
    # combined objective before constructing any CP-SAT variables.
    deviation_upper = 0
    for member in data.faculty:
        absolute_upper = max(
            abs(member.baseline_load_scaled - member.target_load_scaled),
            abs(load_upper[member.faculty_id] - member.target_load_scaled),
        )
        if absolute_upper * _UTILIZATION_SCALE > cp_model.INT_MAX:
            raise ValueError("normalized deviation exceeds CP-SAT integer range")
        deviation_upper += absolute_upper * _UTILIZATION_SCALE // member.target_load_scaled
    qualification_upper = sum(
        max((candidate.qualification_match_count for candidate in demand.options), default=0)
        for demand in data.offerings
    )
    primary_upper = _BALANCE_WEIGHT * deviation_upper + _CHANGE_PENALTY * len(data.offerings)
    if primary_upper * (qualification_upper + 1) + qualification_upper > cp_model.INT_MAX:
        raise ValueError("objective exceeds CP-SAT integer range")
    return faculty_by_id, load_upper


def solve_balancing(data: BalancingInput) -> BalancingResult:
    """Choose exactly one complete option per offering under enforced maxima.

    Minimize the sum of each faculty's absolute utilization deviation from its
    configured target, plus a centralized cost for changed assignments. The
    optional recorded-qualification score breaks primary-score ties only.
    All inputs must already be authorized and eligible; this pure solver cannot
    prove database scope, activity, or the provenance of workload arithmetic.
    """

    faculty_by_id, load_upper = _validate(data)
    model = cp_model.CpModel()
    choices: list[tuple[AssignmentOption, cp_model.IntVar]] = []
    option_vars_by_demand: list[tuple[cp_model.IntVar, ...]] = []
    for demand in data.offerings:
        current_vars: list[cp_model.IntVar] = []
        for index, candidate in enumerate(demand.options):
            variable = model.new_bool_var(f"offering_{demand.offering_id}_option_{index}")
            choices.append((candidate, variable))
            current_vars.append(variable)
        model.add(sum(current_vars) == 1)
        option_vars_by_demand.append(tuple(current_vars))

    deviations: list[cp_model.IntVar] = []
    for member in data.faculty:
        contributions = [
            share.contribution_scaled * variable
            for candidate, variable in choices
            for share in candidate.shares
            if share.faculty_id == member.faculty_id
        ]
        upper = load_upper[member.faculty_id]
        load = model.new_int_var(member.baseline_load_scaled, upper, f"load_{member.faculty_id}")
        model.add(load == member.baseline_load_scaled + sum(contributions))
        if member.hard_max_scaled is not None:
            model.add(load <= member.hard_max_scaled)
        abs_upper = max(
            abs(member.baseline_load_scaled - member.target_load_scaled),
            abs(upper - member.target_load_scaled),
        )
        absolute_deviation = model.new_int_var(0, abs_upper, f"abs_deviation_{member.faculty_id}")
        model.add_abs_equality(absolute_deviation, load - member.target_load_scaled)
        scaled_numerator = model.new_int_var(
            0, abs_upper * _UTILIZATION_SCALE, f"normalized_numerator_{member.faculty_id}"
        )
        model.add(scaled_numerator == absolute_deviation * _UTILIZATION_SCALE)
        normalized = model.new_int_var(
            0,
            abs_upper * _UTILIZATION_SCALE // member.target_load_scaled,
            f"normalized_deviation_{member.faculty_id}",
        )
        model.add_division_equality(
            normalized, scaled_numerator, member.target_load_scaled
        )
        deviations.append(normalized)

    qualification_maxima = [
        max((candidate.qualification_match_count for candidate in demand.options), default=0)
        for demand in data.offerings
    ]
    qualification_span = sum(qualification_maxima)
    qualification_shortfall = sum(
        (qualification_maxima[index] - candidate.qualification_match_count) * variable
        for index, demand in enumerate(data.offerings)
        for candidate, variable in zip(demand.options, option_vars_by_demand[index])
    )
    primary = (
        _BALANCE_WEIGHT * sum(deviations)
        + _CHANGE_PENALTY * sum(variable for candidate, variable in choices if candidate.changed)
    )
    model.minimize(primary * (qualification_span + 1) + qualification_shortfall)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = data.time_limit_seconds
    solver.parameters.random_seed = data.random_seed
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    raw_status = _STATUSES.get(status, "UNKNOWN")
    statistics = BalancingStatistics(
        wall_time_seconds=float(solver.wall_time),
        branches=int(solver.num_branches),
        conflicts=int(solver.num_conflicts),
        option_count=len(choices),
        variable_count=len(model.proto.variables),
        random_seed=data.random_seed,
        worker_count=1,
        time_limit_seconds=data.time_limit_seconds,
    )
    if raw_status not in ("OPTIMAL", "FEASIBLE"):
        return BalancingResult(raw_status=raw_status, statistics=statistics)
    selected = tuple(
        candidate for candidate, variable in choices if solver.boolean_value(variable)
    )
    return BalancingResult(
        raw_status=raw_status,
        chosen_options=selected,
        objective_value=round(solver.objective_value),
        best_bound=float(solver.best_objective_bound),
        statistics=statistics,
    )

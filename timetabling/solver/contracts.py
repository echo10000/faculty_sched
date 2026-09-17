"""Immutable primitive contracts shared by timetable preprocessing and solving.

Only the scoped ORM adapter may construct these DTOs in production. It must
resolve identifiers and validate active state, scope, and forged IDs first;
these pure contracts intentionally assume that validation has already
succeeded and carry no ORM eligibility state.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DemandKey = tuple[int, int]
CandidateCounts = tuple[tuple[DemandKey, int], ...]


def _candidate_count_for(
    candidate_counts: CandidateCounts,
    key: DemandKey,
) -> int:
    for demand_key, count in candidate_counts:
        if demand_key == key:
            return count
    raise KeyError(key)


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    faculty_preference: int = 0
    faculty_gap: int = 0
    section_gap: int = 0
    meeting_distribution: int = 0
    room_fit: int = 0


@dataclass(frozen=True, slots=True)
class SchedulingPolicy:
    earliest_minute: int
    latest_minute: int
    slot_increment_minutes: int
    allowed_weekdays: tuple[int, ...]
    solver_time_limit_seconds: int
    random_seed: int
    worker_count: int
    weights: ObjectiveWeights

    @property
    def slot_count(self) -> int:
        return (self.latest_minute - self.earliest_minute) // self.slot_increment_minutes


@dataclass(frozen=True, slots=True)
class MeetingDemand:
    assignment_id: int
    meeting_requirement_id: int
    occurrence_index: int
    faculty_id: int
    section_id: int
    meeting_type: str
    duration_slots: int
    expected_size: int | None
    required_room_type_id: int | None
    room_type_mandatory: bool
    capacity_is_hard: bool

    @property
    def key(self) -> DemandKey:
        return self.meeting_requirement_id, self.occurrence_index


@dataclass(frozen=True, slots=True)
class FixedMeeting:
    assignment_id: int | None
    faculty_id: int
    section_id: int
    room_id: int
    day_of_week: int
    start_slot: int
    end_slot: int
    meeting_type: str | None
    counts_for_distribution: bool


@dataclass(frozen=True, slots=True)
class RoomOption:
    room_id: int
    room_type_id: int | None
    capacity: int


@dataclass(frozen=True, slots=True)
class WeeklyBlock:
    resource_id: int
    day_of_week: int
    start_slot: int
    end_slot: int


@dataclass(frozen=True, slots=True)
class CandidatePlacement:
    demand_key: DemandKey
    assignment_id: int
    faculty_id: int
    section_id: int
    room_id: int
    day_of_week: int
    start_slot: int
    end_slot: int
    meeting_type: str
    preferred_penalty: int
    room_fit_penalty: int


@dataclass(frozen=True, slots=True)
class CandidateLimits:
    max_seconds: int
    max_candidates: int
    max_slot_literals: int


@dataclass(frozen=True, slots=True)
class CandidateBuildResult:
    candidates: tuple[CandidatePlacement, ...]
    candidate_counts: CandidateCounts
    issues: tuple[ReadinessIssue, ...]

    def candidate_count_for(self, key: DemandKey) -> int:
        """Return the candidate count for ``key``, raising ``KeyError`` if absent."""

        return _candidate_count_for(self.candidate_counts, key)


@dataclass(frozen=True, slots=True)
class SolverInput:
    policy: SchedulingPolicy
    demands: tuple[MeetingDemand, ...]
    candidates: tuple[CandidatePlacement, ...]
    fixed_meetings: tuple[FixedMeeting, ...]
    candidate_counts: CandidateCounts

    def candidate_count_for(self, key: DemandKey) -> int:
        """Return the candidate count for ``key``, raising ``KeyError`` if absent."""

        return _candidate_count_for(self.candidate_counts, key)


@dataclass(frozen=True, slots=True)
class ProposedMeeting:
    assignment_id: int
    meeting_requirement_id: int
    occurrence_index: int
    room_id: int
    day_of_week: int
    start_slot: int
    end_slot: int
    meeting_type: str

    @property
    def demand_key(self) -> DemandKey:
        return self.meeting_requirement_id, self.occurrence_index


@dataclass(frozen=True, slots=True)
class ReadinessIssue:
    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class PenaltyBreakdown:
    faculty_preference: int = 0
    faculty_gap: int = 0
    section_gap: int = 0
    meeting_distribution: int = 0
    room_fit: int = 0


@dataclass(frozen=True, slots=True)
class SolverStatistics:
    wall_time_seconds: float = 0.0
    branches: int = 0
    conflicts: int = 0
    candidate_count: int = 0
    variable_count: int = 0
    generated_meeting_count: int = 0
    random_seed: int = 0
    worker_count: int = 0
    time_limit_seconds: int = 0


@dataclass(frozen=True, slots=True)
class SolverResult:
    raw_status: str
    proposals: tuple[ProposedMeeting, ...] = ()
    objective_value: int | None = None
    best_bound: float | None = None
    penalties: PenaltyBreakdown = field(default_factory=PenaltyBreakdown)
    statistics: SolverStatistics = field(default_factory=SolverStatistics)
    issues: tuple[ReadinessIssue, ...] = ()

"""Bounded deterministic expansion of sparse timetable candidates."""

from __future__ import annotations

import time
from collections.abc import Callable
from operator import attrgetter

from .contracts import (
    CandidateBuildResult,
    CandidateLimits,
    CandidatePlacement,
    FixedMeeting,
    MeetingDemand,
    ReadinessIssue,
    RoomOption,
    SchedulingPolicy,
    WeeklyBlock,
)


MODEL_SIZE_ISSUE = ReadinessIssue(
    "MODEL_SIZE_LIMIT",
    "ERROR",
    "Candidate model exceeds the configured deployment limit.",
)
TIMEOUT_ISSUE = ReadinessIssue(
    "PREPROCESSING_TIMEOUT",
    "ERROR",
    "Candidate preprocessing exceeded its configured time limit.",
)


def _overlaps(start_slot: int, end_slot: int, other_start: int, other_end: int) -> bool:
    return start_slot < other_end and other_start < end_slot


def _demand_order(demand: MeetingDemand) -> tuple[int, str, int, int]:
    return (
        demand.assignment_id,
        demand.meeting_type,
        demand.occurrence_index,
        demand.meeting_requirement_id,
    )


def _blocked_by_resource(
    blocks: tuple[WeeklyBlock, ...],
    resource_id: int,
    day_of_week: int,
    start_slot: int,
    end_slot: int,
) -> bool:
    return any(
        block.resource_id == resource_id
        and block.day_of_week == day_of_week
        and _overlaps(start_slot, end_slot, block.start_slot, block.end_slot)
        for block in blocks
    )


def _blocked_by_fixed_meeting(
    fixed_meetings: tuple[FixedMeeting, ...],
    demand: MeetingDemand,
    room: RoomOption,
    day_of_week: int,
    start_slot: int,
    end_slot: int,
) -> bool:
    return any(
        fixed.day_of_week == day_of_week
        and _overlaps(start_slot, end_slot, fixed.start_slot, fixed.end_slot)
        and (
            fixed.faculty_id == demand.faculty_id
            or fixed.room_id == room.room_id
            or fixed.section_id == demand.section_id
        )
        for fixed in fixed_meetings
    )


def _placement_is_hard_invalid(
    demand: MeetingDemand,
    room: RoomOption,
    day_of_week: int,
    start_slot: int,
    policy: SchedulingPolicy,
    unavailable: tuple[WeeklyBlock, ...],
    room_closures: tuple[WeeklyBlock, ...],
    fixed_meetings: tuple[FixedMeeting, ...],
) -> bool:
    end_slot = start_slot + demand.duration_slots
    if (
        day_of_week not in policy.allowed_weekdays
        or demand.duration_slots <= 0
        or start_slot < 0
        or end_slot > policy.slot_count
    ):
        return True
    if (
        demand.room_type_mandatory
        and room.room_type_id != demand.required_room_type_id
    ):
        return True
    if (
        demand.capacity_is_hard
        and demand.expected_size is not None
        and room.capacity < demand.expected_size
    ):
        return True
    if _blocked_by_resource(
        unavailable,
        demand.faculty_id,
        day_of_week,
        start_slot,
        end_slot,
    ):
        return True
    if _blocked_by_resource(
        room_closures,
        room.room_id,
        day_of_week,
        start_slot,
        end_slot,
    ):
        return True
    return _blocked_by_fixed_meeting(
        fixed_meetings,
        demand,
        room,
        day_of_week,
        start_slot,
        end_slot,
    )


def _preferred_penalty(
    demand: MeetingDemand,
    day_of_week: int,
    start_slot: int,
    end_slot: int,
    preferred: tuple[WeeklyBlock, ...],
) -> int:
    faculty_preferences = tuple(
        block for block in preferred if block.resource_id == demand.faculty_id
    )
    if not faculty_preferences:
        return 0
    fully_preferred = any(
        block.day_of_week == day_of_week
        and block.start_slot <= start_slot
        and end_slot <= block.end_slot
        for block in faculty_preferences
    )
    return int(not fully_preferred)


def _room_fit_penalty(demand: MeetingDemand, room: RoomOption) -> int:
    optional_type_mismatch = int(
        demand.required_room_type_id is not None
        and room.room_type_id != demand.required_room_type_id
    )
    capacity_slack = (
        0
        if demand.expected_size is None
        else max(0, room.capacity - demand.expected_size)
    )
    return optional_type_mismatch + capacity_slack


def _candidate_for(
    demand: MeetingDemand,
    room: RoomOption,
    day_of_week: int,
    start_slot: int,
    preferred: tuple[WeeklyBlock, ...],
) -> CandidatePlacement:
    end_slot = start_slot + demand.duration_slots
    return CandidatePlacement(
        demand_key=demand.key,
        assignment_id=demand.assignment_id,
        faculty_id=demand.faculty_id,
        section_id=demand.section_id,
        room_id=room.room_id,
        day_of_week=day_of_week,
        start_slot=start_slot,
        end_slot=end_slot,
        meeting_type=demand.meeting_type,
        preferred_penalty=_preferred_penalty(
            demand,
            day_of_week,
            start_slot,
            end_slot,
            preferred,
        ),
        room_fit_penalty=_room_fit_penalty(demand, room),
    )


def build_candidates(
    *,
    policy: SchedulingPolicy,
    demands: tuple[MeetingDemand, ...],
    rooms: tuple[RoomOption, ...],
    unavailable: tuple[WeeklyBlock, ...],
    room_closures: tuple[WeeklyBlock, ...],
    fixed_meetings: tuple[FixedMeeting, ...],
    preferred: tuple[WeeklyBlock, ...],
    limits: CandidateLimits,
    clock: Callable[[], float] = time.monotonic,
) -> CandidateBuildResult:
    """Return hard-valid candidates, or one deployment-cap issue and no candidates."""

    started = clock()
    ordered_demands = sorted(demands, key=_demand_order)
    ordered_rooms = sorted(rooms, key=attrgetter("room_id"))
    ordered_days = sorted(policy.allowed_weekdays)
    candidates: list[CandidatePlacement] = []
    counts = {demand.key: 0 for demand in ordered_demands}
    slot_literals = 0

    for demand in ordered_demands:
        start_count = policy.slot_count - demand.duration_slots + 1
        for day_of_week in ordered_days:
            for start_slot in range(max(0, start_count)):
                for room in ordered_rooms:
                    if _placement_is_hard_invalid(
                        demand,
                        room,
                        day_of_week,
                        start_slot,
                        policy,
                        unavailable,
                        room_closures,
                        fixed_meetings,
                    ):
                        continue
                    candidates.append(
                        _candidate_for(
                            demand,
                            room,
                            day_of_week,
                            start_slot,
                            preferred,
                        )
                    )
                    counts[demand.key] += 1
                    slot_literals += demand.duration_slots
                    if (
                        len(candidates) > limits.max_candidates
                        or slot_literals > limits.max_slot_literals
                    ):
                        return CandidateBuildResult((), counts, (MODEL_SIZE_ISSUE,))
                if clock() - started > limits.max_seconds:
                    return CandidateBuildResult((), counts, (TIMEOUT_ISSUE,))

    return CandidateBuildResult(tuple(candidates), counts, ())

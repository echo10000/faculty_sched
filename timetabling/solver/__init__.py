"""Pure timetable candidate and solver contracts."""

from .candidates import build_candidates
from .contracts import (
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

__all__ = (
    "CandidateBuildResult",
    "CandidateLimits",
    "CandidatePlacement",
    "FixedMeeting",
    "MeetingDemand",
    "ObjectiveWeights",
    "PenaltyBreakdown",
    "ProposedMeeting",
    "ReadinessIssue",
    "RoomOption",
    "SchedulingPolicy",
    "SolverInput",
    "SolverResult",
    "SolverStatistics",
    "WeeklyBlock",
    "build_candidates",
)

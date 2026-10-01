"""Scoped ORM capture and readiness checks for timetable generation."""

from __future__ import annotations

from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.shortcuts import get_object_or_404

from accounts.permissions import require_access
from resources.models import RoomType
from resources.selectors import available_resources
from scheduling.models import Room
from workloads.models import (
    FacultyAvailability,
    FacultySubjectAssignment,
    SubjectOffering,
)
from workloads.selectors import scoped_records

from .conflicts import validate_candidate_schedule
from .intervals import duration_minutes, terms_share_weekday
from .models import (
    AssignmentMeetingRequirement,
    OfferingRequirement,
    RoomUnavailability,
    ScheduleGenerationRun,
    SchedulingConfiguration,
)
from .occupancy import authoritative_occupancy
from .queries import authorized, entry_queryset, get_schedule, scoped
from .signatures import dependency_signature
from .solver.candidates import build_candidates
from .solver.contracts import (
    CandidateLimits,
    FixedMeeting,
    MeetingDemand,
    ObjectiveWeights,
    ReadinessIssue,
    RoomOption,
    SchedulingPolicy,
    SolverInput,
    WeeklyBlock,
)


GENERATION_PERMISSIONS = (
    "timetabling.generate_schedule",
    "timetabling.change_schedule",
    "timetabling.add_scheduleentry",
    "timetabling.view_scheduleentry",
    "timetabling.view_offeringrequirement",
    "timetabling.view_assignmentmeetingrequirement",
    "timetabling.view_schedulingconfiguration",
    "timetabling.view_roomunavailability",
    "academics.view_academicterm",
    "workloads.view_workload",
    "workloads.view_facultyavailability",
    "workloads.view_facultysubjectassignment",
    "scheduling.view_room",
)

MEETING_TYPES = ("lecture", "laboratory")
OVERRIDE_FIELDS = (
    "solver_time_limit_seconds",
    "faculty_preference_weight",
    "faculty_gap_weight",
    "section_gap_weight",
    "meeting_distribution_weight",
    "room_fit_weight",
)
IGNORED_CANONICAL_REQUIREMENT_CODES = {
    "MEETING_REQUIREMENT_COUNT",
    "MEETING_REQUIREMENT_DURATION",
    "MEETING_REQUIREMENT_MISSING",
    "MEETING_HOURS_WARNING",
}


@dataclass(frozen=True, slots=True)
class GenerationOverrides:
    solver_time_limit_seconds: int | None = None
    faculty_preference_weight: int | None = None
    faculty_gap_weight: int | None = None
    section_gap_weight: int | None = None
    meeting_distribution_weight: int | None = None
    room_fit_weight: int | None = None


@dataclass(frozen=True, slots=True)
class PreparedGeneration:
    schedule_id: int
    configuration_id: int | None
    source_signature: str
    configuration_snapshot: dict
    input_summary: dict
    solver_input: SolverInput | None
    issues: tuple[ReadinessIssue, ...]
    retained_entry_ids: tuple[int, ...]
    replace_entry_ids: tuple[int, ...]


class _Issues:
    def __init__(self):
        self._items: list[ReadinessIssue] = []
        self._seen: set[tuple[str, object]] = set()

    def add(
        self,
        code: str,
        severity: str,
        message: str,
        affected: object = None,
    ) -> None:
        marker = (code, affected)
        if marker in self._seen:
            return
        self._seen.add(marker)
        self._items.append(ReadinessIssue(code, severity, message))

    def has_errors(self) -> bool:
        return any(item.severity == "ERROR" for item in self._items)

    def freeze(self) -> tuple[ReadinessIssue, ...]:
        return tuple(self._items)


def _minute(value) -> int:
    return value.hour * 60 + value.minute


def _decimal_minute(value) -> Decimal:
    return (
        Decimal(value.hour * 3600 + value.minute * 60 + value.second)
        + Decimal(value.microsecond) / Decimal(1_000_000)
    ) / Decimal(60)


def _floor_slot(value, *, earliest_minute: int, increment: int) -> int:
    relative = (_decimal_minute(value) - Decimal(earliest_minute)) / Decimal(
        increment
    )
    return int(relative.to_integral_value(rounding=ROUND_FLOOR))


def _ceil_slot(value, *, earliest_minute: int, increment: int) -> int:
    relative = (_decimal_minute(value) - Decimal(earliest_minute)) / Decimal(
        increment
    )
    return int(relative.to_integral_value(rounding=ROUND_CEILING))


def _hard_block(
    resource_id: int,
    day_of_week: int,
    start_time,
    end_time,
    *,
    policy: SchedulingPolicy,
) -> WeeklyBlock:
    return WeeklyBlock(
        resource_id=resource_id,
        day_of_week=day_of_week,
        start_slot=_floor_slot(
            start_time,
            earliest_minute=policy.earliest_minute,
            increment=policy.slot_increment_minutes,
        ),
        end_slot=_ceil_slot(
            end_time,
            earliest_minute=policy.earliest_minute,
            increment=policy.slot_increment_minutes,
        ),
    )


def _preferred_block(
    resource_id: int,
    day_of_week: int,
    start_time,
    end_time,
    *,
    policy: SchedulingPolicy,
) -> WeeklyBlock | None:
    start_slot = _ceil_slot(
        start_time,
        earliest_minute=policy.earliest_minute,
        increment=policy.slot_increment_minutes,
    )
    end_slot = _floor_slot(
        end_time,
        earliest_minute=policy.earliest_minute,
        increment=policy.slot_increment_minutes,
    )
    if start_slot >= end_slot:
        return None
    return WeeklyBlock(resource_id, day_of_week, start_slot, end_slot)


def require_generation_access(user, strategy) -> None:
    for permission in GENERATION_PERMISSIONS:
        require_access(user, permission)
    if strategy == ScheduleGenerationRun.Strategy.REPLACE_UNLOCKED:
        require_access(user, "timetabling.delete_scheduleentry")


def generation_run_queryset(user):
    authorized(user, ScheduleGenerationRun)
    return scoped(user, ScheduleGenerationRun.objects.all()).select_related(
        "schedule__academic_term__academic_year",
        "schedule__academic_term__semester",
        "schedule__department__college",
        "requested_by",
        "accepted_by",
    )


def get_generation_run(user, pk, *, lock=False):
    queryset = generation_run_queryset(user)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return get_object_or_404(queryset, pk=pk)


def _configuration_snapshot(configuration: SchedulingConfiguration) -> dict:
    return {
        "configuration_id": configuration.pk,
        "academic_term_id": configuration.academic_term_id,
        "department_id": configuration.department_id,
        "allowed_weekdays": list(configuration.allowed_weekdays),
        "earliest_start": configuration.earliest_start.strftime("%H:%M"),
        "latest_end": configuration.latest_end.strftime("%H:%M"),
        "slot_increment_minutes": configuration.slot_increment_minutes,
        "solver_time_limit_seconds": configuration.solver_time_limit_seconds,
        "random_seed": configuration.random_seed,
        "worker_count": configuration.worker_count,
        "faculty_preference_weight": configuration.faculty_preference_weight,
        "faculty_gap_weight": configuration.faculty_gap_weight,
        "section_gap_weight": configuration.section_gap_weight,
        "meeting_distribution_weight": configuration.meeting_distribution_weight,
        "room_fit_weight": configuration.room_fit_weight,
    }


def _policy_for(configuration, term) -> SchedulingPolicy:
    configured_days = tuple(configuration.allowed_weekdays)
    allowed_days = tuple(
        day
        for day in configured_days
        if terms_share_weekday(term, term, day)
    )
    return SchedulingPolicy(
        earliest_minute=_minute(configuration.earliest_start),
        latest_minute=_minute(configuration.latest_end),
        slot_increment_minutes=configuration.slot_increment_minutes,
        allowed_weekdays=allowed_days,
        solver_time_limit_seconds=configuration.solver_time_limit_seconds,
        random_seed=configuration.random_seed,
        worker_count=configuration.worker_count,
        weights=ObjectiveWeights(
            faculty_preference=configuration.faculty_preference_weight,
            faculty_gap=configuration.faculty_gap_weight,
            section_gap=configuration.section_gap_weight,
            meeting_distribution=configuration.meeting_distribution_weight,
            room_fit=configuration.room_fit_weight,
        ),
    )


def _schedule_readiness(schedule, issues: _Issues) -> None:
    term = schedule.academic_term
    resources = (
        term,
        term.academic_year,
        term.semester,
        schedule.department,
        schedule.department.college,
    )
    if any(not resource.is_active for resource in resources):
        issues.add(
            "INACTIVE_RESOURCE",
            "ERROR",
            "The schedule uses an inactive calendar or organization resource.",
            ("schedule", schedule.pk),
        )
    if (
        term.start_date < term.academic_year.start_date
        or term.end_date > term.academic_year.end_date
    ):
        issues.add(
            "TERM_MISMATCH",
            "ERROR",
            "The academic term falls outside its academic year.",
            ("schedule", schedule.pk),
        )


def _offering_readiness(offering, offering_requirement, issues: _Issues) -> bool:
    subject = offering.subject
    active_resources = (
        offering,
        subject,
        offering.department,
        offering.department.college,
        offering.academic_term,
        offering.academic_term.academic_year,
        offering.academic_term.semester,
    )
    valid = True
    if any(not resource.is_active for resource in active_resources):
        issues.add(
            "INACTIVE_RESOURCE",
            "ERROR",
            "An offering uses an inactive catalog, calendar or organization resource.",
            ("offering", offering.pk),
        )
        valid = False
    if subject.owning_department_id != offering.department_id:
        issues.add(
            "ORGANIZATION_MISMATCH",
            "ERROR",
            "An offering and its catalog subject belong to different departments.",
            ("offering", offering.pk),
        )
        valid = False
    if offering_requirement is None:
        issues.add(
            "SECTION_REQUIRED",
            "ERROR",
            "An active offering has no class section configured.",
            ("offering", offering.pk),
        )
        return False

    section = offering_requirement.section
    if not section.is_active:
        issues.add(
            "INACTIVE_RESOURCE",
            "ERROR",
            "An offering uses an inactive class section.",
            ("section", section.pk),
        )
        valid = False
    if section.academic_term_id != offering.academic_term_id:
        issues.add(
            "TERM_MISMATCH",
            "ERROR",
            "An offering and its class section use different academic terms.",
            ("section", section.pk),
        )
        valid = False
    if section.department_id != offering.department_id or (
        section.program_id
        and section.program.department_id != section.department_id
    ):
        issues.add(
            "ORGANIZATION_MISMATCH",
            "ERROR",
            "An offering and its class section belong to different departments.",
            ("section", section.pk),
        )
        valid = False
    if offering_requirement.room_type_id and not offering_requirement.room_type.is_active:
        issues.add(
            "INACTIVE_RESOURCE",
            "ERROR",
            "An offering requirement uses an inactive room type.",
            ("room_type", offering_requirement.room_type_id),
        )
        valid = False
    return valid


def _assignment_readiness(assignment, schedule, issues: _Issues) -> bool:
    faculty = assignment.faculty
    offering = assignment.subject_offering
    valid = True
    if any(
        not resource.is_active
        for resource in (
            faculty,
            faculty.home_department,
            faculty.home_department.college,
            offering,
            offering.subject,
        )
    ):
        issues.add(
            "INACTIVE_RESOURCE",
            "ERROR",
            "A teaching assignment uses an inactive faculty or offering resource.",
            ("assignment", assignment.pk),
        )
        valid = False
    if offering.academic_term_id != schedule.academic_term_id:
        issues.add(
            "TERM_MISMATCH",
            "ERROR",
            "A teaching assignment belongs to a different academic term.",
            ("assignment", assignment.pk),
        )
        valid = False
    if (
        offering.department_id != schedule.department_id
        or faculty.home_department_id != schedule.department_id
    ):
        issues.add(
            "ORGANIZATION_MISMATCH",
            "ERROR",
            "A teaching assignment belongs to a different department.",
            ("assignment", assignment.pk),
        )
        valid = False
    return valid


def _selected_fixed_meeting(entry, section_id, policy) -> FixedMeeting | None:
    if (
        entry.day_of_week not in policy.allowed_weekdays
        or entry.start_time.second
        or entry.start_time.microsecond
        or entry.end_time.second
        or entry.end_time.microsecond
    ):
        return None
    start_minute = _minute(entry.start_time)
    end_minute = _minute(entry.end_time)
    if (
        start_minute < policy.earliest_minute
        or end_minute > policy.latest_minute
        or start_minute >= end_minute
        or (start_minute - policy.earliest_minute)
        % policy.slot_increment_minutes
        or (end_minute - policy.earliest_minute)
        % policy.slot_increment_minutes
    ):
        return None
    return FixedMeeting(
        assignment_id=entry.assignment_id,
        faculty_id=entry.assignment.faculty_id,
        section_id=section_id,
        room_id=entry.room_id,
        day_of_week=entry.day_of_week,
        start_slot=(start_minute - policy.earliest_minute)
        // policy.slot_increment_minutes,
        end_slot=(end_minute - policy.earliest_minute)
        // policy.slot_increment_minutes,
        meeting_type=entry.meeting_type,
        counts_for_distribution=True,
    )


def _peer_section_id(entry) -> int:
    try:
        return entry.assignment.subject_offering.scheduling_requirement.section_id
    except ObjectDoesNotExist:
        return -entry.pk


def _peer_fixed_meeting(entry, policy) -> FixedMeeting:
    return FixedMeeting(
        assignment_id=None,
        faculty_id=entry.assignment.faculty_id,
        section_id=_peer_section_id(entry),
        room_id=entry.room_id,
        day_of_week=entry.day_of_week,
        start_slot=_floor_slot(
            entry.start_time,
            earliest_minute=policy.earliest_minute,
            increment=policy.slot_increment_minutes,
        ),
        end_slot=_ceil_slot(
            entry.end_time,
            earliest_minute=policy.earliest_minute,
            increment=policy.slot_increment_minutes,
        ),
        meeting_type=None,
        counts_for_distribution=False,
    )


def _empty_summary() -> dict:
    return {
        "offering_ids": [],
        "assignment_ids": [],
        "meeting_requirement_ids": [],
        "section_ids": [],
        "eligible_room_ids": [],
        "retained_entry_ids": [],
        "replace_entry_ids": [],
        "remaining_demands": [],
        "candidate_counts": [],
        "availability_count": 0,
        "unavailable_count": 0,
        "preferred_count": 0,
        "closure_count": 0,
        "peer_occupancy_count": 0,
        "retained_entry_count": 0,
        "replace_entry_count": 0,
        "current_entry_count": 0,
        "candidate_count": 0,
        "slot_literal_count": 0,
    }


def prepare_generation_input(
    *,
    user,
    schedule_id: int,
    strategy: str,
    overrides: GenerationOverrides,
) -> PreparedGeneration:
    if strategy not in ScheduleGenerationRun.Strategy.values:
        raise ValueError("Unknown timetable generation strategy.")
    if not isinstance(overrides, GenerationOverrides):
        raise TypeError("overrides must be a GenerationOverrides instance.")

    require_generation_access(user, strategy)
    schedule = get_schedule(user, schedule_id, action="change")
    issues = _Issues()
    _schedule_readiness(schedule, issues)

    configurations = list(
        scoped(
            user,
            SchedulingConfiguration.objects.filter(
                academic_term_id=schedule.academic_term_id,
                department_id=schedule.department_id,
            ),
        ).order_by("pk")
    )
    configuration = configurations[0] if configurations else None
    effective_configuration = None
    configuration_snapshot = {}
    policy = None
    if configuration is None:
        issues.add(
            "CONFIGURATION_MISSING",
            "ERROR",
            "Configure timetable generation for this term and department.",
            ("configuration", schedule.pk),
        )
    else:
        effective_configuration = copy(configuration)
        for field_name in OVERRIDE_FIELDS:
            value = getattr(overrides, field_name)
            if value is not None:
                setattr(effective_configuration, field_name, value)
        try:
            effective_configuration.full_clean(
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError:
            issues.add(
                "CONFIGURATION_INVALID",
                "ERROR",
                "The effective timetable generation configuration is invalid.",
                ("configuration", configuration.pk),
            )
        else:
            configuration_snapshot = _configuration_snapshot(
                effective_configuration
            )
            policy = _policy_for(effective_configuration, schedule.academic_term)

    offerings = list(
        scoped_records(
            user,
            SubjectOffering.objects.filter(
                academic_term_id=schedule.academic_term_id,
                department_id=schedule.department_id,
                is_active=True,
            ),
        )
        .select_related(
            "subject__owning_department__college",
            "academic_term__academic_year",
            "academic_term__semester",
            "department__college",
        )
        .order_by("pk")
    )
    offering_ids = tuple(offering.pk for offering in offerings)

    assignments = list(
        scoped_records(
            user,
            FacultySubjectAssignment.objects.filter(
                subject_offering_id__in=offering_ids
            ),
        )
        .select_related(
            "faculty__home_department__college",
            "subject_offering__subject__owning_department__college",
            "subject_offering__academic_term__academic_year",
            "subject_offering__academic_term__semester",
            "subject_offering__department__college",
        )
        .order_by("subject_offering_id", "pk")
    )
    assignment_ids = tuple(assignment.pk for assignment in assignments)

    offering_requirements = list(
        scoped(
            user,
            OfferingRequirement.objects.filter(
                subject_offering_id__in=offering_ids
            ),
        )
        .select_related(
            "subject_offering",
            "section__program__department",
            "section__department__college",
            "room_type",
        )
        .order_by("subject_offering_id", "pk")
    )
    offering_requirement_by_offering = {
        requirement.subject_offering_id: requirement
        for requirement in offering_requirements
    }

    meeting_requirements = list(
        scoped(
            user,
            AssignmentMeetingRequirement.objects.filter(
                assignment_id__in=assignment_ids
            ),
        )
        .select_related("assignment__subject_offering")
        .order_by("assignment_id", "meeting_type", "pk")
    )
    requirements_by_assignment_type = defaultdict(list)
    for requirement in meeting_requirements:
        requirements_by_assignment_type[
            (requirement.assignment_id, requirement.meeting_type)
        ].append(requirement)

    faculty_ids = tuple(sorted({item.faculty_id for item in assignments}))
    availability = list(
        scoped_records(
            user,
            FacultyAvailability.objects.filter(
                academic_term_id=schedule.academic_term_id,
                faculty_id__in=faculty_ids,
            ),
        )
        .select_related("faculty__home_department__college", "academic_term")
        .order_by("faculty_id", "day_of_week", "start_time", "pk")
    )

    candidate_rooms = list(
        available_resources(user, Room)
        .select_related(
            "category",
            "building",
            "owner_department__college",
            "owner_college",
        )
        .order_by("pk")
    )
    eligible_rooms = []
    for room in candidate_rooms:
        if (room.category_id and not room.category.is_active) or (
            room.building_id and not room.building.is_active
        ):
            issues.add(
                "INACTIVE_RESOURCE",
                "ERROR",
                "An otherwise eligible room has an inactive type or building.",
                ("room", room.pk),
            )
            continue
        eligible_rooms.append(room)
    eligible_room_ids = tuple(room.pk for room in eligible_rooms)

    closures = [
        closure
        for closure in scoped(
            user,
            RoomUnavailability.objects.filter(
                academic_term__start_date__lte=schedule.academic_term.end_date,
                academic_term__end_date__gte=schedule.academic_term.start_date,
            ),
        )
        .select_related("academic_term", "room")
        .order_by("room_id", "day_of_week", "start_time", "pk")
        if terms_share_weekday(
            schedule.academic_term,
            closure.academic_term,
            closure.day_of_week,
        )
    ]

    current_entries = list(
        entry_queryset().filter(schedule=schedule).order_by("pk")
    )
    current_entry_ids = tuple(entry.pk for entry in current_entries)
    if strategy == ScheduleGenerationRun.Strategy.FILL_GAPS:
        retained_entries = current_entries
        replace_entries = []
    else:
        retained_entries = [entry for entry in current_entries if entry.is_locked]
        replace_entries = [entry for entry in current_entries if not entry.is_locked]
    retained_entry_ids = tuple(entry.pk for entry in retained_entries)
    replace_entry_ids = tuple(entry.pk for entry in replace_entries)

    peers = tuple(
        entry
        for entry in authoritative_occupancy(
            schedule,
            excluded_entry_ids=current_entry_ids,
        )
        if terms_share_weekday(
            schedule.academic_term,
            entry.schedule.academic_term,
            entry.day_of_week,
        )
    )

    assignments_by_offering = defaultdict(list)
    assignment_validity = {}
    for assignment in assignments:
        assignments_by_offering[assignment.subject_offering_id].append(assignment)
        assignment_validity[assignment.pk] = _assignment_readiness(
            assignment,
            schedule,
            issues,
        )

    offering_validity = {}
    for offering in offerings:
        offering_requirement = offering_requirement_by_offering.get(offering.pk)
        offering_validity[offering.pk] = _offering_readiness(
            offering,
            offering_requirement,
            issues,
        )
        offering_assignments = assignments_by_offering.get(offering.pk, ())
        if not offering_assignments:
            issues.add(
                "ASSIGNMENT_REQUIRED",
                "ERROR",
                "An active offering has no teaching assignment.",
                ("offering", offering.pk),
            )
            continue
        share_total = sum(
            (Decimal(assignment.share) for assignment in offering_assignments),
            Decimal("0"),
        )
        if share_total < Decimal("1.0"):
            issues.add(
                "ASSIGNMENT_SHARES_INCOMPLETE",
                "ERROR",
                "Teaching assignment shares for an offering total less than one.",
                ("offering", offering.pk),
            )
        elif share_total > Decimal("1.0"):
            issues.add(
                "ASSIGNMENT_SHARES_EXCESS",
                "ERROR",
                "Teaching assignment shares for an offering total more than one.",
                ("offering", offering.pk),
            )

    valid_requirements = {}
    for requirement in meeting_requirements:
        if requirement.meeting_type not in MEETING_TYPES:
            issues.add(
                "MEETING_REQUIREMENT_CONTRADICTORY",
                "ERROR",
                "A meeting requirement has an unsupported meeting type.",
                ("meeting_requirement", requirement.pk),
            )

    for assignment in assignments:
        offering = assignment.subject_offering
        for meeting_type in MEETING_TYPES:
            required_minutes = (
                Decimal(getattr(offering, f"{meeting_type}_hours"))
                * Decimal(assignment.share)
                * Decimal(60)
            )
            matching = requirements_by_assignment_type.get(
                (assignment.pk, meeting_type),
                (),
            )
            affected = ("assignment_component", assignment.pk, meeting_type)
            if required_minutes > 0 and not matching:
                issues.add(
                    "MEETING_REQUIREMENT_MISSING",
                    "ERROR",
                    "A positive teaching component has no exact meeting requirement.",
                    affected,
                )
                continue
            if len(matching) > 1:
                issues.add(
                    "MEETING_REQUIREMENT_DUPLICATE",
                    "ERROR",
                    "A teaching component has duplicate meeting requirements.",
                    affected,
                )
                continue
            if not matching:
                continue
            requirement = matching[0]
            contradictory = (
                required_minutes <= 0
                or requirement.assignment_id != assignment.pk
                or requirement.meeting_type != meeting_type
                or type(requirement.meetings_per_week) is not int
                or requirement.meetings_per_week <= 0
                or type(requirement.duration_minutes) is not int
                or requirement.duration_minutes <= 0
                or Decimal(
                    requirement.meetings_per_week
                    * requirement.duration_minutes
                )
                != required_minutes
            )
            if contradictory:
                issues.add(
                    "MEETING_REQUIREMENT_CONTRADICTORY",
                    "ERROR",
                    "A meeting requirement contradicts its authoritative component minutes.",
                    affected,
                )
            if (
                policy is not None
                and (
                    requirement.duration_minutes <= 0
                    or requirement.duration_minutes
                    % policy.slot_increment_minutes
                )
            ):
                issues.add(
                    "MEETING_REQUIREMENT_GRID_MISMATCH",
                    "ERROR",
                    "A meeting duration is not a positive multiple of the slot increment.",
                    affected,
                )
            if not contradictory and (
                policy is None
                or requirement.duration_minutes % policy.slot_increment_minutes == 0
            ):
                valid_requirements[(assignment.pk, meeting_type)] = requirement

    canonical_conflicts = (
        validate_candidate_schedule(
            schedule,
            retained_entries=retained_entries,
            proposed_entries=(),
            user=user,
        )
        if retained_entries
        else ()
    )
    for conflict in canonical_conflicts:
        if conflict.code in IGNORED_CANONICAL_REQUIREMENT_CODES:
            continue
        issues.add(
            conflict.code,
            conflict.severity,
            conflict.message,
            ("fixed_entry", conflict.entry_id, conflict.other_entry_id),
        )

    fixed_meetings = []
    consumed = defaultdict(int)
    if policy is not None:
        for entry in retained_entries:
            requirement = valid_requirements.get(
                (entry.assignment_id, entry.meeting_type)
            )
            offering_requirement = offering_requirement_by_offering.get(
                entry.assignment.subject_offering_id
            )
            if requirement is None or offering_requirement is None:
                issues.add(
                    "FIXED_ENTRY_NO_REQUIREMENT",
                    "ERROR",
                    "A retained meeting has no valid matching requirement.",
                    ("fixed_entry", entry.pk),
                )
                continue
            fixed = _selected_fixed_meeting(
                entry,
                offering_requirement.section_id,
                policy,
            )
            if (
                fixed is None
                or duration_minutes(entry.start_time, entry.end_time)
                != Decimal(requirement.duration_minutes)
            ):
                issues.add(
                    "FIXED_ENTRY_INVALID_DURATION",
                    "ERROR",
                    "A retained meeting has an invalid duration or slot-grid position.",
                    ("fixed_entry", entry.pk),
                )
                continue
            occurrence = consumed[(entry.assignment_id, entry.meeting_type)]
            if occurrence >= requirement.meetings_per_week:
                issues.add(
                    "FIXED_ENTRY_EXCESS",
                    "ERROR",
                    "Retained meetings exceed the configured weekly requirement.",
                    ("fixed_entry", entry.pk),
                )
                continue
            consumed[(entry.assignment_id, entry.meeting_type)] += 1
            fixed_meetings.append(fixed)

        fixed_meetings.extend(
            _peer_fixed_meeting(peer, policy) for peer in peers
        )

    active_room_types = {
        room_type.code: room_type
        for room_type in RoomType.objects.filter(is_active=True).order_by("pk")
    }
    inactive_room_type_codes = set(
        RoomType.objects.filter(is_active=False).values_list("code", flat=True)
    )
    demands = []
    for assignment in assignments:
        offering = assignment.subject_offering
        offering_requirement = offering_requirement_by_offering.get(offering.pk)
        if (
            not assignment_validity.get(assignment.pk)
            or not offering_validity.get(offering.pk)
            or offering_requirement is None
        ):
            continue
        explicit_room_type = (
            offering_requirement.room_type
            if offering_requirement.room_type_id
            else None
        )
        required_code = (
            explicit_room_type.code
            if explicit_room_type is not None
            else offering.subject.required_room_type
        )
        if explicit_room_type is not None:
            required_room_type_id = explicit_room_type.pk
            room_type_mandatory = offering_requirement.room_type_mandatory
        else:
            resolved = active_room_types.get(required_code)
            required_room_type_id = resolved.pk if resolved else None
            room_type_mandatory = bool(required_code)
            if required_code in inactive_room_type_codes:
                issues.add(
                    "INACTIVE_RESOURCE",
                    "ERROR",
                    "A catalog room-type requirement refers to an inactive room type.",
                    ("catalog_room_type", offering.pk),
                )
        for meeting_type in MEETING_TYPES:
            requirement = valid_requirements.get(
                (assignment.pk, meeting_type)
            )
            if requirement is None or policy is None:
                continue
            first_remaining = consumed[(assignment.pk, meeting_type)]
            for occurrence_index in range(
                first_remaining,
                requirement.meetings_per_week,
            ):
                demands.append(
                    MeetingDemand(
                        assignment_id=assignment.pk,
                        meeting_requirement_id=requirement.pk,
                        occurrence_index=occurrence_index,
                        faculty_id=assignment.faculty_id,
                        section_id=offering_requirement.section_id,
                        meeting_type=meeting_type,
                        duration_slots=requirement.duration_minutes
                        // policy.slot_increment_minutes,
                        expected_size=offering_requirement.section.expected_size,
                        required_room_type_id=required_room_type_id,
                        room_type_mandatory=room_type_mandatory,
                        capacity_is_hard=offering_requirement.capacity_is_hard,
                    )
                )
    demands = sorted(
        demands,
        key=lambda item: (
            item.meeting_requirement_id,
            item.occurrence_index,
        ),
    )

    room_options = tuple(
        RoomOption(room.pk, room.category_id, room.capacity)
        for room in eligible_rooms
    )
    for demand in demands:
        feasible = any(
            (
                not demand.room_type_mandatory
                or (
                    demand.required_room_type_id is not None
                    and room.room_type_id == demand.required_room_type_id
                )
            )
            and (
                not demand.capacity_is_hard
                or demand.expected_size is None
                or room.capacity >= demand.expected_size
            )
            for room in room_options
        )
        if not feasible:
            issues.add(
                "NO_ELIGIBLE_ROOM",
                "ERROR",
                "A remaining meeting demand has no eligible room.",
                ("demand", demand.key),
            )

    unavailable_blocks = ()
    preferred_blocks = ()
    closure_blocks = ()
    if policy is not None:
        unavailable_blocks = tuple(
            _hard_block(
                record.faculty_id,
                record.day_of_week,
                record.start_time,
                record.end_time,
                policy=policy,
            )
            for record in availability
            if record.availability_type == FacultyAvailability.Kind.UNAVAILABLE
        )
        preferred_blocks = tuple(
            block
            for block in (
                _preferred_block(
                    record.faculty_id,
                    record.day_of_week,
                    record.start_time,
                    record.end_time,
                    policy=policy,
                )
                for record in availability
                if record.availability_type == FacultyAvailability.Kind.PREFERRED
            )
            if block is not None
        )
        closure_blocks = tuple(
            _hard_block(
                closure.room_id,
                closure.day_of_week,
                closure.start_time,
                closure.end_time,
                policy=policy,
            )
            for closure in closures
        )

    source_signature = dependency_signature(schedule)
    summary = _empty_summary()
    summary.update(
        {
            "offering_ids": sorted(offering_ids),
            "assignment_ids": sorted(assignment_ids),
            "meeting_requirement_ids": sorted(
                requirement.pk for requirement in meeting_requirements
            ),
            "section_ids": sorted(
                {
                    requirement.section_id
                    for requirement in offering_requirements
                }
            ),
            "eligible_room_ids": sorted(eligible_room_ids),
            "retained_entry_ids": list(retained_entry_ids),
            "replace_entry_ids": list(replace_entry_ids),
            "remaining_demands": [list(demand.key) for demand in demands],
            "availability_count": len(availability),
            "unavailable_count": len(unavailable_blocks),
            "preferred_count": len(preferred_blocks),
            "closure_count": len(closure_blocks),
            "peer_occupancy_count": len(peers),
            "retained_entry_count": len(retained_entries),
            "replace_entry_count": len(replace_entries),
            "current_entry_count": len(current_entries),
        }
    )

    solver_input = None
    if not issues.has_errors() and policy is not None:
        result = build_candidates(
            policy=policy,
            demands=tuple(demands),
            rooms=room_options,
            unavailable=unavailable_blocks,
            room_closures=closure_blocks,
            fixed_meetings=tuple(fixed_meetings),
            preferred=preferred_blocks,
            limits=CandidateLimits(
                settings.SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS,
                settings.SCHEDULER_MAX_CANDIDATES,
                settings.SCHEDULER_MAX_SLOT_LITERALS,
            ),
        )
        for candidate_issue in result.issues:
            issues.add(
                candidate_issue.code,
                candidate_issue.severity,
                candidate_issue.message,
                ("candidate_build", candidate_issue.code),
            )
        summary["candidate_counts"] = [
            [key[0], key[1], count]
            for key, count in result.candidate_counts
        ]
        summary["candidate_count"] = len(result.candidates)
        summary["slot_literal_count"] = sum(
            candidate.end_slot - candidate.start_slot
            for candidate in result.candidates
        )
        if not any(issue.severity == "ERROR" for issue in result.issues):
            for demand in demands:
                if result.candidate_count_for(demand.key) == 0:
                    issues.add(
                        "ZERO_CANDIDATES",
                        "ERROR",
                        "A remaining meeting demand has no valid placement.",
                        ("demand", demand.key),
                    )
        if not issues.has_errors():
            solver_input = SolverInput(
                policy=policy,
                demands=tuple(demands),
                candidates=result.candidates,
                fixed_meetings=tuple(fixed_meetings),
                candidate_counts=result.candidate_counts,
            )

    return PreparedGeneration(
        schedule_id=schedule.pk,
        configuration_id=configuration.pk if configuration else None,
        source_signature=source_signature,
        configuration_snapshot=configuration_snapshot,
        input_summary=summary,
        solver_input=solver_input,
        issues=issues.freeze(),
        retained_entry_ids=retained_entry_ids,
        replace_entry_ids=replace_entry_ids,
    )

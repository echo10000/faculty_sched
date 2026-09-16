"""Canonical deterministic validation. Unsaved candidates are valid inputs."""

from dataclasses import asdict, dataclass
from decimal import Decimal

from scheduling.models import Room
from workloads.models import FacultyAvailability, FacultySubjectAssignment

from .intervals import DAYS, duration_minutes, overlaps, terms_share_weekday
from .models import (
    AssignmentMeetingRequirement,
    OfferingRequirement,
    RoomUnavailability,
    ScheduleEntry,
)
from .occupancy import authoritative_occupancy
from .queries import entry_queryset, scoped


@dataclass(frozen=True)
class Conflict:
    code: str
    severity: str
    message: str
    remedy: str
    entry_id: int | None = None
    other_entry_id: int | None = None

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class _RuleConflict:
    conflict: Conflict
    other: ScheduleEntry | None = None


def _visible_entry_ids(user):
    if user is None:
        return None
    if not user.has_perm("timetabling.view_scheduleentry"):
        return set()
    return set(
        scoped(user, ScheduleEntry.objects.all()).values_list("pk", flat=True)
    )


def _entry_rule_conflicts(entry, *, schedule, peers, visible_entry_ids):
    detected = []

    def add(
        code,
        severity,
        message,
        remedy="Choose another time or correct the resource configuration.",
        other=None,
    ):
        can_show_other = (
            other is None
            or other.pk is None
            or visible_entry_ids is None
            or other.pk in visible_entry_ids
        )
        detected.append(
            _RuleConflict(
                Conflict(
                    code,
                    severity,
                    message,
                    remedy,
                    entry.pk,
                    other.pk if other is not None and can_show_other else None,
                ),
                other,
            )
        )

    if (
        not entry.start_time
        or not entry.end_time
        or entry.start_time >= entry.end_time
        or entry.day_of_week not in dict(DAYS)
    ):
        add(
            "INVALID_TIME_RANGE",
            "ERROR",
            "Choose a valid weekday and a start time earlier than the end time.",
        )
        return detected
    if entry.meeting_type not in ("lecture", "laboratory"):
        add("INVALID_MEETING_TYPE", "ERROR", "Choose lecture or laboratory.")

    assignment = FacultySubjectAssignment.objects.select_related(
        "faculty__home_department__college",
        "subject_offering__academic_term",
        "subject_offering__subject",
        "subject_offering__department__college",
    ).get(pk=entry.assignment_id)
    faculty = assignment.faculty
    offering = assignment.subject_offering
    term = schedule.academic_term
    room = Room.objects.select_related(
        "category",
        "building",
        "owner_department__college",
        "owner_college",
    ).get(pk=entry.room_id)
    requirement = OfferingRequirement.objects.select_related(
        "section__department__college",
        "room_type",
    ).filter(subject_offering=offering).first()
    section = requirement.section if requirement else None

    if offering.academic_term_id != term.pk or (
        section and section.academic_term_id != term.pk
    ):
        add(
            "TERM_MISMATCH",
            "ERROR",
            "Schedule, teaching assignment and class section must use the same academic term.",
        )
    if (
        offering.department_id != schedule.department_id
        or faculty.home_department_id != offering.department_id
        or (section and section.department_id != offering.department_id)
    ):
        add(
            "ORGANIZATION_MISMATCH",
            "ERROR",
            "Schedule, faculty, offering and section must belong to the same department.",
        )
    if not section:
        add(
            "SECTION_REQUIRED",
            "ERROR",
            f"Configure a class section for {offering.subject.code} before scheduling.",
            "Open Offering requirements and choose its class section.",
        )

    resources = [
        faculty,
        faculty.home_department,
        faculty.home_department.college,
        offering,
        offering.subject,
        offering.department,
        offering.department.college,
        room,
        term,
        term.academic_year,
        term.semester,
        schedule.department,
        schedule.department.college,
    ]
    resources += [
        resource
        for resource in (
            section,
            room.category,
            room.building,
            room.owner_department,
            room.owner_college,
            room.owner_department.college if room.owner_department_id else None,
        )
        if resource
    ]
    if any(not resource.is_active for resource in resources):
        add(
            "INACTIVE_RESOURCE",
            "ERROR",
            "This meeting uses an inactive faculty member, room, offering, section, organization or calendar resource.",
            "Restore the resource or select an active replacement.",
        )
    if not terms_share_weekday(term, term, entry.day_of_week):
        add(
            "INVALID_TIME_RANGE",
            "ERROR",
            "The selected weekday does not occur within this academic term.",
        )

    for unavailable in FacultyAvailability.objects.filter(
        faculty=faculty,
        academic_term=term,
        day_of_week=entry.day_of_week,
        availability_type="unavailable",
    ):
        if overlaps(
            entry.start_time,
            entry.end_time,
            unavailable.start_time,
            unavailable.end_time,
        ):
            add(
                "FACULTY_UNAVAILABLE",
                "ERROR",
                f"{faculty} is unavailable on {dict(DAYS)[entry.day_of_week]} "
                f"{unavailable.start_time:%H:%M}–{unavailable.end_time:%H:%M}.",
            )

    for closure in RoomUnavailability.objects.filter(
        room=room,
        day_of_week=entry.day_of_week,
    ).select_related("academic_term"):
        if terms_share_weekday(
            term,
            closure.academic_term,
            entry.day_of_week,
        ) and overlaps(
            entry.start_time,
            entry.end_time,
            closure.start_time,
            closure.end_time,
        ):
            add(
                "ROOM_UNAVAILABLE",
                "ERROR",
                f"Room {room.code} is unavailable on {dict(DAYS)[entry.day_of_week]} "
                f"{closure.start_time:%H:%M}–{closure.end_time:%H:%M}.",
            )

    required_code = (
        requirement.room_type.code
        if requirement and requirement.room_type_id
        else offering.subject.required_room_type
    )
    mandatory = (
        requirement.room_type_mandatory
        if requirement and requirement.room_type_id
        else bool(offering.subject.required_room_type)
    )
    if required_code and (
        not room.category_id or room.category.code != required_code
    ):
        add(
            "ROOM_TYPE_MISMATCH",
            "ERROR" if mandatory else "WARNING",
            f"{offering.subject.code} "
            f"{'requires' if mandatory else 'prefers'} room type {required_code}; "
            f"{room.code} does not match.",
            "Choose a matching room or review the offering requirement.",
        )
    if (
        section
        and section.expected_size is not None
        and room.capacity is not None
        and section.expected_size > room.capacity
    ):
        add(
            "ROOM_CAPACITY_WARNING",
            "ERROR" if requirement.capacity_is_hard else "WARNING",
            f"Section {section.code} expects {section.expected_size} people; "
            f"room {room.code} holds {room.capacity}.",
            "Choose a larger room or review the expected class size.",
        )

    for other in peers:
        if other is entry or (
            entry.pk is not None
            and other.pk is not None
            and other.pk == entry.pk
        ):
            continue
        if (
            other.day_of_week != entry.day_of_week
            or not other.start_time
            or not other.end_time
            or other.start_time >= other.end_time
        ):
            continue
        if not terms_share_weekday(
            term,
            other.schedule.academic_term,
            entry.day_of_week,
        ) or not overlaps(
            entry.start_time,
            entry.end_time,
            other.start_time,
            other.end_time,
        ):
            continue

        other_requirement = OfferingRequirement.objects.filter(
            subject_offering_id=other.assignment.subject_offering_id
        ).first()
        matches = (
            (
                "FACULTY_OVERLAP",
                faculty.pk == other.assignment.faculty_id,
                f"Faculty {faculty}",
            ),
            ("ROOM_OVERLAP", room.pk == other.room_id, f"Room {room.code}"),
            (
                "SECTION_OVERLAP",
                section is not None
                and other_requirement is not None
                and section.pk == other_requirement.section_id,
                f"Section {section.code if section else ''}",
            ),
        )
        for code, matched, label in matches:
            if not matched:
                continue
            can_show_other = (
                other.pk is None
                or visible_entry_ids is None
                or other.pk in visible_entry_ids
            )
            detail = (
                f"{other.assignment.subject_offering.subject.code} "
                f"({other.schedule.name})"
                if can_show_other
                else "another protected meeting"
            )
            add(
                code,
                "ERROR",
                f"{label} is already assigned to {detail} on "
                f"{dict(DAYS)[other.day_of_week]} "
                f"{other.start_time:%H:%M}–{other.end_time:%H:%M}.",
                other=other,
            )
    return detected


def detect_entry_conflicts(entry, *, user=None, peers=None):
    if peers is None:
        excluded_entry_ids = (entry.pk,) if entry.pk is not None else ()
        peers = authoritative_occupancy(
            entry.schedule,
            excluded_entry_ids=excluded_entry_ids,
        )
    return [
        item.conflict
        for item in _entry_rule_conflicts(
            entry,
            schedule=entry.schedule,
            peers=tuple(peers),
            visible_entry_ids=_visible_entry_ids(user),
        )
    ]


def _entry_identity(entry, candidate_indexes):
    if entry.pk is not None:
        return "entry", entry.pk
    return "candidate", candidate_indexes[id(entry)]


def _meeting_requirement_conflicts(schedule, entries):
    assignments = list(
        FacultySubjectAssignment.objects.filter(
            subject_offering__academic_term=schedule.academic_term,
            subject_offering__department=schedule.department,
        ).select_related("subject_offering__subject")
    )
    requirements_by_assignment = {}
    for requirement in AssignmentMeetingRequirement.objects.filter(
        assignment__in=assignments
    ).order_by("assignment_id", "meeting_type", "pk"):
        requirements_by_assignment.setdefault(requirement.assignment_id, []).append(
            requirement
        )

    conflicts = []
    for assignment in assignments:
        requirements = requirements_by_assignment.get(assignment.pk, ())
        if requirements:
            for requirement in requirements:
                matching = [
                    entry
                    for entry in entries
                    if entry.assignment_id == assignment.pk
                    and entry.meeting_type == requirement.meeting_type
                ]
                if len(matching) != requirement.meetings_per_week:
                    conflicts.append(
                        Conflict(
                            "MEETING_REQUIREMENT_COUNT",
                            "ERROR",
                            f"{assignment.subject_offering.subject.code}: "
                            f"{len(matching)} of {requirement.meetings_per_week} required "
                            f"weekly {requirement.meeting_type} meetings recorded.",
                            "Add or remove meetings to match the configured requirement.",
                        )
                    )
                wrong_duration = [
                    entry
                    for entry in matching
                    if not entry.start_time
                    or not entry.end_time
                    or duration_minutes(entry.start_time, entry.end_time)
                    != Decimal(requirement.duration_minutes)
                ]
                if wrong_duration:
                    conflicts.append(
                        Conflict(
                            "MEETING_REQUIREMENT_DURATION",
                            "ERROR",
                            f"{assignment.subject_offering.subject.code}: "
                            f"{len(wrong_duration)} weekly {requirement.meeting_type} "
                            f"meeting(s) do not match the required "
                            f"{requirement.duration_minutes}-minute duration.",
                            "Adjust each meeting to the configured duration.",
                        )
                    )
            continue

        for kind in ("lecture", "laboratory"):
            required = (
                getattr(assignment.subject_offering, f"{kind}_hours")
                * assignment.share
            )
            actual = sum(
                (
                    duration_minutes(entry.start_time, entry.end_time) / 60
                    for entry in entries
                    if entry.assignment_id == assignment.pk
                    and entry.meeting_type == kind
                    and entry.start_time
                    and entry.end_time
                ),
                Decimal(0),
            )
            if actual != required:
                conflicts.append(
                    Conflict(
                        "MEETING_HOURS_WARNING",
                        "WARNING",
                        f"{assignment.subject_offering.subject.code}: "
                        f"{actual.normalize()} of {required.normalize()} weekly {kind} "
                        "hours recorded for this teaching assignment.",
                        "Review meeting durations and teaching share.",
                    )
                )
    return conflicts


def validate_candidate_schedule(
    schedule,
    *,
    retained_entries,
    proposed_entries,
    user=None,
) -> list[Conflict]:
    retained_entries = tuple(retained_entries)
    proposed_entries = tuple(proposed_entries)
    candidates = retained_entries + proposed_entries
    selected_entry_ids = tuple(
        ScheduleEntry.objects.filter(schedule=schedule)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    occupancy = authoritative_occupancy(
        schedule,
        excluded_entry_ids=selected_entry_ids,
    )
    peers = candidates + occupancy
    visible_entry_ids = _visible_entry_ids(user)
    candidate_indexes = {id(entry): index for index, entry in enumerate(candidates)}
    peer_identities = {
        id(entry): _entry_identity(entry, candidate_indexes)
        for entry in candidates
    }
    peer_identities.update(
        {id(entry): ("entry", entry.pk) for entry in occupancy}
    )

    result = []
    seen = set()
    for entry in candidates:
        identity = _entry_identity(entry, candidate_indexes)
        if (
            user is not None
            and entry.pk is not None
            and entry.pk not in visible_entry_ids
        ):
            key = ("PROTECTED_ENTRY", identity)
            if key not in seen:
                result.append(
                    Conflict(
                        "PROTECTED_ENTRY",
                        "ERROR",
                        "A meeting references data outside your current organizational scope.",
                        "Ask a system administrator to review its resource ownership.",
                    )
                )
                seen.add(key)
            continue

        for detected in _entry_rule_conflicts(
            entry,
            schedule=schedule,
            peers=peers,
            visible_entry_ids=visible_entry_ids,
        ):
            conflict = detected.conflict
            if detected.other is None:
                key = (conflict.code, identity, conflict.message)
            else:
                other_identity = peer_identities[id(detected.other)]
                key = (
                    conflict.code,
                    tuple(sorted((identity, other_identity))),
                )
            if key not in seen:
                result.append(conflict)
                seen.add(key)

    result.extend(_meeting_requirement_conflicts(schedule, candidates))
    return result


def get_schedule_conflicts(schedule, *, user=None):
    entries = list(entry_queryset().filter(schedule=schedule))
    return validate_candidate_schedule(
        schedule,
        retained_entries=entries,
        proposed_entries=(),
        user=user,
    )


def summarize(conflicts, total_entries):
    return {
        "total_entries": total_entries,
        "errors": sum(conflict.severity == "ERROR" for conflict in conflicts),
        "warnings": sum(conflict.severity == "WARNING" for conflict in conflicts),
        "conflicts": conflicts,
        "categories": sorted({conflict.code for conflict in conflicts}),
    }

"""Canonical deterministic validation. Unsaved candidates are valid inputs."""
from dataclasses import dataclass, asdict
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist

from workloads.models import FacultyAvailability, FacultySubjectAssignment
from scheduling.models import Room
from .models import OfferingRequirement, RoomUnavailability, ScheduleEntry
from .intervals import overlaps, terms_share_weekday, duration_minutes, DAYS
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


def detect_entry_conflicts(entry, *, user=None, peers=None):
    conflicts = []
    def add(code, severity, message, remedy="Choose another time or correct the resource configuration.", other=None):
        conflicts.append(Conflict(code, severity, message, remedy, entry.pk, other))
    if not entry.start_time or not entry.end_time or entry.start_time >= entry.end_time or entry.day_of_week not in dict(DAYS):
        add("INVALID_TIME_RANGE", "ERROR", "Choose a valid weekday and a start time earlier than the end time.")
        return conflicts
    if entry.meeting_type not in ("lecture", "laboratory"):
        add("INVALID_MEETING_TYPE", "ERROR", "Choose lecture or laboratory.")
    assignment = FacultySubjectAssignment.objects.select_related("faculty__home_department__college", "subject_offering__academic_term", "subject_offering__subject", "subject_offering__department__college").get(pk=entry.assignment_id)
    faculty, offering, term = assignment.faculty, assignment.subject_offering, entry.schedule.academic_term
    room = Room.objects.select_related("category", "building", "owner_department__college", "owner_college").get(pk=entry.room_id)
    requirement = OfferingRequirement.objects.select_related("section__department__college", "room_type").filter(subject_offering=offering).first()
    section = requirement.section if requirement else None
    if offering.academic_term_id != term.pk or (section and section.academic_term_id != term.pk):
        add("TERM_MISMATCH", "ERROR", "Schedule, teaching assignment and class section must use the same academic term.")
    if offering.department_id != entry.schedule.department_id or faculty.home_department_id != offering.department_id or (section and section.department_id != offering.department_id):
        add("ORGANIZATION_MISMATCH", "ERROR", "Schedule, faculty, offering and section must belong to the same department.")
    if not section:
        add("SECTION_REQUIRED", "ERROR", f"Configure a class section for {offering.subject.code} before scheduling.", "Open Offering requirements and choose its class section.")
    resources = [faculty, faculty.home_department, faculty.home_department.college, offering, offering.subject,
                 offering.department, offering.department.college, room, term, term.academic_year, term.semester,
                 entry.schedule.department, entry.schedule.department.college]
    resources += [obj for obj in (section, room.category, room.building, room.owner_department, room.owner_college, room.owner_department.college if room.owner_department_id else None) if obj]
    if any(not obj.is_active for obj in resources):
        add("INACTIVE_RESOURCE", "ERROR", "This meeting uses an inactive faculty member, room, offering, section, organization or calendar resource.", "Restore the resource or select an active replacement.")
    if not terms_share_weekday(term, term, entry.day_of_week):
        add("INVALID_TIME_RANGE", "ERROR", "The selected weekday does not occur within this academic term.")
    for unavailable in FacultyAvailability.objects.filter(faculty=faculty, academic_term=term, day_of_week=entry.day_of_week, availability_type="unavailable"):
        if overlaps(entry.start_time, entry.end_time, unavailable.start_time, unavailable.end_time):
            add("FACULTY_UNAVAILABLE", "ERROR", f"{faculty} is unavailable on {dict(DAYS)[entry.day_of_week]} {unavailable.start_time:%H:%M}–{unavailable.end_time:%H:%M}.")
    # Room closures are physical restrictions, also across overlapping term calendars.
    for closure in RoomUnavailability.objects.filter(room=room, day_of_week=entry.day_of_week).select_related("academic_term"):
        if terms_share_weekday(term, closure.academic_term, entry.day_of_week) and overlaps(entry.start_time, entry.end_time, closure.start_time, closure.end_time):
            add("ROOM_UNAVAILABLE", "ERROR", f"Room {room.code} is unavailable on {dict(DAYS)[entry.day_of_week]} {closure.start_time:%H:%M}–{closure.end_time:%H:%M}.")
    required_code = requirement.room_type.code if requirement and requirement.room_type_id else offering.subject.required_room_type
    mandatory = requirement.room_type_mandatory if requirement and requirement.room_type_id else bool(offering.subject.required_room_type)
    if required_code and (not room.category_id or room.category.code != required_code):
        add("ROOM_TYPE_MISMATCH", "ERROR" if mandatory else "WARNING", f"{offering.subject.code} {'requires' if mandatory else 'prefers'} room type {required_code}; {room.code} does not match.", "Choose a matching room or review the offering requirement.")
    if section and section.expected_size is not None and room.capacity is not None and section.expected_size > room.capacity:
        add("ROOM_CAPACITY_WARNING", "ERROR" if requirement.capacity_is_hard else "WARNING", f"Section {section.code} expects {section.expected_size} people; room {room.code} holds {room.capacity}.", "Choose a larger room or review the expected class size.")
    if peers is None:
        peers = entry_queryset().filter(day_of_week=entry.day_of_week, start_time__lt=entry.end_time, end_time__gt=entry.start_time,
            schedule__academic_term__start_date__lte=term.end_date, schedule__academic_term__end_date__gte=term.start_date)
    visible = None
    if user is not None:
        visible = set(scoped(user, ScheduleEntry.objects.all()).values_list("pk", flat=True)) if user.has_perm("timetabling.view_scheduleentry") else set()
    for other in peers:
        if (entry.pk and other.pk == entry.pk) or other.day_of_week != entry.day_of_week:
            continue
        if not terms_share_weekday(term, other.schedule.academic_term, entry.day_of_week) or not overlaps(entry.start_time, entry.end_time, other.start_time, other.end_time):
            continue
        other_req = OfferingRequirement.objects.filter(subject_offering_id=other.assignment.subject_offering_id).first()
        matches = [("FACULTY_OVERLAP", faculty.pk == other.assignment.faculty_id, f"Faculty {faculty}"),
                   ("ROOM_OVERLAP", room.pk == other.room_id, f"Room {room.code}"),
                   ("SECTION_OVERLAP", section is not None and other_req is not None and section.pk == other_req.section_id, f"Section {section.code if section else ''}")]
        for code, matched, label in matches:
            if matched:
                can_show = visible is None or other.pk in visible
                detail = f"{other.assignment.subject_offering.subject.code} ({other.schedule.name})" if can_show else "another protected meeting"
                add(code, "ERROR", f"{label} is already assigned to {detail} on {dict(DAYS)[other.day_of_week]} {other.start_time:%H:%M}–{other.end_time:%H:%M}.", other=other.pk if can_show else None)
    return conflicts


def get_schedule_conflicts(schedule, *, user=None):
    entries = list(entry_queryset().filter(schedule=schedule))
    result, seen = [], set()
    visible = set(scoped(user, ScheduleEntry.objects.filter(schedule=schedule)).values_list("pk", flat=True)) if user is not None else None
    for entry in entries:
        if visible is not None and entry.pk not in visible:
            result.append(Conflict("PROTECTED_ENTRY", "ERROR", "A meeting references data outside your current organizational scope.", "Ask a system administrator to review its resource ownership."))
            continue
        for conflict in detect_entry_conflicts(entry, user=user):
            pair = tuple(sorted((conflict.entry_id, conflict.other_entry_id))) if conflict.other_entry_id else (conflict.entry_id,)
            key = (conflict.code, pair, conflict.message if not conflict.other_entry_id else "")
            if key not in seen:
                result.append(conflict)
                seen.add(key)
    # Required hours are teaching-share based; no extra workload credit is created.
    assignments = FacultySubjectAssignment.objects.filter(subject_offering__academic_term=schedule.academic_term, subject_offering__department=schedule.department).select_related("subject_offering__subject")
    for assignment in assignments:
        for kind in ("lecture", "laboratory"):
            required = getattr(assignment.subject_offering, f"{kind}_hours") * assignment.share
            actual = sum((duration_minutes(e.start_time, e.end_time) / 60 for e in entries if e.assignment_id == assignment.pk and e.meeting_type == kind), Decimal(0))
            if actual != required:
                result.append(Conflict("MEETING_HOURS_WARNING", "WARNING", f"{assignment.subject_offering.subject.code}: {actual.normalize()} of {required.normalize()} weekly {kind} hours recorded for this teaching assignment.", "Review meeting durations and teaching share."))
    return result


def summarize(conflicts, total_entries):
    return {"total_entries": total_entries, "errors": sum(c.severity == "ERROR" for c in conflicts),
            "warnings": sum(c.severity == "WARNING" for c in conflicts), "conflicts": conflicts,
            "categories": sorted({c.code for c in conflicts})}

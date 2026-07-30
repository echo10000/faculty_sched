"""Constraint-plan documentation for the timetable suggestion engine.

CP-SAT is a constraint-satisfaction solver: it searches combinations of
decisions that obey every hard rule we give it.  It is not inherently chasing
one "best" timetable; it only optimizes when we provide an objective.  Here,
load balance will be a soft objective after feasibility has been established.

The eventual decision variables will represent whether a particular unassigned
subject for a block is taught by a particular qualified faculty member in a
particular eligible room at a particular time slot.  A selected combination
therefore supplies the faculty, room, and time for one subject/block demand.

Hard constraints will prohibit faculty, room, and block double-booking; require
that faculty are qualified for the subject; require a subject's room type when
one is specified; and require room capacity to meet the block enrolment.  The
solver will also respect the existing assignments for the term.

This is deliberately a whole-term suggestion tool, not a live incremental
scheduler.  It will propose a complete feasible timetable that an administrator
reviews and may adjust before creating real Assignments.  The existing Django
validation and database exclusion constraints remain the final safeguards when
the approved suggestions are saved.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import Count

from academics.models import CurriculumSubject
from faculty.models import FacultyQualification
from ortools.sat.python import cp_model

from .models import Assignment, Block, Room, TimeSlot

if TYPE_CHECKING:
    from .models import Term


@dataclass(frozen=True)
class TimeSlotInput:
    id: int
    day_of_week: str
    start_time: str
    end_time: str


@dataclass(frozen=True)
class SchedulingDemand:
    block_id: int
    subject_id: int
    subject_code: str
    subject_title: str
    units: str
    faculty_ids: tuple[int, ...]
    room_ids: tuple[int, ...]
    time_slot_ids: tuple[int, ...]


@dataclass(frozen=True)
class SchedulingInputs:
    term_id: int
    time_slots: tuple[TimeSlotInput, ...]
    demands: tuple[SchedulingDemand, ...]


def prepare_scheduling_inputs(term: "Term") -> SchedulingInputs:
    """Resolve the term's unscheduled teaching demand into solver-ready values.

    The result deliberately contains only normal Python values and IDs.  This
    keeps the CP-SAT model independent from Django querysets and model objects.
    """
    blocks = list(
        Block.objects.filter(term=term)
        .select_related("curriculum__program__department")
        .annotate(enrolled_count_value=Count("students"))
    )
    curriculum_ids = {block.curriculum_id for block in blocks}
    placements = CurriculumSubject.objects.filter(
        curriculum_id__in=curriculum_ids,
        term=term.term_name,
    ).select_related("subject")
    placements_by_curriculum_and_level = {}
    subject_ids = set()
    for placement in placements:
        placements_by_curriculum_and_level.setdefault(
            (placement.curriculum_id, placement.year_level), []
        ).append(placement.subject)
        subject_ids.add(placement.subject_id)

    existing_assignments = set(
        Assignment.objects.filter(term=term).values_list("block_id", "subject_id")
    )
    qualifications_by_subject = {}
    for qualification in FacultyQualification.objects.filter(
        subject_id__in=subject_ids,
        faculty__is_active=True,
    ).values_list("subject_id", "faculty_id", "faculty__home_department_id"):
        subject_id, faculty_id, department_id = qualification
        qualifications_by_subject.setdefault(subject_id, []).append((faculty_id, department_id))

    rooms = list(Room.objects.values("id", "room_type", "capacity", "restricted_to_department_id"))
    time_slots = tuple(
        TimeSlotInput(
            id=slot.id,
            day_of_week=slot.day_of_week,
            start_time=slot.start_time.isoformat(),
            end_time=slot.end_time.isoformat(),
        )
        for slot in TimeSlot.objects.all().order_by("day_of_week", "start_time", "end_time")
    )
    time_slot_ids = tuple(slot.id for slot in time_slots)

    demands = []
    for block in blocks:
        department_id = block.curriculum.program.department_id
        for subject in placements_by_curriculum_and_level.get(
            (block.curriculum_id, block.year_level), []
        ):
            if (block.id, subject.id) in existing_assignments:
                continue
            faculty_ids = tuple(
                faculty_id
                for faculty_id, faculty_department_id in qualifications_by_subject.get(subject.id, [])
                if subject.is_general_education or faculty_department_id == department_id
            )
            room_ids = tuple(
                room["id"]
                for room in rooms
                if room["capacity"] >= block.enrolled_count_value
                and (not subject.required_room_type or room["room_type"] == subject.required_room_type)
                and (
                    room["restricted_to_department_id"] is None
                    or room["restricted_to_department_id"] == department_id
                )
            )
            demands.append(
                SchedulingDemand(
                    block_id=block.id,
                    subject_id=subject.id,
                    subject_code=subject.code,
                    subject_title=subject.title,
                    units=str(subject.units),
                    faculty_ids=faculty_ids,
                    room_ids=room_ids,
                    time_slot_ids=time_slot_ids,
                )
            )

    return SchedulingInputs(term_id=term.id, time_slots=time_slots, demands=tuple(demands))

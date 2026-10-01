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
from datetime import time
from decimal import Decimal
from itertools import combinations
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


@dataclass(frozen=True)
class _Candidate:
    demand_index: int
    faculty_id: int
    subject_id: int
    block_id: int
    room_id: int
    time_slot_id: int
    units_tenths: int


def _slots_overlap(left: TimeSlotInput, right: TimeSlotInput) -> bool:
    return (
        left.day_of_week == right.day_of_week
        and left.start_time < right.end_time
        and left.end_time > right.start_time
    )


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
                    # Preserve legacy decimal validation when master totals have trailing zeros.
                    units=format(subject.units.normalize(), "f"),
                    faculty_ids=faculty_ids,
                    room_ids=room_ids,
                    time_slot_ids=time_slot_ids,
                )
            )

    return SchedulingInputs(term_id=term.id, time_slots=time_slots, demands=tuple(demands))


def generate_schedule_suggestions(term: "Term", block_ids=None) -> dict:
    """Generate unsaved, conflict-free proposals for a term.

    ``block_ids`` is an optional testing/preview limiter.  It does not change
    the public whole-term behaviour when omitted.
    """
    inputs = prepare_scheduling_inputs(term)
    selected_block_ids = set(block_ids) if block_ids is not None else None
    demands = [
        demand
        for demand in inputs.demands
        if selected_block_ids is None or demand.block_id in selected_block_ids
    ]
    slots_by_id = {slot.id: slot for slot in inputs.time_slots}
    model = cp_model.CpModel()

    existing = list(
        Assignment.objects.filter(term=term).values(
            "faculty_id", "room_id", "block_id", "day_of_week", "start_time", "end_time"
        )
    )
    candidates = []
    unavailable_reasons = {}
    for demand_index, demand in enumerate(demands):
        if not demand.faculty_ids:
            unavailable_reasons[demand_index] = "No qualified active faculty are available."
            continue
        if not demand.room_ids:
            unavailable_reasons[demand_index] = "No room meets the capacity and room-type requirements."
            continue
        if not demand.time_slot_ids:
            unavailable_reasons[demand_index] = "No time slots are defined."
            continue

        for faculty_id in demand.faculty_ids:
            for room_id in demand.room_ids:
                for time_slot_id in demand.time_slot_ids:
                    slot = slots_by_id[time_slot_id]
                    conflicts_existing = any(
                        slot.day_of_week == assignment["day_of_week"]
                        and slot.start_time < assignment["end_time"].isoformat()
                        and slot.end_time > assignment["start_time"].isoformat()
                        and (
                            faculty_id == assignment["faculty_id"]
                            or room_id == assignment["room_id"]
                            or demand.block_id == assignment["block_id"]
                        )
                        for assignment in existing
                    )
                    if not conflicts_existing:
                        candidates.append(
                            _Candidate(
                                demand_index=demand_index,
                                faculty_id=faculty_id,
                                subject_id=demand.subject_id,
                                block_id=demand.block_id,
                                room_id=room_id,
                                time_slot_id=time_slot_id,
                                units_tenths=int(Decimal(demand.units) * 10),
                            )
                        )

    variables = [model.NewBoolVar(f"assignment_{index}") for index in range(len(candidates))]
    candidate_indices_by_demand = {}
    for index, candidate in enumerate(candidates):
        candidate_indices_by_demand.setdefault(candidate.demand_index, []).append(index)
    for demand_index, indices in candidate_indices_by_demand.items():
        # At-most-one retains feasibility when options conflict globally.  The
        # objective below strongly prefers filling each demand where possible.
        model.AddAtMostOne(variables[index] for index in indices)

    def add_resource_overlap_constraints(resource_attribute):
        by_resource = {}
        for index, candidate in enumerate(candidates):
            by_resource.setdefault(getattr(candidate, resource_attribute), []).append(index)
        for indices in by_resource.values():
            for left_index, right_index in combinations(indices, 2):
                if _slots_overlap(
                    slots_by_id[candidates[left_index].time_slot_id],
                    slots_by_id[candidates[right_index].time_slot_id],
                ):
                    model.Add(variables[left_index] + variables[right_index] <= 1)

    add_resource_overlap_constraints("faculty_id")
    add_resource_overlap_constraints("room_id")
    add_resource_overlap_constraints("block_id")

    faculty_ids = sorted({candidate.faculty_id for candidate in candidates})
    existing_units = {
        faculty_id: 0
        for faculty_id in faculty_ids
    }
    for assignment in Assignment.objects.filter(term=term, faculty_id__in=faculty_ids).values(
        "faculty_id", "units_credited"
    ):
        existing_units[assignment["faculty_id"]] += int(Decimal(assignment["units_credited"]) * 10)

    from faculty.models import Faculty

    faculty_targets = {
        faculty.id: int(faculty.effective_load_units * 10)
        for faculty in Faculty.objects.filter(id__in=faculty_ids).select_related("designation")
    }
    total_units_off_target = []
    max_new_units = sum(candidate.units_tenths for candidate in candidates)
    for faculty_id in faculty_ids:
        selected_units = sum(
            candidate.units_tenths * variables[index]
            for index, candidate in enumerate(candidates)
            if candidate.faculty_id == faculty_id
        )
        total_units = model.NewIntVar(0, existing_units[faculty_id] + max_new_units, f"load_{faculty_id}")
        model.Add(total_units == existing_units[faculty_id] + selected_units)
        deviation = model.NewIntVar(0, existing_units[faculty_id] + max_new_units + faculty_targets[faculty_id], f"deviation_{faculty_id}")
        model.AddAbsEquality(deviation, total_units - faculty_targets[faculty_id])
        total_units_off_target.append(deviation)

    coverage_weight = sum(
        existing_units[faculty_id] + max_new_units + faculty_targets[faculty_id]
        for faculty_id in faculty_ids
    ) + 1
    model.Minimize(
        sum(total_units_off_target) - coverage_weight * sum(variables)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    feasible_statuses = (cp_model.OPTIMAL, cp_model.FEASIBLE)
    if status not in feasible_statuses:
        return {
            "proposals": [],
            "unfilled": [
                {
                    "block_id": demand.block_id,
                    "subject_id": demand.subject_id,
                    "reason": "No feasible schedule could be found within the solver time limit.",
                }
                for demand in demands
            ],
        }

    proposals = [
        {
            "faculty_id": candidate.faculty_id,
            "subject_id": candidate.subject_id,
            "block_id": candidate.block_id,
            "room_id": candidate.room_id,
            "time_slot_id": candidate.time_slot_id,
        }
        for index, candidate in enumerate(candidates)
        if solver.Value(variables[index])
    ]
    filled_demand_indices = {
        candidate.demand_index
        for index, candidate in enumerate(candidates)
        if solver.Value(variables[index])
    }
    unfilled = [
        {
            "block_id": demand.block_id,
            "subject_id": demand.subject_id,
            "reason": unavailable_reasons.get(
                demand_index,
                "No non-conflicting combination could be selected.",
            ),
        }
        for demand_index, demand in enumerate(demands)
        if demand_index not in filled_demand_indices
    ]
    return {"proposals": proposals, "unfilled": unfilled}

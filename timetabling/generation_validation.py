"""Strict, scoped validation of a stored timetable generation proposal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from resources.selectors import available_resources
from scheduling.models import Room
from workloads.models import FacultySubjectAssignment
from workloads.selectors import scoped_records

from .conflicts import Conflict
from .models import AssignmentMeetingRequirement, ScheduleEntry, ScheduleGenerationRun
from .queries import get_schedule, scoped


PROPOSAL_KEYS = frozenset({
    "assignment_id", "meeting_requirement_id", "occurrence_index", "room_id",
    "day_of_week", "start_time", "end_time", "meeting_type",
})
PROPOSAL_CONFLICT_CODES = frozenset({
    "PROPOSAL_SCHEMA", "PROPOSAL_SCOPE", "PROPOSAL_DEMAND", "PROPOSAL_GRID",
})


@dataclass(frozen=True, slots=True)
class _ResolvedProposal:
    entries: tuple[ScheduleEntry, ...]
    retained_entries: tuple[ScheduleEntry, ...]
    conflicts: tuple[Conflict, ...]


def _conflict(code: str) -> Conflict:
    messages = {
        "PROPOSAL_SCHEMA": "The stored proposal has an invalid record format.",
        "PROPOSAL_SCOPE": "The stored proposal references data outside its authorized scope.",
        "PROPOSAL_DEMAND": "The stored proposal does not match the current meeting requirements.",
        "PROPOSAL_GRID": "The stored proposal has an invalid scheduling position.",
    }
    return Conflict(code, "ERROR", messages[code], "Generate a new proposal.")


def _strict_time(value: object) -> time | None:
    if type(value) is not str or len(value) != 5 or value[2] != ":":
        return None
    if not (value[:2].isdigit() and value[3:].isdigit()):
        return None
    try:
        parsed = time(int(value[:2]), int(value[3:]))
    except ValueError:
        return None
    return parsed if parsed.strftime("%H:%M") == value else None


def _minute(value: time) -> int:
    return value.hour * 60 + value.minute


def serialize_proposals(proposals, configuration_snapshot) -> list[dict[str, object]]:
    """Convert solver slot indexes into canonical, eight-key JSON rows."""
    if not isinstance(configuration_snapshot, dict):
        raise ValueError("Invalid captured configuration.")
    start = _strict_time(configuration_snapshot.get("earliest_start"))
    end = _strict_time(configuration_snapshot.get("latest_end"))
    increment = configuration_snapshot.get("slot_increment_minutes")
    if start is None or end is None or type(increment) is not int or increment <= 0:
        raise ValueError("Invalid captured scheduling grid.")
    first, last = _minute(start), _minute(end)
    if last <= first or (last - first) % increment:
        raise ValueError("Invalid captured scheduling window.")
    count = (last - first) // increment
    rows = []
    for proposal in proposals:
        a, b = proposal.start_slot, proposal.end_slot
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= count:
            raise ValueError("Solver returned an invalid slot.")
        row = {
            "assignment_id": proposal.assignment_id,
            "meeting_requirement_id": proposal.meeting_requirement_id,
            "occurrence_index": proposal.occurrence_index,
            "room_id": proposal.room_id,
            "day_of_week": proposal.day_of_week,
            "start_time": f"{(first + a * increment) // 60:02d}:{(first + a * increment) % 60:02d}",
            "end_time": f"{(first + b * increment) // 60:02d}:{(first + b * increment) % 60:02d}",
            "meeting_type": proposal.meeting_type,
        }
        rows.append(row)
    rows.sort(key=lambda row: (
        row["assignment_id"], row["meeting_type"], row["meeting_requirement_id"],
        row["occurrence_index"], row["day_of_week"], row["start_time"], row["room_id"],
    ))
    return rows


def _valid_row(row: object) -> bool:
    if type(row) is not dict or row.keys() != PROPOSAL_KEYS:
        return False
    for key in ("assignment_id", "meeting_requirement_id", "room_id", "day_of_week"):
        if type(row[key]) is not int or row[key] <= 0:
            return False
    if type(row["occurrence_index"]) is not int or row["occurrence_index"] < 0:
        return False
    return (
        _strict_time(row["start_time"]) is not None
        and _strict_time(row["end_time"]) is not None
        and type(row["meeting_type"]) is str
        and row["meeting_type"] in ("lecture", "laboratory")
    )


def _valid_id_list(value: object) -> bool:
    return type(value) is list and all(type(item) is int and item > 0 for item in value)


def _valid_manifest(value: object) -> bool:
    return type(value) is list and all(
        type(pair) is list and len(pair) == 2
        and type(pair[0]) is int and pair[0] > 0
        and type(pair[1]) is int and pair[1] >= 0
        for pair in value
    )


def _resolve_generation_contract(*, run, prepared, proposal_rows, user) -> _ResolvedProposal:
    conflicts: list[Conflict] = []
    if type(proposal_rows) is not list:
        return _ResolvedProposal((), (), (_conflict("PROPOSAL_SCHEMA"),))
    valid_rows = []
    for row in proposal_rows:
        if _valid_row(row):
            valid_rows.append(row)
        else:
            conflicts.append(_conflict("PROPOSAL_SCHEMA"))
    schedule = get_schedule(user, prepared.schedule_id, action="change")
    if (run.schedule_id != prepared.schedule_id or run.academic_term_id != schedule.academic_term_id
            or run.department_id != schedule.department_id):
        conflicts.append(_conflict("PROPOSAL_SCOPE"))
    snap = run.configuration_snapshot
    fresh = prepared.configuration_snapshot
    if (type(snap) is not dict or snap != fresh
            or snap.get("configuration_id") != prepared.configuration_id
            or snap.get("academic_term_id") != schedule.academic_term_id
            or snap.get("department_id") != schedule.department_id):
        conflicts.append(_conflict("PROPOSAL_DEMAND"))
    summary = run.input_summary
    fresh_summary = prepared.input_summary
    if type(summary) is not dict or any(
        not _valid_id_list(summary.get(key)) or summary.get(key) != fresh_summary.get(key)
        for key in ("retained_entry_ids", "replace_entry_ids", "eligible_room_ids")
    ) or not _valid_manifest(summary.get("remaining_demands")) or summary.get("remaining_demands") != fresh_summary.get("remaining_demands"):
        conflicts.append(_conflict("PROPOSAL_DEMAND"))
    if run.strategy == ScheduleGenerationRun.Strategy.FILL_GAPS and prepared.replace_entry_ids:
        conflicts.append(_conflict("PROPOSAL_DEMAND"))
    manifest = tuple(tuple(item) for item in fresh_summary.get("remaining_demands", []))
    if run.status == ScheduleGenerationRun.Status.PROPOSAL_READY and run.proposed_meeting_count != len(proposal_rows):
        conflicts.append(_conflict("PROPOSAL_DEMAND"))
    keys = tuple((row["meeting_requirement_id"], row["occurrence_index"]) for row in valid_rows)
    if len(keys) != len(set(keys)) or set(keys) != set(manifest) or len(keys) != len(manifest):
        conflicts.append(_conflict("PROPOSAL_DEMAND"))

    assignments = {item.pk: item for item in scoped_records(
        user, FacultySubjectAssignment.objects.filter(
            pk__in={row["assignment_id"] for row in valid_rows},
            subject_offering__academic_term_id=schedule.academic_term_id,
            subject_offering__department_id=schedule.department_id,
            faculty__home_department_id=schedule.department_id,
        ),
    ).select_related("subject_offering", "faculty")}
    requirements = {item.pk: item for item in scoped(
        user, AssignmentMeetingRequirement.objects.filter(
            pk__in={row["meeting_requirement_id"] for row in valid_rows},
            assignment_id__in=assignments,
        ),
    )}
    eligible_ids = fresh_summary.get("eligible_room_ids", [])
    rooms = {item.pk: item for item in available_resources(user, Room).filter(
        pk__in=set(eligible_ids) & {row["room_id"] for row in valid_rows}
    )}
    retained = tuple(scoped(user, ScheduleEntry.objects.filter(
        pk__in=prepared.retained_entry_ids, schedule=schedule,
    )).order_by("pk"))
    if tuple(item.pk for item in retained) != tuple(prepared.retained_entry_ids):
        conflicts.append(_conflict("PROPOSAL_DEMAND"))
    entries = []
    start = _strict_time(fresh.get("earliest_start")) if type(fresh) is dict else None
    end = _strict_time(fresh.get("latest_end")) if type(fresh) is dict else None
    increment = fresh.get("slot_increment_minutes") if type(fresh) is dict else None
    days = fresh.get("allowed_weekdays") if type(fresh) is dict else None
    for row in valid_rows:
        assignment = assignments.get(row["assignment_id"])
        requirement = requirements.get(row["meeting_requirement_id"])
        room = rooms.get(row["room_id"])
        if assignment is None or requirement is None or room is None:
            conflicts.append(_conflict("PROPOSAL_SCOPE"))
            continue
        if (requirement.assignment_id != assignment.pk
                or requirement.meeting_type != row["meeting_type"]
                or (requirement.pk, row["occurrence_index"]) not in manifest):
            conflicts.append(_conflict("PROPOSAL_DEMAND"))
            continue
        row_start, row_end = _strict_time(row["start_time"]), _strict_time(row["end_time"])
        if (start is None or end is None or type(increment) is not int or increment <= 0
                or type(days) is not list or row["day_of_week"] not in days
                or not start <= row_start < row_end <= end
                or (_minute(row_start) - _minute(start)) % increment
                or (_minute(row_end) - _minute(start)) % increment):
            conflicts.append(_conflict("PROPOSAL_GRID"))
            continue
        if _minute(row_end) - _minute(row_start) != requirement.duration_minutes:
            conflicts.append(_conflict("PROPOSAL_DEMAND"))
            continue
        entries.append(ScheduleEntry(
            schedule=schedule, assignment=assignment, room=room,
            day_of_week=row["day_of_week"], start_time=row_start,
            end_time=row_end, meeting_type=row["meeting_type"],
        ))
    return _ResolvedProposal(tuple(entries), retained, tuple(conflicts))


def validate_generation_contract(*, run, prepared, proposal_rows, user) -> list[Conflict]:
    return list(_resolve_generation_contract(
        run=run, prepared=prepared, proposal_rows=proposal_rows, user=user,
    ).conflicts)

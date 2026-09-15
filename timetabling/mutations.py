import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.shortcuts import get_object_or_404

from audit.services import record_event
from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from core.models import Department, College
from faculty.models import Faculty
from resources.models import RoomType, Building
from scheduling.models import Room
from workloads.models import FacultyAvailability, SubjectOffering, FacultySubjectAssignment
from workloads.models import validate_active_term
from .models import Schedule, ScheduleEntry, ClassSection, OfferingRequirement, RoomUnavailability
from .forms import EntryForm
from .queries import authorized, scoped, get_schedule
from .conflicts import detect_entry_conflicts, get_schedule_conflicts, summarize


def mutation_lock():
    # Shared across terms: overlapping calendars can compete for physical resources.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [74190304])


def snapshot(obj):
    return {field.attname: str(getattr(obj, field.attname)) for field in obj._meta.fields
            if field.name not in ("notes", "created_at", "updated_at", "validated_signature")}


def event(action, user, obj, before=None, **details):
    record_event(action, actor=user, obj=obj, details={"academic_term_id": obj.academic_term.pk,
        "before": before or {}, "after": snapshot(obj), **details})


def mark_draft(schedule, user):
    if schedule.status != Schedule.Status.DRAFT or schedule.validated_signature:
        before = snapshot(schedule)
        schedule.status, schedule.validated_signature = Schedule.Status.DRAFT, ""
        schedule.save()
        event("schedule.status_changed", user, schedule, before)


def dependency_signature(schedule):
    """Conservative invalidation: shared resource edits invalidate prior checks.

    Only a digest is stored. No credentials, contacts or notes enter this payload.
    Narrowing dependencies is an optimization for a later version.
    """
    specs = [
        (Schedule, ["id", "name", "department_id", "academic_term_id"]),
        (ScheduleEntry, ["id", "schedule_id", "assignment_id", "room_id", "day_of_week", "start_time", "end_time", "meeting_type"]),
        (ClassSection, ["id", "department_id", "academic_term_id", "code", "is_active", "expected_size"]),
        (OfferingRequirement, ["id", "subject_offering_id", "section_id", "room_type_id", "room_type_mandatory", "capacity_is_hard"]),
        (RoomUnavailability, ["id", "room_id", "academic_term_id", "day_of_week", "start_time", "end_time"]),
        (FacultyAvailability, ["id", "faculty_id", "academic_term_id", "day_of_week", "start_time", "end_time", "availability_type"]),
        (FacultySubjectAssignment, ["id", "faculty_id", "subject_offering_id", "share"]),
        (SubjectOffering, ["id", "subject_id", "department_id", "academic_term_id", "is_active", "lecture_hours", "laboratory_hours"]),
        (Subject, ["id", "is_active", "required_room_type", "owning_department_id"]),
        (Faculty, ["id", "is_active", "home_department_id"]),
        (Room, ["id", "is_active", "capacity", "category_id", "building_id", "owner_department_id", "owner_college_id"]),
        (RoomType, ["id", "code", "is_active"]), (Building, ["id", "is_active"]),
        (AcademicTerm, ["id", "start_date", "end_date", "is_active", "academic_year_id", "semester_id"]),
        (AcademicYear, ["id", "is_active", "start_date", "end_date"]), (Semester, ["id", "is_active"]),
        (Department, ["id", "is_active", "college_id"]), (College, ["id", "is_active"]),
    ]
    payload = {model._meta.label: list(model.objects.order_by("pk").values_list(*fields)) for model, fields in specs}
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def effective_status(schedule):
    return "validated" if schedule.status == "validated" and schedule.validated_signature == dependency_signature(schedule) else "draft"


@transaction.atomic
def save_record(*, user, form_class, data, pk=None, term=None):
    model = form_class._meta.model
    authorized(user, model, "change" if pk else "add")
    mutation_lock()
    original = get_object_or_404(scoped(user, model.objects.select_for_update()), pk=pk) if pk else None
    before = snapshot(original) if original else {}
    form = form_class(data, user=user, instance=original, term=term)
    if not form.is_valid():
        return None, form
    obj = form.save(commit=False)
    validate_active_term(obj.academic_term)
    if not pk:
        obj.created_by = user
    if model is Schedule:
        obj.status, obj.validated_signature = "draft", ""
    obj.save()
    event(f"{model._meta.model_name}.{'updated' if pk else 'created'}", user, obj, before, notes_changed="notes" in form.changed_data)
    if model is Schedule and before.get("status") == "validated":
        event("schedule.status_changed", user, obj, before)
    return obj, form


@transaction.atomic
def save_entry(*, user, schedule_id, data, pk=None, preview=False):
    authorized(user, ScheduleEntry, "change" if pk else "add")
    mutation_lock()
    schedule = get_schedule(user, schedule_id)
    validate_active_term(schedule.academic_term)
    original = get_object_or_404(scoped(user, ScheduleEntry.objects.select_for_update()).filter(schedule=schedule), pk=pk) if pk else None
    before = snapshot(original) if original else {}
    form = EntryForm(data, user=user, schedule=schedule, instance=original)
    if not form.is_valid():
        return None, form, []
    entry = form.save(commit=False)
    conflicts = detect_entry_conflicts(entry, user=user)
    if any(c.severity == "ERROR" for c in conflicts):
        return None, form, conflicts
    if preview:
        return entry, form, conflicts
    if not pk:
        entry.created_by = user
    entry.save()
    mark_draft(schedule, user)
    event(f"scheduleentry.{'updated' if pk else 'created'}", user, entry, before, notes_changed="notes" in form.changed_data)
    return entry, form, conflicts


@transaction.atomic
def remove_entry(*, user, schedule_id, pk):
    authorized(user, ScheduleEntry, "delete")
    mutation_lock()
    schedule = get_schedule(user, schedule_id)
    entry = get_object_or_404(scoped(user, ScheduleEntry.objects.select_for_update()).filter(schedule=schedule), pk=pk)
    event("scheduleentry.removed", user, entry, snapshot(entry))
    entry.delete()
    mark_draft(schedule, user)


@transaction.atomic
def remove_closure(*, user, pk):
    authorized(user, RoomUnavailability, "delete")
    mutation_lock()
    obj = get_object_or_404(scoped(user, RoomUnavailability.objects.select_for_update()), pk=pk)
    event("roomunavailability.removed", user, obj, snapshot(obj))
    obj.delete()


@transaction.atomic
def validate_schedule(*, user, schedule_id):
    mutation_lock()
    schedule = get_schedule(user, schedule_id, "validate")
    authorized(user, ScheduleEntry)
    before = snapshot(schedule)
    starting_signature = dependency_signature(schedule)
    conflicts = get_schedule_conflicts(schedule, user=user)
    if starting_signature != dependency_signature(schedule):
        from .conflicts import Conflict
        conflicts.append(Conflict("CONCURRENT_CHANGE", "ERROR", "Scheduling data changed while validation was running.", "Run validation again against the latest records."))
    if not schedule.entries.exists():
        from .conflicts import Conflict
        conflicts.append(Conflict("EMPTY_SCHEDULE", "ERROR", "Add at least one meeting before validating this schedule.", "Create a schedule entry."))
    report = summarize(conflicts, schedule.entries.count())
    schedule.status = "draft" if report["errors"] else "validated"
    schedule.validated_signature = starting_signature if not report["errors"] else ""
    schedule.save()
    event("schedule.validated", user, schedule, before, errors=report["errors"], warnings=report["warnings"])
    if before["status"] != schedule.status:
        event("schedule.status_changed", user, schedule, before)
    return report

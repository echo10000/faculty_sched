from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from uuid import uuid4

from audit.services import record_event
from workloads.models import validate_active_term
from .models import Schedule, ScheduleEntry, RoomUnavailability
from .forms import EntryForm
from .queries import authorized, scoped, get_schedule
from .conflicts import detect_entry_conflicts, get_schedule_conflicts, summarize
from .locking import scheduling_lock
from .signatures import dependency_signature


mutation_lock = scheduling_lock


def snapshot(obj):
    return {field.attname: str(getattr(obj, field.attname)) for field in obj._meta.fields
            if field.name not in ("notes", "created_at", "updated_at", "validated_signature")}


def event(action, user, obj, before=None, **details):
    record_event(action, actor=user, obj=obj, details={"academic_term_id": obj.academic_term.pk,
        "before": before or {}, "after": snapshot(obj), **details})


def mark_draft(schedule, user):
    require_editable(schedule)
    before = snapshot(schedule)
    if schedule.status == Schedule.Status.VALIDATED:
        schedule.status = Schedule.Status.DRAFT
    schedule.validated_signature = ""
    schedule.revision_token = uuid4().hex
    schedule.save(update_fields=["status", "validated_signature", "revision_token", "updated_at"])
    if before["status"] != schedule.status:
        event("schedule.status_changed", user, schedule, before)


def require_editable(schedule):
    if schedule.status not in (Schedule.Status.DRAFT, Schedule.Status.VALIDATED, Schedule.Status.NEEDS_REVISION):
        raise ValidationError("This schedule version is read-only. Revise an approved version to make changes.")


def effective_status(schedule):
    return "validated" if schedule.status == "validated" and schedule.validated_signature == dependency_signature(schedule) else "draft"


@transaction.atomic
def save_record(*, user, form_class, data, pk=None, term=None):
    model = form_class._meta.model
    authorized(user, model, "change" if pk else "add")
    mutation_lock()
    original = get_object_or_404(scoped(user, model.objects.select_for_update()), pk=pk) if pk else None
    if model is Schedule and original:
        require_editable(original)
    before = snapshot(original) if original else {}
    form = form_class(data, user=user, instance=original, term=term)
    if not form.is_valid():
        return None, form
    obj = form.save(commit=False)
    validate_active_term(obj.academic_term)
    if not pk:
        obj.created_by = user
    if model is Schedule:
        if original:
            obj.status = Schedule.Status.NEEDS_REVISION if original.status == Schedule.Status.NEEDS_REVISION else Schedule.Status.DRAFT
            obj.revision_token = uuid4().hex
        obj.validated_signature = ""
    obj.save()
    event(f"{model._meta.model_name}.{'updated' if pk else 'created'}", user, obj, before, notes_changed="notes" in form.changed_data)
    if model is Schedule and before.get("status") == "validated":
        event("schedule.status_changed", user, obj, before)
    return obj, form


@transaction.atomic
def save_entry(*, user, schedule_id, data, pk=None, preview=False):
    authorized(user, ScheduleEntry, "change" if pk else "add")
    mutation_lock()
    schedule = get_schedule(user, schedule_id, lock=True)
    require_editable(schedule)
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
    schedule = get_schedule(user, schedule_id, lock=True)
    require_editable(schedule)
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
    schedule = get_schedule(user, schedule_id, "validate", lock=True)
    # Retained submissions can re-enter the staff workflow through validation.
    # Their submission records remain intact; published versions stay immutable.
    if schedule.status != Schedule.Status.UNDER_REVIEW:
        require_editable(schedule)
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

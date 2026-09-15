from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404

from academics.models import Subject
from accounts.permissions import require_access
from audit.services import record_event
from faculty.models import Faculty
from resources.services import permission_for
from .calculation import calculate_workload, validate_workload
from .models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering
from .selectors import accessible_terms, scoped_records


def snapshot(obj):
    # Numeric/time context supports review without duplicating private notes.
    fields = ("faculty_id", "subject_id", "subject_offering_id", "department_id", "code", "day_of_week", "start_time", "end_time", "availability_type", "share", "lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours", "is_active")
    return {name: str(getattr(obj, name)) for name in fields if hasattr(obj, name)}


def term_for(obj):
    return obj.academic_term


def scoped_object(user, model, pk, term, *, lock=False):
    qs = model.objects.select_for_update(of=("self",)) if lock else model.objects.all()
    lookup = "subject_offering__academic_term" if model is FacultySubjectAssignment else "academic_term"
    return get_object_or_404(scoped_records(user, qs).filter(**{lookup: term}), pk=pk)


def check_share(obj):
    used = FacultySubjectAssignment.objects.filter(subject_offering=obj.subject_offering).exclude(pk=obj.pk).aggregate(total=Sum("share"))["total"] or 0
    if used + obj.share > 1:
        raise ValidationError("Total assigned teaching shares for this offering would exceed 1. Reduce the share or create a separate offering.")


@transaction.atomic
def save_term_record(*, user, form_class, data, term, pk=None, preview=False):
    model = form_class._meta.model
    require_access(user, permission_for(model, "change" if pk else "add"))
    get_object_or_404(accessible_terms(user, active=True), pk=term.pk)
    original = scoped_object(user, model, pk, term) if pk else None
    before = snapshot(original) if original else {}
    form = form_class(data, user=user, term=term, instance=original)
    if not form.is_valid():
        return None, form, None
    obj = form.save(commit=False)
    # Consistent lock order: faculty, catalog subject, offering, edited child row.
    if hasattr(obj, "faculty_id"):
        Faculty.objects.select_for_update().get(pk=obj.faculty_id)
    if model is SubjectOffering:
        Subject.objects.select_for_update().get(pk=obj.subject_id)
    if model is FacultySubjectAssignment:
        SubjectOffering.objects.select_for_update().get(pk=obj.subject_offering_id)
    if pk:
        original = scoped_object(user, model, pk, term, lock=True)
    # Re-read form choices after acquiring locks: deactivation/ownership may change.
    form = form_class(data, user=user, term=term, instance=original)
    if not form.is_valid():
        return None, form, None
    obj = form.save(commit=False)
    report = None
    if model is FacultySubjectAssignment:
        check_share(obj)
        current = calculate_workload(obj.faculty, term)
        report = validate_workload(obj.faculty, term, obj, exclude_pk=pk)
        report["current_load"] = current["assigned_load"]
        report["change_load"] = report["assigned_load"] - current["assigned_load"] if report["assigned_load"] is not None and current["assigned_load"] is not None else None
        report["offering"] = obj.subject_offering
        report["assignment_teaching_units"] = obj.subject_offering.total_units * obj.share
        report["assignment_teaching_hours"] = (obj.subject_offering.lecture_hours + obj.subject_offering.laboratory_hours) * obj.share
    if preview:
        return obj, form, report
    if not pk:
        obj.created_by = user
    obj.save()
    record_event(f"{model._meta.model_name}.{'updated' if pk else 'created'}", actor=user, obj=obj, details={"academic_term_id": term.pk, "before": before, "after": snapshot(obj), "notes_changed": "notes" in form.changed_data})
    return obj, form, report


@transaction.atomic
def remove_term_record(*, user, model, pk, term):
    if model not in (FacultyAvailability, FacultySubjectAssignment):
        raise ValidationError("Offerings are deactivated rather than deleted.")
    require_access(user, permission_for(model, "delete"))
    get_object_or_404(accessible_terms(user), pk=term.pk)
    obj = scoped_object(user, model, pk, term)
    Faculty.objects.select_for_update().get(pk=obj.faculty_id)
    if model is FacultySubjectAssignment:
        SubjectOffering.objects.select_for_update().get(pk=obj.subject_offering_id)
    obj = scoped_object(user, model, pk, term, lock=True)
    record_event(f"{model._meta.model_name}.deleted", actor=user, obj=obj, details={"academic_term_id": term.pk, "before": snapshot(obj)})
    obj.delete()

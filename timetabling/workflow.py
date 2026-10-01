"""Staff finalization, historical review and official schedule transitions.

The Phase 4 validator decides timetable validity. PostgreSQL official-booking
exclusions provide the final guarantee when publications race.
"""

from datetime import timedelta
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.permissions import require_access
from audit.services import record_event
from workloads.models import WorkloadPolicy, validate_active_term
from .conflicts import get_schedule_conflicts
from .locking import scheduling_lock
from .models import (
    ActiveSchedule, OfficialResourceBooking, Schedule, ScheduleApprovalSnapshot,
    ScheduleEntry, ScheduleWorkflowEvent, SchedulingConfiguration,
)
from .queries import get_schedule
from .signatures import dependency_signature


EDITABLE_STATES = (Schedule.Status.DRAFT, Schedule.Status.VALIDATED, Schedule.Status.NEEDS_REVISION)


def finalize_schedule(*, user, schedule_id, revision_token, acknowledged_warnings=(), remarks=""):
    require_access(user, "timetabling.finalize_schedule")
    require_access(user, "timetabling.change_schedule")
    from .finalization_validation import publication_conflicts
    try:
        with transaction.atomic():
            scheduling_lock()
            schedule = get_schedule(user, schedule_id, lock=True)
            _current_revision(schedule, revision_token)
            if schedule.status not in (*EDITABLE_STATES, Schedule.Status.UNDER_REVIEW):
                raise ValidationError("This version has already been published. Prepare a revision to replace it.")
            validate_active_term(schedule.academic_term)
            signature = dependency_signature(schedule)
            if not schedule.validated_signature:
                raise ValidationError("Validate this version before finalizing and publishing it.")
            if signature != schedule.validated_signature:
                raise ValidationError("The draft, scheduling inputs or official selection changed since validation. Validate again.")
            active = _active_selection(schedule)
            findings = publication_conflicts(schedule, user,
                excluded_schedule_ids=(active.schedule_id,) if active else ())
            errors = [f"{c.code}: {c.message} {c.remedy}" for c in findings if c.severity == 'ERROR']
            if errors:
                raise ValidationError(errors)
            warnings = [c for c in findings if c.severity == 'WARNING']
            _acknowledged(warnings, acknowledged_warnings)
            if signature != dependency_signature(schedule):
                raise ValidationError("Scheduling inputs changed during validation. Validate again.")
            return _publish(schedule, user, signature, active, warnings, remarks=remarks, finalizing=True)
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.__cause__, 'diag', None), 'constraint_name', '')
        if constraint.startswith('official_'):
            raise ValidationError('An official resource booking conflicts with this version. Validate and adjust its meetings.') from exc
        raise


def _current_revision(schedule, token):
    if not token or str(token) != schedule.revision_token:
        raise ValidationError("This schedule changed. Reload it and try again.")


def _warnings_and_errors(schedule, user, *, replaced_schedule_id=None):
    excluded = (replaced_schedule_id,) if replaced_schedule_id else ()
    findings = get_schedule_conflicts(schedule, user=user, excluded_schedule_ids=excluded)
    if not schedule.entries.exists():
        raise ValidationError("Add at least one meeting before submitting this schedule.")
    if any(item.severity == "ERROR" for item in findings):
        raise ValidationError("The schedule has blocking timetable conflicts. Validate and resolve them first.")
    return [item for item in findings if item.severity == "WARNING"]


def _warning_codes(warnings):
    return sorted({item.code for item in warnings})


def _acknowledged(warnings, acknowledged):
    required = set(_warning_codes(warnings))
    supplied = set(acknowledged or ())
    if not required.issubset(supplied):
        raise ValidationError("Acknowledge every current timetable warning before continuing.")


def _active_selection(schedule):
    return ActiveSchedule.objects.select_for_update().filter(
        academic_term_id=schedule.academic_term_id,
        department_id=schedule.department_id,
    ).first()


def _event(schedule, action, user, *, remarks="", warning_codes=()):
    return ScheduleWorkflowEvent.objects.create(
        schedule=schedule, action=action, actor=user,
        revision_token=schedule.revision_token,
        remarks=remarks, warning_codes=list(warning_codes),
    )


def _snapshot_payload(schedule, warnings, reviewer):
    term = schedule.academic_term
    entries = []
    rows = ScheduleEntry.objects.filter(schedule=schedule).select_related(
        "assignment__faculty", "assignment__subject_offering__subject",
        "assignment__subject_offering__scheduling_requirement__section", "room",
    ).order_by("day_of_week", "start_time", "pk")
    for entry in rows:
        assignment = entry.assignment
        offering = assignment.subject_offering
        section = offering.scheduling_requirement.section
        entries.append({
            "entry_id": entry.pk,
            "day_of_week": entry.day_of_week,
            "start_time": entry.start_time.isoformat(),
            "end_time": entry.end_time.isoformat(),
            "meeting_type": entry.meeting_type,
            "faculty_id": assignment.faculty_id,
            "faculty_name": str(assignment.faculty),
            "subject_code": offering.subject.code,
            "subject_title": offering.subject.title,
            "offering_id": offering.pk,
            "offering_code": offering.code,
            "section_id": section.pk,
            "section_code": section.code,
            "room_id": entry.room_id,
            "room_code": entry.room.code,
            "room_name": entry.room.name,
            "assignment_id": assignment.pk,
            "assignment_share": str(assignment.share),
            "lecture_units": str(offering.lecture_units),
            "laboratory_units": str(offering.laboratory_units),
            "lecture_hours": str(offering.lecture_hours),
            "laboratory_hours": str(offering.laboratory_hours),
        })
    configuration = SchedulingConfiguration.objects.filter(
        academic_term_id=term.pk, department_id=schedule.department_id,
    ).values(
        "allowed_weekdays", "earliest_start", "latest_end", "slot_increment_minutes",
        "faculty_preference_weight", "faculty_gap_weight", "section_gap_weight",
        "meeting_distribution_weight", "room_fit_weight",
    ).first()
    if configuration:
        configuration = {key: value.isoformat() if hasattr(value, "isoformat") else value
                         for key, value in configuration.items()}
    policies = list(WorkloadPolicy.objects.filter(academic_term_id=term.pk).filter(
        Q(department_id=schedule.department_id)
        | Q(college_id=schedule.department.college_id)
        | Q(department__isnull=True, college__isnull=True)
    ).values("id", "department_id", "college_id", "recommended_load", "maximum_load", "enforce_maximum", "lecture_weight", "laboratory_weight"))
    return {
        "schedule": {
            "id": schedule.pk, "name": schedule.name,
            "family_id": schedule.family_id, "family_name": schedule.family.name,
            "version_number": schedule.version_number,
            "academic_term_id": term.pk, "academic_term_code": term.code,
            "term_start": term.start_date.isoformat(), "term_end": term.end_date.isoformat(),
            "department_id": schedule.department_id, "department_name": str(schedule.department),
        },
        "entries": entries,
        "warnings": [{"code": item.code, "message": item.message} for item in warnings],
        "warning_acknowledgements": {
            "submitted_by_id": schedule.submitted_by_id,
            "submitted_at": schedule.submitted_at.isoformat() if schedule.submitted_at else None,
            "submitter_codes": list(schedule.submitted_warning_codes),
            "reviewer_id": reviewer.pk,
            "reviewer_codes": _warning_codes(warnings),
        },
        "configuration": configuration or {},
        "workload_policies": [{key: str(value) if value is not None else None for key, value in row.items()}
                              for row in policies],
    }


def _booking_rows(schedule):
    term = schedule.academic_term
    rows = []
    entries = ScheduleEntry.objects.filter(schedule=schedule).select_related(
        "assignment__subject_offering__scheduling_requirement",
    ).order_by("pk")
    for entry in entries:
        section_id = entry.assignment.subject_offering.scheduling_requirement.section_id
        first = term.start_date + timedelta(days=(entry.day_of_week - term.start_date.isoweekday()) % 7)
        day = first
        while day <= term.end_date:
            rows.append(OfficialResourceBooking(
                schedule_entry=entry, booking_date=day,
                start_time=entry.start_time, end_time=entry.end_time,
                faculty_id=entry.assignment.faculty_id, room_id=entry.room_id,
                section_id=section_id,
            ))
            day += timedelta(days=7)
    return rows


def submit_schedule(*, user, schedule_id, revision_token, acknowledged_warnings=()):
    require_access(user, "timetabling.submit_schedule")
    require_access(user, "timetabling.change_schedule")
    with transaction.atomic():
        scheduling_lock()
        schedule = get_schedule(user, schedule_id, lock=True)
        _current_revision(schedule, revision_token)
        if schedule.status not in EDITABLE_STATES:
            raise ValidationError("This schedule cannot be submitted in its current state.")
        validate_active_term(schedule.academic_term)
        active = _active_selection(schedule)
        warnings = _warnings_and_errors(
            schedule, user, replaced_schedule_id=active.schedule_id if active else None,
        )
        _acknowledged(warnings, acknowledged_warnings)
        signature = dependency_signature(schedule)
        resubmission = ScheduleWorkflowEvent.objects.filter(
            schedule=schedule, action__in=("submitted", "resubmitted"),
        ).exists()
        schedule.status = Schedule.Status.UNDER_REVIEW
        schedule.validated_signature = signature
        schedule.submitted_by = user
        schedule.submitted_at = timezone.now()
        schedule.submitted_revision_token = schedule.revision_token
        schedule.submitted_signature = signature
        schedule.submitted_warning_codes = _warning_codes(warnings)
        schedule.submitted_active_schedule_id = active.schedule_id if active else None
        schedule.save(update_fields=[
            "status", "validated_signature", "submitted_by", "submitted_at",
            "submitted_revision_token", "submitted_signature", "submitted_warning_codes",
            "submitted_active_schedule", "updated_at",
        ])
        action = "resubmitted" if resubmission else "submitted"
        _event(schedule, action, user, warning_codes=schedule.submitted_warning_codes)
        record_event(f"schedule.{action}", actor=user, obj=schedule,
                     details={"version_number": schedule.version_number,
                              "revision_token": schedule.revision_token,
                              "warning_codes": schedule.submitted_warning_codes})
        return schedule


def return_schedule(*, user, schedule_id, revision_token, remarks):
    require_access(user, "timetabling.review_schedule")
    if not remarks or not remarks.strip():
        raise ValidationError("Enter a reason for returning this schedule.")
    with transaction.atomic():
        scheduling_lock()
        schedule = get_schedule(user, schedule_id, lock=True)
        _current_revision(schedule, revision_token)
        if schedule.status != Schedule.Status.UNDER_REVIEW:
            raise ValidationError("Only a submitted schedule can be returned.")
        if schedule.submitted_by_id == user.pk:
            raise ValidationError("A submitter cannot review their own schedule.")
        schedule.status = Schedule.Status.NEEDS_REVISION
        schedule.revision_token = uuid4().hex
        schedule.validated_signature = ""
        schedule.save(update_fields=["status", "revision_token", "validated_signature", "updated_at"])
        _event(schedule, "returned", user, remarks=remarks.strip())
        record_event("schedule.returned", actor=user, obj=schedule,
                     details={"version_number": schedule.version_number,
                              "revision_token": schedule.revision_token})
        return schedule


def approve_schedule(*, user, schedule_id, revision_token, acknowledged_warnings=(), remarks=""):
    require_access(user, "timetabling.approve_schedule")
    try:
        with transaction.atomic():
            scheduling_lock()
            schedule = get_schedule(user, schedule_id, lock=True)
            _current_revision(schedule, revision_token)
            if schedule.status != Schedule.Status.UNDER_REVIEW:
                raise ValidationError("Only a submitted schedule can be approved.")
            if schedule.submitted_by_id == user.pk:
                raise ValidationError("A submitter cannot approve their own schedule.")
            if schedule.submitted_revision_token != schedule.revision_token:
                raise ValidationError("The submitted revision is stale. Submit the schedule again.")
            validate_active_term(schedule.academic_term)
            current_signature = dependency_signature(schedule)
            if current_signature != schedule.submitted_signature:
                raise ValidationError("Scheduling inputs changed after submission. Validate and submit again.")
            active = _active_selection(schedule)
            old_schedule_id = active.schedule_id if active else None
            if old_schedule_id != schedule.submitted_active_schedule_id:
                raise ValidationError("The official schedule changed after submission. Submit again.")
            warnings = _warnings_and_errors(schedule, user, replaced_schedule_id=old_schedule_id)
            codes = _warning_codes(warnings)
            if codes != sorted(schedule.submitted_warning_codes):
                raise ValidationError("Timetable warnings changed after submission. Submit again.")
            _acknowledged(warnings, acknowledged_warnings)
            return _publish(schedule, user, current_signature, active, warnings, remarks=remarks)
    except IntegrityError as exc:
        # PostgreSQL exclusions are deliberately the final barrier. Do not
        # reveal protected peer names or SQL constraint details to reviewers.
        constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", "")
        if constraint and constraint.startswith("official_"):
            raise ValidationError("An official resource booking conflicts with this schedule.") from exc
        raise


def revise_approved_schedule(*, user, schedule_id, revision_token):
    require_access(user, "timetabling.revise_schedule")
    require_access(user, "timetabling.change_schedule")
    with transaction.atomic():
        scheduling_lock()
        source = get_schedule(user, schedule_id, lock=True)
        _current_revision(source, revision_token)
        if source.status != Schedule.Status.APPROVED:
            raise ValidationError("Only an approved schedule can be revised as a new version.")
        family = type(source.family).objects.select_for_update().get(pk=source.family_id)
        latest = Schedule.objects.filter(family=family).order_by("-version_number").first()
        clone = Schedule.objects.create(
            academic_term=source.academic_term, department=source.department,
            family=family, version_number=latest.version_number + 1,
            parent_version=source, name=source.name, status=Schedule.Status.DRAFT,
            created_by=user,
        )
        for entry in ScheduleEntry.objects.filter(schedule=source).order_by("pk"):
            ScheduleEntry.objects.create(
                schedule=clone, assignment=entry.assignment, room=entry.room,
                day_of_week=entry.day_of_week, start_time=entry.start_time,
                end_time=entry.end_time, meeting_type=entry.meeting_type,
                is_locked=True, notes=entry.notes, created_by=user,
            )
        _event(clone, "revised", user)
        record_event("schedule.revision_created", actor=user, obj=clone,
                     details={"parent_schedule_id": source.pk,
                              "version_number": clone.version_number})
        return clone


def _publish(schedule, user, current_signature, active, warnings, *, remarks="", finalizing=False):
    now = timezone.now()
    codes = _warning_codes(warnings)
    old_schedule_id = active.schedule_id if active else None
    payload = _snapshot_payload(schedule, warnings, user)
    if finalizing:
        from workloads.calculation import calculate_workload
        from workloads.models import FacultySubjectAssignment
        from faculty.models import Faculty
        payload['finalization'] = {'finalized_by_id': user.pk, 'finalized_at': now.isoformat()}
        summaries = {}
        for person in Faculty.objects.filter(pk__in=schedule.entries.values('assignment__faculty_id')):
            published_assignments = FacultySubjectAssignment.objects.filter(
                faculty=person, pk__in=schedule.entries.values('assignment_id'),
            ).select_related('subject_offering')
            report = calculate_workload(person, schedule.academic_term,
                assignments_override=published_assignments)
            summaries[str(person.pk)] = {key: str(report[key]) if report[key] is not None else None
                for key in ('lecture_units', 'laboratory_units', 'teaching_units', 'teaching_hours', 'assigned_load', 'status')}
        payload['workload_summaries'] = summaries
    bookings = _booking_rows(schedule)
    ScheduleApprovalSnapshot.objects.create(
        schedule=schedule, revision_token=schedule.revision_token,
        dependency_signature=current_signature, approved_by=user,
        approved_at=now, payload=payload,
    )
    schedule.status = Schedule.Status.APPROVED
    schedule.save(update_fields=["status", "updated_at"])
    if active:
        active.schedule = schedule
        active.selected_by = user
        active.selected_at = now
        active.save(update_fields=["schedule", "selected_by", "selected_at"])
    else:
        ActiveSchedule.objects.create(
            academic_term=schedule.academic_term, department=schedule.department,
            schedule=schedule, selected_by=user, selected_at=now,
        )
    if old_schedule_id:
        OfficialResourceBooking.objects.filter(schedule_entry__schedule_id=old_schedule_id).delete()
    OfficialResourceBooking.objects.bulk_create(bookings)
    _event(schedule, "finalized" if finalizing else "approved", user, remarks=(remarks or "").strip(), warning_codes=codes)
    if old_schedule_id and old_schedule_id != schedule.pk:
        _event(schedule, "replaced", user)
        record_event("schedule.official_replaced", actor=user, obj=schedule,
                     details={"previous_schedule_id": old_schedule_id,
                              "version_number": schedule.version_number})
    record_event("schedule.finalized" if finalizing else "schedule.approved", actor=user, obj=schedule,
                 details={"version_number": schedule.version_number,
                          "revision_token": schedule.revision_token,
                          "booking_count": len(bookings)})
    record_event("schedule.official_selected", actor=user, obj=schedule,
                 details={"previous_schedule_id": old_schedule_id})
    return schedule

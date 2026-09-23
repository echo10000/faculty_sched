"""Deterministic fingerprint of authoritative balancing inputs in one scope.

Call while holding ``timetabling.locking.scheduling_lock`` inside an atomic
transaction when recording or comparing a recommendation snapshot. Database
triggers acquire that same lock for edits to the workload dependencies.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Q

from academics.models import AcademicTerm
from core.models import Department
from faculty.models import Faculty, FacultyQualification
from timetabling.models import AssignmentMeetingRequirement, Schedule, ScheduleEntry

from .models import FacultySubjectAssignment, FacultyTermCapacity, SubjectOffering, WorkloadPolicy


def _primitive(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def _rows(queryset, *fields):
    return [list(map(_primitive, row)) for row in queryset.order_by("pk").values_list(*fields)]


def compute_balancing_source_signature(*, academic_term, department) -> str:
    """Return a SHA-256 digest of material term/department recommendation inputs.

    The signature is scoped by both identifiers, includes applicable broad
    policies, and excludes unrelated organizational rows. It does not contain
    proposal or run state, so repeated captures of unchanged inputs agree.
    """
    term_id = academic_term.pk if isinstance(academic_term, AcademicTerm) else academic_term
    department_id = department.pk if isinstance(department, Department) else department
    scope = Department.objects.select_related("college").get(pk=department_id)
    term = AcademicTerm.objects.select_related("academic_year", "semester").get(pk=term_id)

    faculty_ids = list(Faculty.objects.filter(home_department_id=department_id).values_list("pk", flat=True))
    scoped_offerings = SubjectOffering.objects.filter(
        academic_term_id=term_id, department_id=department_id,
    )
    assignments = FacultySubjectAssignment.objects.filter(
        Q(subject_offering__academic_term_id=term_id, subject_offering__department_id=department_id)
        | Q(subject_offering__academic_term_id=term_id, faculty_id__in=faculty_ids)
    ).distinct()
    offering_ids = set(scoped_offerings.values_list("pk", flat=True))
    offering_ids.update(assignments.values_list("subject_offering_id", flat=True))
    offering_rows = SubjectOffering.objects.filter(pk__in=offering_ids)
    subject_ids = set(offering_rows.values_list("subject_id", flat=True))
    schedule_rows = Schedule.objects.filter(academic_term_id=term_id, department_id=department_id)

    snapshot = {
        "version": 1,
        "scope": {
            "academic_term": [
                term.pk, term.code, term.start_date.isoformat(), term.end_date.isoformat(),
                term.is_active, term.academic_year_id, term.academic_year.is_active,
                term.semester_id, term.semester.is_active,
            ],
            "department": [scope.pk, scope.is_active, scope.college_id, scope.college.is_active],
        },
        "faculty": _rows(
            Faculty.objects.filter(pk__in=faculty_ids),
            "pk", "home_department_id", "is_active", "recommended_load", "maximum_load",
        ),
        "offerings": _rows(
            offering_rows,
            "pk", "academic_term_id", "department_id", "subject_id", "code",
            "lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours", "is_active",
            "subject__is_active", "subject__owning_department_id", "subject__units",
            "subject__lecture_units", "subject__laboratory_units",
        ),
        "assignments": _rows(assignments, "pk", "faculty_id", "subject_offering_id", "share"),
        "policies": _rows(
            WorkloadPolicy.objects.filter(academic_term_id=term_id).filter(
                Q(department_id=department_id) | Q(college_id=scope.college_id)
                | Q(department__isnull=True, college__isnull=True)
            ),
            "pk", "academic_term_id", "college_id", "department_id",
            "recommended_load", "maximum_load", "enforce_maximum",
            "lecture_weight", "laboratory_weight",
        ),
        "capacities": _rows(
            FacultyTermCapacity.objects.filter(academic_term_id=term_id, faculty_id__in=faculty_ids),
            "pk", "faculty_id", "academic_term_id", "recommended_load", "maximum_load",
            "enforce_maximum",
        ),
        "qualifications": _rows(
            FacultyQualification.objects.filter(faculty_id__in=faculty_ids, subject_id__in=subject_ids),
            "pk", "faculty_id", "subject_id",
        ),
        "meeting_requirements": _rows(
            AssignmentMeetingRequirement.objects.filter(assignment_id__in=assignments.values("pk")),
            "pk", "assignment_id", "meeting_type", "meetings_per_week", "duration_minutes",
        ),
        "schedules": _rows(schedule_rows, "pk", "academic_term_id", "department_id", "status"),
        "schedule_entries": _rows(
            ScheduleEntry.objects.filter(schedule_id__in=schedule_rows.values("pk")),
            "pk", "schedule_id", "assignment_id", "room_id", "day_of_week", "start_time",
            "end_time", "meeting_type", "is_locked", "generation_run_id",
        ),
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()

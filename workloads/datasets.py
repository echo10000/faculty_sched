"""Scoped, deterministic data contracts for future balancing consumers. No ranking or AI."""
from django.shortcuts import get_object_or_404
from accounts.permissions import require_access
from .calculation import calculate_workload
from .models import FacultyAvailability, SubjectOffering
from .selectors import accessible_terms, scoped_faculty, scoped_records


def faculty_candidates(user, term):
    require_access(user, "workloads.view_workload")
    get_object_or_404(accessible_terms(user), pk=term.pk)
    for faculty in scoped_faculty(user):
        report = calculate_workload(faculty, term)
        availability = None
        if user.has_perm("workloads.view_facultyavailability"):
            availability = list(FacultyAvailability.objects.filter(faculty=faculty, academic_term=term).values("day_of_week", "start_time", "end_time", "availability_type"))
        yield {"faculty_id": faculty.pk, "department_id": faculty.home_department_id, "is_active": faculty.is_active,
               "current_workload": report["assigned_load"], "capacity": report["policy"], "remaining_capacity": report["remaining_capacity"],
               "status": report["status"], "availability": availability, "offering_ids": [a.subject_offering_id for a in report["assignments"]]}


def offering_data(user, term):
    require_access(user, "workloads.view_subjectoffering")
    get_object_or_404(accessible_terms(user), pk=term.pk)
    return list(scoped_records(user, SubjectOffering.objects.filter(academic_term=term)).values("id", "subject_id", "department_id", "academic_term_id", "lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours", "is_active"))

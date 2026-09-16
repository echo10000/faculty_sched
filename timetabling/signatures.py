import hashlib
import json

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from core.models import College, Department
from faculty.models import Faculty
from resources.models import Building, RoomType
from scheduling.models import Room
from workloads.models import (
    FacultyAvailability,
    FacultySubjectAssignment,
    SubjectOffering,
)

from .models import (
    AssignmentMeetingRequirement,
    ClassSection,
    OfferingRequirement,
    RoomUnavailability,
    Schedule,
    ScheduleEntry,
    SchedulingConfiguration,
)


DEPENDENCY_FIELD_SPECS = (
    (Schedule, ("id", "name", "department_id", "academic_term_id")),
    (
        ScheduleEntry,
        (
            "id",
            "schedule_id",
            "assignment_id",
            "room_id",
            "day_of_week",
            "start_time",
            "end_time",
            "meeting_type",
            "is_locked",
            "generation_run_id",
        ),
    ),
    (
        SchedulingConfiguration,
        (
            "id",
            "academic_term_id",
            "department_id",
            "allowed_weekdays",
            "earliest_start",
            "latest_end",
            "slot_increment_minutes",
            "solver_time_limit_seconds",
            "random_seed",
            "worker_count",
            "faculty_preference_weight",
            "faculty_gap_weight",
            "section_gap_weight",
            "meeting_distribution_weight",
            "room_fit_weight",
        ),
    ),
    (
        AssignmentMeetingRequirement,
        (
            "id",
            "assignment_id",
            "meeting_type",
            "meetings_per_week",
            "duration_minutes",
        ),
    ),
    (
        ClassSection,
        (
            "id",
            "department_id",
            "academic_term_id",
            "code",
            "is_active",
            "expected_size",
        ),
    ),
    (
        OfferingRequirement,
        (
            "id",
            "subject_offering_id",
            "section_id",
            "room_type_id",
            "room_type_mandatory",
            "capacity_is_hard",
        ),
    ),
    (
        RoomUnavailability,
        (
            "id",
            "room_id",
            "academic_term_id",
            "day_of_week",
            "start_time",
            "end_time",
        ),
    ),
    (
        FacultyAvailability,
        (
            "id",
            "faculty_id",
            "academic_term_id",
            "day_of_week",
            "start_time",
            "end_time",
            "availability_type",
        ),
    ),
    (
        FacultySubjectAssignment,
        ("id", "faculty_id", "subject_offering_id", "share"),
    ),
    (
        SubjectOffering,
        (
            "id",
            "subject_id",
            "department_id",
            "academic_term_id",
            "is_active",
            "lecture_hours",
            "laboratory_hours",
        ),
    ),
    (Subject, ("id", "is_active", "required_room_type", "owning_department_id")),
    (Faculty, ("id", "is_active", "home_department_id")),
    (
        Room,
        (
            "id",
            "is_active",
            "capacity",
            "category_id",
            "building_id",
            "owner_department_id",
            "owner_college_id",
        ),
    ),
    (RoomType, ("id", "code", "is_active")),
    (Building, ("id", "is_active")),
    (
        AcademicTerm,
        (
            "id",
            "start_date",
            "end_date",
            "is_active",
            "academic_year_id",
            "semester_id",
        ),
    ),
    (AcademicYear, ("id", "is_active", "start_date", "end_date")),
    (Semester, ("id", "is_active")),
    (Department, ("id", "is_active", "college_id")),
    (College, ("id", "is_active")),
)


def dependency_signature(schedule: Schedule) -> str:
    """Return a conservative institution-wide feasibility digest.

    Generation-run lifecycle/proposal data, audit data, notes, and timestamps are
    deliberately absent. Recording a run therefore cannot make its own source
    signature stale.
    """
    payload = {
        model._meta.label: list(
            model.objects.order_by("pk").values_list(*fields)
        )
        for model, fields in DEPENDENCY_FIELD_SPECS
    }
    return hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True).encode()
    ).hexdigest()

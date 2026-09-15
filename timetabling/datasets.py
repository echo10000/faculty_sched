"""Scoped scheduling inputs. This module does not select times or solve schedules."""
from accounts.permissions import require_access
from resources.selectors import scope_resources
from scheduling.models import Room
from workloads.calculation import calculate_workload
from workloads.models import FacultyAvailability, FacultySubjectAssignment
from workloads.selectors import scoped_records
from .models import ScheduleEntry, OfferingRequirement, RoomUnavailability
from .queries import get_schedule, authorized, scoped


def scheduling_input(user, schedule_id):
    schedule = get_schedule(user, schedule_id)
    authorized(user, ScheduleEntry)
    authorized(user, OfferingRequirement)
    authorized(user, RoomUnavailability)
    require_access(user, "workloads.view_facultyavailability")
    require_access(user, "workloads.view_workload")
    assignments = []
    for assignment in scoped_records(user, FacultySubjectAssignment.objects.filter(subject_offering__academic_term=schedule.academic_term, subject_offering__department=schedule.department)).select_related("faculty__home_department", "subject_offering__subject"):
        offering = assignment.subject_offering
        requirement = scoped(user, OfferingRequirement.objects.all()).filter(subject_offering=offering).first()
        report = calculate_workload(assignment.faculty, schedule.academic_term)
        assignments.append({"assignment_id": assignment.pk, "offering_id": offering.pk, "subject_id": offering.subject_id,
            "section_id": requirement.section_id if requirement else None, "faculty_id": assignment.faculty_id,
            "lecture_hours": offering.lecture_hours * assignment.share, "laboratory_hours": offering.laboratory_hours * assignment.share,
            "required_room_type_id": requirement.room_type_id if requirement else None,
            "catalog_required_room_type": offering.subject.required_room_type,
            "room_type_mandatory": requirement.room_type_mandatory if requirement else False,
            "capacity_is_hard": requirement.capacity_is_hard if requirement else False,
            "expected_size": requirement.section.expected_size if requirement else None,
            "workload": report["assigned_load"], "maximum_load": report["policy"]["maximum_load"],
            "is_active": assignment.faculty.is_active and offering.is_active and offering.subject.is_active,
            "availability": list(FacultyAvailability.objects.filter(faculty=assignment.faculty, academic_term=schedule.academic_term).values("day_of_week", "start_time", "end_time", "availability_type"))})
    rooms = list(scope_resources(user, Room.objects.all()).values("id", "code", "category_id", "capacity", "is_active"))
    return {"schedule": {"id": schedule.pk, "term_id": schedule.academic_term_id, "department_id": schedule.department_id},
        "term_dates": [schedule.academic_term.start_date, schedule.academic_term.end_date], "assignments": assignments, "rooms": rooms,
        "room_unavailability": list(scoped(user, RoomUnavailability.objects.filter(academic_term__start_date__lte=schedule.academic_term.end_date, academic_term__end_date__gte=schedule.academic_term.start_date)).values("room_id", "academic_term_id", "day_of_week", "start_time", "end_time")),
        "existing_entries": list(scoped(user, ScheduleEntry.objects.filter(schedule=schedule)).values("id", "assignment_id", "room_id", "day_of_week", "start_time", "end_time", "meeting_type")),
        "time_domain": {"weekdays": list(range(1, 8)), "institutional_hours": None, "fixed_time_grid": None}}

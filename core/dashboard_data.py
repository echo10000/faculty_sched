"""Permission-scoped, term-specific reads for the existing overview page."""

from collections import Counter
from decimal import Decimal

from django.db.models import Count, DurationField, ExpressionWrapper, F, Sum

from accounts.permissions import department_scoped_queryset
from resources.selectors import scope_resources


WORKLOAD_LABELS = (
    ("UNDERLOAD", "Underload"),
    ("WITHIN_LOAD", "Within load"),
    ("AT_CAPACITY", "At capacity"),
    ("OVERLOAD", "Overload"),
    ("UNCONFIGURED", "Unconfigured"),
)
SCHEDULE_LABELS = (
    ("draft", "Draft"),
    ("validated", "Validated"),
    ("under_review", "Under review"),
    ("needs_revision", "Needs revision"),
    ("approved", "Published versions"),
)
GENERATION_LABELS = (
    ("PROPOSAL_READY", "Ready"),
    ("ACCEPTED", "Accepted"),
    ("DISCARDED", "Discarded"),
    ("INFEASIBLE", "Infeasible"),
    ("STALE", "Stale"),
    ("FAILED", "Failed"),
)
BALANCING_LABELS = (
    ("PROPOSAL_READY", "Ready"),
    ("ACCEPTED", "Accepted"),
    ("DISCARDED", "Discarded"),
    ("INFEASIBLE", "Infeasible"),
    ("STALE", "Stale"),
    ("FAILED", "Failed"),
)


def _distribution(labels, counts):
    total = sum(counts.values())
    return [
        {"key": key, "label": label, "count": counts.get(key, 0),
         "percent": round(counts.get(key, 0) * 100 / total) if total else 0}
        for key, label in labels
    ]


def _hours(duration):
    if duration is None:
        return Decimal("0.00")
    return (Decimal(str(duration.total_seconds())) / Decimal("3600")).quantize(Decimal("0.01"))


def _duration_expression():
    return ExpressionWrapper(F("end_time") - F("start_time"), output_field=DurationField())


def build_monitoring(user, term):
    """Return only sections backed by the viewer's granted capabilities.

    Every organization-bearing queryset is scoped before aggregation. No
    solver, approval transition, or alternate workload calculation runs here.
    """
    result = dict.fromkeys((
        "workload", "schedules", "rooms", "teaching", "generation",
        "balancing", "departments",
    ))
    if term is None:
        return result

    from faculty.models import Faculty
    from scheduling.models import Room
    from workloads.calculation import calculate_workloads
    from workloads.models import FacultySubjectAssignment, SubjectOffering
    from workloads.selectors import scoped_faculty, scoped_records
    from timetabling.models import (
        ActiveSchedule, OfficialResourceBooking, Schedule, ScheduleApprovalSnapshot,
        ScheduleEntry, ScheduleGenerationRun,
    )
    from timetabling.queries import scoped as scoped_timetable

    if user.has_perm("workloads.view_workload"):
        people = scoped_faculty(user).filter(
            is_active=True, home_department__is_active=True,
            home_department__college__is_active=True,
        )
        reports = calculate_workloads(people, term)
        status_labels = dict(WORKLOAD_LABELS)
        for report in reports:
            report["status_label"] = status_labels[report["status"]]
        counts = Counter(report["status"] for report in reports)
        result["workload"] = {
            "counts": _distribution(WORKLOAD_LABELS, counts),
            "faculty": sorted(reports, key=lambda row: (
                row["status"] not in ("OVERLOAD", "UNCONFIGURED", "UNDERLOAD"),
                row["faculty"].last_name, row["faculty"].first_name,
            ))[:8],
            "total": len(reports),
        }

    can_schedule = user.has_perm("timetabling.view_schedule")
    can_entry = user.has_perm("timetabling.view_scheduleentry")
    active_queryset = None
    active_ids = None
    visible_entries = None
    if can_schedule:
        schedules = scoped_timetable(user, Schedule.objects.filter(academic_term=term))
        counts = dict(schedules.values_list("status").annotate(total=Count("pk")))
        active_queryset = department_scoped_queryset(
            user, ActiveSchedule.objects.filter(academic_term=term),
        )
        active_ids = active_queryset.values("schedule_id")
        current = list(active_queryset.select_related(
            "schedule__family", "schedule__department", "academic_term", "department",
        ).order_by("department__name", "pk")[:8])
        snapshots = {
            item.schedule_id: item for item in ScheduleApprovalSnapshot.objects.filter(
                schedule_id__in=[selection.schedule_id for selection in current],
            ).select_related("approved_by")
        }
        meeting_counts = {}
        if can_entry and current:
            meeting_counts = dict(scoped_timetable(user, ScheduleEntry.objects.filter(
                schedule_id__in=[selection.schedule_id for selection in current],
            )).values("schedule_id").annotate(total=Count("pk")).values_list("schedule_id", "total"))
        pending = list(schedules.filter(status__in=(Schedule.Status.DRAFT, Schedule.Status.VALIDATED, Schedule.Status.NEEDS_REVISION, Schedule.Status.UNDER_REVIEW)).select_related(
            "department", "family", "submitted_by",
        ).order_by("submitted_at", "pk")[:5]) if user.has_perm("timetabling.finalize_schedule") else []
        recent_approved = list(ScheduleApprovalSnapshot.objects.filter(
            schedule_id__in=schedules.filter(status=Schedule.Status.APPROVED).values("pk"),
        ).select_related("schedule__family", "approved_by").order_by(
            "-approved_at", "-pk",
        )[:3]) if user.has_perm("timetabling.finalize_schedule") else []
        issues = []
        if can_entry:
            # One bounded live check uses Phase 4's validator; a dashboard-wide
            # conflict count would require revalidating every version per view.
            recent = schedules.exclude(status=Schedule.Status.APPROVED).select_related(
                "academic_term", "department",
            ).order_by("-updated_at", "-pk").first()
            if recent:
                from timetabling.conflicts import get_schedule_conflicts

                previous = active_queryset.filter(department_id=recent.department_id).first()
                excluded = (previous.schedule_id,) if previous and previous.schedule_id != recent.pk else ()
                findings = get_schedule_conflicts(
                    recent, user=user, excluded_schedule_ids=excluded,
                )
                errors = sum(item.severity == "ERROR" for item in findings)
                warnings = sum(item.severity == "WARNING" for item in findings)
                if errors or warnings:
                    issues = [{"schedule": recent, "error_count": errors, "warning_count": warnings}]
        result["schedules"] = {
            "counts": _distribution(SCHEDULE_LABELS, counts),
            "official_count": active_queryset.count(),
            "pending_count": counts.get(Schedule.Status.UNDER_REVIEW, 0)
            if user.has_perm("timetabling.finalize_schedule") else None,
            "finalization_count": sum(counts.get(state, 0) for state in (Schedule.Status.DRAFT, Schedule.Status.VALIDATED, Schedule.Status.NEEDS_REVISION, Schedule.Status.UNDER_REVIEW)),
            "returned_count": counts.get(Schedule.Status.NEEDS_REVISION, 0),
            "recent_official": [{
                "selection": selection,
                "snapshot": snapshots.get(selection.schedule_id),
                "meeting_count": meeting_counts.get(selection.schedule_id) if can_entry else None,
            } for selection in current],
            "recent_pending": pending,
            "recent_approved": recent_approved,
            "issues": issues,
        }
        if can_entry:
            visible_entries = scoped_timetable(user, ScheduleEntry.objects.filter(
                schedule_id__in=active_ids,
            ))

    if can_schedule and can_entry and user.has_perm("scheduling.view_room"):
        visible_rooms = scope_resources(user, Room.objects.filter(is_active=True))
        booking_rows = OfficialResourceBooking.objects.filter(
            schedule_entry_id__in=visible_entries.values("pk"),
            room_id__in=visible_rooms.values("pk"),
            booking_date__gte=term.start_date, booking_date__lte=term.end_date,
        ).values("room_id", "room__code").annotate(
            duration=Sum(_duration_expression()),
        ).order_by("room__code")
        room_rows = [{
            "room_id": row["room_id"], "code": row["room__code"],
            "hours": _hours(row["duration"]),
        } for row in booking_rows]
        result["rooms"] = {
            "active_count": visible_rooms.count(),
            "used_count": len(room_rows),
            "scheduled_hours": sum((row["hours"] for row in room_rows), Decimal("0.00")),
            "rows": sorted(room_rows, key=lambda row: (-row["hours"], row["code"]))[:8],
        }

    if can_schedule and can_entry and user.has_perm("workloads.view_facultysubjectassignment"):
        assignment_queryset = scoped_records(user, FacultySubjectAssignment.objects.filter(
            subject_offering__academic_term=term,
        ))
        scheduled_assignment_ids = visible_entries.values("assignment_id")
        weekly = visible_entries.aggregate(duration=Sum(_duration_expression()))["duration"]
        result["teaching"] = {
            "scheduled_faculty_count": visible_entries.values("assignment__faculty_id").distinct().count(),
            "unscheduled_assignment_count": assignment_queryset.exclude(pk__in=scheduled_assignment_ids).count(),
            "scheduled_weekly_hours": _hours(weekly),
        }

    if user.has_perm("timetabling.view_schedulegenerationrun"):
        from timetabling.generation_inputs import generation_run_queryset

        runs = generation_run_queryset(user).filter(academic_term=term)
        counts = dict(runs.values_list("status").annotate(total=Count("pk")))
        result["generation"] = {
            "counts": _distribution(GENERATION_LABELS, counts),
            "recent": list(runs.order_by("-requested_at", "-pk")[:5]),
        }

    if user.has_perm("workloads.view_workloadrecommendationrun"):
        from workloads.balancing_service import scoped_balancing_runs

        runs = scoped_balancing_runs(user).filter(academic_term=term)
        counts = dict(runs.values_list("status").annotate(total=Count("pk")))
        result["balancing"] = {
            "counts": _distribution(BALANCING_LABELS, counts),
            "recent": list(runs.order_by("-created_at", "-pk")[:5]),
        }

    if user.has_perm("core.view_department"):
        from core.models import Department

        departments = list(department_scoped_queryset(
            user, Department.objects.filter(is_active=True, college__is_active=True), "pk",
        ).order_by("name", "pk")[:12])
        ids = [department.pk for department in departments]
        faculty_counts = {}
        if user.has_perm("faculty.view_faculty"):
            faculty_counts = dict(Faculty.objects.filter(
                home_department_id__in=ids, is_active=True,
            ).values("home_department_id").annotate(total=Count("pk")).values_list("home_department_id", "total"))
        offering_counts = {}
        if user.has_perm("workloads.view_subjectoffering"):
            offering_counts = dict(scoped_records(user, SubjectOffering.objects.filter(
                academic_term=term, department_id__in=ids,
            )).values("department_id").annotate(total=Count("pk")).values_list("department_id", "total"))
        official_counts = {}
        if can_schedule:
            official_counts = dict(active_queryset.filter(
                department_id__in=ids,
            ).values("department_id").annotate(total=Count("pk")).values_list("department_id", "total"))
        result["departments"] = [{
            "department": department,
            "active_faculty": faculty_counts.get(department.pk, 0) if user.has_perm("faculty.view_faculty") else None,
            "offerings": offering_counts.get(department.pk, 0) if user.has_perm("workloads.view_subjectoffering") else None,
            "official": official_counts.get(department.pk, 0) if can_schedule else None,
        } for department in departments]

    return result

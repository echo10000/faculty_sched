"""Staff-facing actions and findings from the existing generation checks."""
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse

from .generation_inputs import GenerationOverrides, prepare_generation_input
from .locking import scheduling_lock
from .models import ScheduleGenerationRun, SchedulingConfiguration
from .workspace import guided_url


def preparation_counts(user, schedule):
    from workloads.models import SubjectOffering, FacultySubjectAssignment
    from workloads.selectors import scoped_records
    from .models import ClassSection
    from .queries import scoped

    term, department = schedule.academic_term_id, schedule.department_id
    counts = {}
    if user.has_perm("workloads.view_subjectoffering"):
        counts["offerings"] = scoped_records(user, SubjectOffering.objects.filter(
            academic_term_id=term, department_id=department, is_active=True)).count()
    if user.has_perm("timetabling.view_classsection"):
        counts["sections"] = scoped(user, ClassSection.objects.filter(
            academic_term_id=term, department_id=department, is_active=True)).count()
    if user.has_perm("workloads.view_facultysubjectassignment"):
        counts["assignments"] = scoped_records(user, FacultySubjectAssignment.objects.filter(
            subject_offering__academic_term_id=term, subject_offering__department_id=department,
            subject_offering__is_active=True)).count()
    return counts


TASKS = {
    2: (("workloads:offerings", "Review classes offered", "workloads.view_subjectoffering"),
        ("timetabling:sections", "Review sections", "timetabling.view_classsection"),
        ("workloads:section-assignments", "Assign instructors by section", "workloads.view_facultysubjectassignment"),
        ("workloads:assignments", "Manage teaching assignments", "workloads.view_facultysubjectassignment")),
    3: (("workloads:monitor", "Review faculty teaching units", "workloads.view_workload"),
        ("timetabling:requirements", "Class scheduling requirements", "timetabling.view_offeringrequirement")),
    4: (("timetabling:meeting-requirements", "Review meeting requirements", "timetabling.view_assignmentmeetingrequirement"),
        ("workloads:availability", "Faculty availability", "workloads.view_facultyavailability"),
        ("rooms:list", "Review rooms", "scheduling.view_room"),
        ("timetabling:closures", "Blocked room times", "timetabling.view_roomunavailability"),
        ("timetabling:configurations", "Generation settings", "timetabling.view_schedulingconfiguration")),
    6: (("timetabling:generation-runs", "Review generated proposals", "timetabling.view_schedulegenerationrun"),),
}


def task_actions(user, term, department, schedule, stage):
    actions = []
    query = {"academic_term": term.pk} if term else {}
    if department:
        query["department"] = department.pk
    if schedule:
        query["schedule"] = schedule.pk
    guide = {"schedule": schedule, "stage": stage} if schedule else None
    for route, label, permission in TASKS.get(stage, ()):
        if not user.has_perm(permission):
            continue
        if route == "workloads:section-assignments" and not all(user.has_perm(item) for item in (
                "timetabling.view_classsection", "timetabling.view_offeringrequirement",
                "workloads.view_subjectoffering", "academics.view_subject", "workloads.view_workload")):
            continue
        task_query = {key: value for key, value in query.items() if route != "rooms:list" or key != "department"}
        url = reverse(route) + ("?" + urlencode(task_query) if task_query else "")
        actions.append({"label": label, "url": guided_url(url, guide)})
    return actions


FIXES = {
    "CONFIGURATION_MISSING": (4, "timetabling:configurations", "Generation settings", "timetabling.view_schedulingconfiguration"),
    "CONFIGURATION_INVALID": (4, "timetabling:configurations", "Generation settings", "timetabling.view_schedulingconfiguration"),
    "SECTION_REQUIRED": (3, "timetabling:requirements", "Link classes to sections", "timetabling.view_offeringrequirement"),
    "SECTION_EMPTY": (2, "workloads:offerings", "Review classes offered", "workloads.view_subjectoffering"),
    "ASSIGNMENT_REQUIRED": (2, "workloads:assignments", "Assign instructors", "workloads.view_facultysubjectassignment"),
    "ASSIGNMENT_SHARES_INCOMPLETE": (2, "workloads:assignments", "Complete teaching shares", "workloads.view_facultysubjectassignment"),
    "ASSIGNMENT_SHARES_EXCESS": (2, "workloads:assignments", "Review teaching shares", "workloads.view_facultysubjectassignment"),
    "NO_ELIGIBLE_ROOM": (4, "rooms:list", "Review rooms and capacity", "scheduling.view_room"),
    "FACULTY_UNAVAILABLE": (4, "workloads:availability", "Review faculty availability", "workloads.view_facultyavailability"),
    "ROOM_UNAVAILABLE": (4, "timetabling:closures", "Review blocked room times", "timetabling.view_roomunavailability"),
    "ZERO_CANDIDATES": (4, "timetabling:configurations", "Review days, times and meeting needs", "timetabling.view_schedulingconfiguration"),
}


def readiness_findings(user, schedule):
    """Read-only check: no solver execution, proposal creation, or status update."""
    try:
        with transaction.atomic():
            scheduling_lock()
            prepared = prepare_generation_input(user=user, schedule_id=schedule.pk,
                strategy=ScheduleGenerationRun.Strategy.FILL_GAPS, overrides=GenerationOverrides())
    except PermissionDenied:
        return None
    findings = []
    seen = set()
    for issue in prepared.issues:
        marker = (issue.code, issue.message)
        if marker in seen:
            continue
        seen.add(marker)
        fix = FIXES.get(issue.code)
        if issue.code.startswith("MEETING_REQUIREMENT"):
            fix = (4, "timetabling:meeting-requirements", "Review meeting requirements", "timetabling.view_assignmentmeetingrequirement")
        if fix is None:
            fix = (6, "timetabling:conflicts", "Review conflicts and requirements", "timetabling.view_scheduleentry")
        stage, route, label, permission = fix
        url = None
        if user.has_perm(permission):
            if issue.code == "CONFIGURATION_MISSING" and user.has_perm("timetabling.add_schedulingconfiguration"):
                route, label = "timetabling:configurations-add", "Add generation settings"
            elif issue.code == "CONFIGURATION_INVALID" and user.has_perm("timetabling.change_schedulingconfiguration"):
                configuration = SchedulingConfiguration.objects.filter(academic_term=schedule.academic_term,
                    department=schedule.department).first()
                if configuration:
                    route = "timetabling:configurations-edit"
            args = [schedule.pk] if route == "timetabling:conflicts" else []
            if route == "timetabling:configurations-edit":
                args = [configuration.pk]
            query = {"academic_term": schedule.academic_term_id, "department": schedule.department_id}
            if route == "rooms:list":
                query.pop("department")
            url = guided_url(reverse(route, args=args) + "?" + urlencode(query), {"schedule": schedule, "stage": stage})
        findings.append({"code": issue.code, "severity": issue.severity, "message": issue.message,
                         "url": url, "label": label, "stage": stage,
                         "targets": missing_input_targets(user, schedule, issue.code, stage)})
    return findings


def missing_input_targets(user, schedule, code, stage):
    """Identify records for missing-input findings already emitted by the backend."""
    from workloads.models import SubjectOffering, FacultySubjectAssignment
    from workloads.selectors import scoped_records

    targets = []
    guide = {"schedule": schedule, "stage": stage}
    query = {"academic_term": schedule.academic_term_id, "department": schedule.department_id}
    if code in ("SECTION_REQUIRED", "ASSIGNMENT_REQUIRED"):
        offerings = scoped_records(user, SubjectOffering.objects.filter(academic_term=schedule.academic_term,
            department=schedule.department, is_active=True)).select_related("subject")
        if code == "SECTION_REQUIRED":
            offerings = offerings.filter(scheduling_requirement__isnull=True)
            route, permission = "timetabling:requirements-add", "timetabling.add_offeringrequirement"
        else:
            offerings = offerings.filter(teaching_assignments__isnull=True)
            route, permission = "workloads:assignments-add", "workloads.add_facultysubjectassignment"
        for offering in offerings:
            url = reverse(route) + "?" + urlencode({**query, "subject_offering": offering.pk}) if user.has_perm(permission) else None
            targets.append({"label": f"{offering.subject.title} · {offering.code}", "url": guided_url(url, guide)})
    elif code == "MEETING_REQUIREMENT_MISSING":
        assignments = scoped_records(user, FacultySubjectAssignment.objects.filter(
            subject_offering__academic_term=schedule.academic_term,
            subject_offering__department=schedule.department, subject_offering__is_active=True, share__gt=0)
        ).select_related("faculty", "subject_offering__subject")
        for component in ("lecture", "laboratory"):
            missing = assignments.filter(**{f"subject_offering__{component}_hours__gt": 0}).exclude(
                meeting_requirements__meeting_type=component)
            for assignment in missing:
                url = reverse("timetabling:meeting-requirements-add") + "?" + urlencode({**query,
                    "assignment": assignment.pk, "meeting_type": component}) if user.has_perm("timetabling.add_assignmentmeetingrequirement") else None
                targets.append({"label": f"{assignment.subject_offering.subject.title} · {assignment.faculty} · {component}",
                                "url": guided_url(url, guide)})
    return targets

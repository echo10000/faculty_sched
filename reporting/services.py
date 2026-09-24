"""Scoped report datasets shared by HTML, print, PDF, XLSX, and CSV."""

from decimal import Decimal
from datetime import time

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from academics.models import AcademicTerm, Subject
from accounts.permissions import department_scoped_queryset, require_access, scoped_colleges
from core.models import College, Department, Program
from faculty.models import Faculty
from resources.selectors import scope_resources
from scheduling.models import Room
from timetabling.conflicts import get_schedule_conflicts
from timetabling.intervals import DAYS
from timetabling.models import (
    ActiveSchedule, ClassSection, Schedule, ScheduleApprovalSnapshot,
    ScheduleEntry, ScheduleGenerationRun, ScheduleWorkflowEvent,
)
from timetabling.queries import entry_queryset, scoped as timetable_scope
from workloads.balancing_service import scoped_balancing_runs
from workloads.calculation import STATUSES, calculate_workloads
from workloads.models import FacultySubjectAssignment
from workloads.selectors import accessible_terms, scoped_faculty, scoped_records


CATALOG = (
    ("workload", "Faculty workload", "Current assignment and policy based loads", ("workloads.view_workload", "workloads.view_facultysubjectassignment", "faculty.view_faculty")),
    ("faculty-schedule", "Faculty teaching schedule", "Official, working, or approved historical meetings", ("timetabling.view_schedule", "timetabling.view_scheduleentry", "faculty.view_faculty", "scheduling.view_room", "timetabling.view_classsection", "academics.view_subject")),
    ("section-schedule", "Section schedule", "Meetings for one class section", ("timetabling.view_schedule", "timetabling.view_scheduleentry", "faculty.view_faculty", "scheduling.view_room", "timetabling.view_classsection", "academics.view_subject")),
    ("room-schedule", "Room schedule", "Meetings for one room", ("timetabling.view_schedule", "timetabling.view_scheduleentry", "faculty.view_faculty", "scheduling.view_room", "timetabling.view_classsection", "academics.view_subject")),
    ("master", "Master academic schedule", "Scoped timetable across the selected term", ("timetabling.view_schedule", "timetabling.view_scheduleentry", "faculty.view_faculty", "scheduling.view_room", "timetabling.view_classsection", "academics.view_subject")),
    ("official", "Current official schedule", "Versions selected as official now", ("timetabling.view_schedule", "timetabling.view_scheduleentry", "faculty.view_faculty", "scheduling.view_room", "timetabling.view_classsection", "academics.view_subject")),
    ("historical", "Approved historical version", "Immutable approval snapshot for one version", ("timetabling.view_schedule", "timetabling.view_scheduleentry", "faculty.view_faculty", "scheduling.view_room", "timetabling.view_classsection", "academics.view_subject")),
    ("approvals", "Approval history", "Immutable review and decision events", ("timetabling.view_schedule",)),
    ("generation", "Timetable generation history", "Recorded Phase 5 runs", ("timetabling.view_schedulegenerationrun",)),
    ("balancing", "Workload recommendation history", "Recorded Phase 6 runs", ("workloads.view_workloadrecommendationrun",)),
    ("conflicts", "Conflict and validation", "Live Phase 4 findings for one visible schedule", ("timetabling.view_schedule", "timetabling.view_scheduleentry")),
)
SCHEDULE_KINDS = {"faculty-schedule", "section-schedule", "room-schedule", "master", "official", "historical"}


def available_catalog(user):
    if not user.is_authenticated:
        return []
    granted = user.get_all_permissions()
    return [
        {"key": key, "title": title, "description": description}
        for key, title, description, permissions in CATALOG
        if "academics.view_academicterm" in granted and all(p in granted for p in permissions)
    ]


def authorize(user, kind):
    definition = next((item for item in CATALOG if item[0] == kind), None)
    if definition is None:
        raise Http404("Unknown report.")
    require_access(user, "academics.view_academicterm")
    for permission in definition[3]:
        require_access(user, permission)
    return definition


def _selected(user, params, name, queryset, label, *, required=False):
    options = [{"value": "", "label": "All permitted"}] + [
        {"value": str(obj.pk), "label": str(obj)} for obj in queryset
    ]
    raw = params.get(name, "")
    if raw:
        try:
            obj = get_object_or_404(queryset, pk=int(raw))
        except (TypeError, ValueError):
            raise Http404("Invalid report filter.") from None
    elif required:
        obj = None
    else:
        obj = None
    return obj, {"name": name, "label": label, "options": options, "selected": str(obj.pk) if obj else ""}


def _filter_context(user, kind, params):
    term_qs = accessible_terms(user).order_by("-start_date", "-pk")
    term, term_filter = _selected(user, params, "term", term_qs, "Academic term")
    if not params.get("term"):
        term = term_qs.filter(is_active=True).first() or term_qs.first()
        term_filter["selected"] = str(term.pk) if term else ""
    filters = [term_filter]
    college = department = faculty = room = section = subject = program = schedule = None
    status = ""
    if kind in {"workload", "master", "official", "approvals", "generation", "balancing"}:
        if user.has_perm("core.view_college"):
            college, item = _selected(user, params, "college", scoped_colleges(user, College.objects.all()), "College")
            filters.append(item)
        elif params.get("college"):
            raise PermissionDenied("College filter access is required.")
        if user.has_perm("core.view_department"):
            departments = department_scoped_queryset(user, Department.objects.all(), "pk")
            if college:
                departments = departments.filter(college=college)
            department, item = _selected(user, params, "department", departments, "Department")
            filters.append(item)
        elif params.get("department"):
            raise PermissionDenied("Department filter access is required.")
    if kind in {"workload", "faculty-schedule", "master"}:
        people = scoped_faculty(user)
        if department:
            people = people.filter(home_department=department)
        if college:
            people = people.filter(home_department__college=college)
        faculty, item = _selected(user, params, "faculty", people, "Faculty")
        filters.append(item)
    if kind == "workload":
        status = params.get("status", "")
        if status and status not in STATUSES:
            raise Http404("Invalid workload status.")
        filters.append({"name": "status", "label": "Workload status", "selected": status,
                        "options": [{"value": "", "label": "All statuses"}] + [
                            {"value": item, "label": item.replace("_", " ").title()} for item in STATUSES
                        ]})
    if kind in {"room-schedule", "master"}:
        room, item = _selected(user, params, "room", scope_resources(user, Room.objects.all()), "Room")
        filters.append(item)
    if kind in {"section-schedule", "master"}:
        sections = timetable_scope(user, ClassSection.objects.filter(academic_term=term)) if term else ClassSection.objects.none()
        section, item = _selected(user, params, "section", sections, "Section")
        filters.append(item)
    if kind == "master":
        subject, item = _selected(user, params, "subject", scope_resources(user, Subject.objects.all()), "Subject")
        filters.append(item)
        programs = department_scoped_queryset(user, Program.objects.all(), "department")
        if department:
            programs = programs.filter(department=department)
        program, item = _selected(user, params, "program", programs, "Program")
        filters.append(item)
    if kind in SCHEDULE_KINDS | {"approvals", "conflicts"}:
        schedules = timetable_scope(user, Schedule.objects.filter(academic_term=term)) if term else Schedule.objects.none()
        if department:
            schedules = schedules.filter(department=department)
        schedule, item = _selected(user, params, "schedule", schedules, "Schedule version")
        if kind in {"historical", "approvals", "conflicts", "master", "faculty-schedule", "section-schedule", "room-schedule"}:
            filters.append(item)
    return {"term": term, "college": college, "department": department, "faculty": faculty,
            "room": room, "section": section, "subject": subject, "program": program,
            "schedule": schedule, "status": status, "filters": filters}


def _base(title, source, term, user, institution, scope, *, orientation="landscape"):
    stamp = timezone.localtime(timezone.now())
    return {
        "title": title, "source_label": source, "orientation": orientation,
        "meta": [("Institution", institution), ("Academic term", str(term) if term else "None selected"),
                 ("Scope", scope), ("Generated at", stamp.strftime("%Y-%m-%d %H:%M %Z")),
                 ("Generated by", user.get_full_name() or user.username)],
        "headers": [], "rows": [], "empty_message": "No matching records.",
        "filename_base": title.lower().replace(" ", "-"),
    }


def _excel_time(value):
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return value


def _numeric(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return value


def _schedule_rows(user, kind, selected):
    """Return frozen rows plus their approved version context, never mutable approval labels."""
    term, schedule = selected["term"], selected["schedule"]
    if not term:
        return [], "CURRENT OFFICIAL SCHEDULE"
    if kind == "historical":
        if schedule is None or schedule.status != Schedule.Status.APPROVED:
            return [], "APPROVED HISTORICAL VERSION — select an approved version"
        selections = [(schedule, False)]
        source = "APPROVED HISTORICAL VERSION"
    elif schedule is not None and kind != "official":
        if schedule.status == Schedule.Status.APPROVED:
            active = ActiveSchedule.objects.filter(academic_term=term, department=schedule.department,
                                                   schedule=schedule).exists()
            selections = [(schedule, active)]
            source = "CURRENT OFFICIAL SCHEDULE" if active else "APPROVED HISTORICAL VERSION"
        else:
            selections = [(schedule, False)]
            source = "DRAFT / WORKING DATA — NOT OFFICIAL"
    else:
        qs = department_scoped_queryset(user, ActiveSchedule.objects.filter(academic_term=term)).select_related(
            "schedule__family", "schedule__department", "schedule__academic_term",
        )
        if selected["department"]:
            qs = qs.filter(department=selected["department"])
        if selected["college"]:
            qs = qs.filter(department__college=selected["college"])
        selections = [(obj.schedule, True) for obj in qs]
        source = "CURRENT OFFICIAL SCHEDULE"
    approved_ids = [version.pk for version, _ in selections if version.status == Schedule.Status.APPROVED]
    snapshots = {
        item.schedule_id: item for item in ScheduleApprovalSnapshot.objects.filter(
            schedule_id__in=approved_ids,
        ).select_related("approved_by")
    }
    visible_entry_ids = set(timetable_scope(
        user, ScheduleEntry.objects.filter(schedule_id__in=approved_ids),
    ).values_list("pk", flat=True)) if approved_ids else set()
    rows = []
    for version, is_active in selections:
        if version.status == Schedule.Status.APPROVED:
            snapshot = snapshots.get(version.pk)
            if snapshot is None or not isinstance(snapshot.payload, dict):
                continue
            frozen = snapshot.payload
            meta = frozen.get("schedule", {})
            for entry in frozen.get("entries", []):
                if isinstance(entry, dict) and entry.get("entry_id") in visible_entry_ids:
                    rows.append({**entry, "family_name": meta.get("family_name", ""),
                                 "version_number": meta.get("version_number", version.version_number),
                                 "department_name": meta.get("department_name", ""),
                                 "approval_date": snapshot.approved_at,
                                 "reviewer": snapshot.approved_by.get_full_name() or snapshot.approved_by.username,
                                 "is_active": is_active,
                                 "program_id": None})
        else:
            entries = timetable_scope(user, entry_queryset().filter(schedule=version)).order_by(
                "day_of_week", "start_time", "pk",
            )
            for entry in entries:
                offering = entry.assignment.subject_offering
                requirement = getattr(offering, "scheduling_requirement", None)
                section = requirement.section if requirement else None
                rows.append({"faculty_id": entry.assignment.faculty_id,
                             "faculty_name": str(entry.assignment.faculty),
                             "subject_code": offering.subject.code,
                             "subject_title": offering.subject.title,
                             "section_id": section.pk if section else None,
                             "section_code": section.code if section else "",
                             "room_id": entry.room_id, "room_code": entry.room.code,
                             "day_of_week": entry.day_of_week,
                             "start_time": entry.start_time, "end_time": entry.end_time,
                             "meeting_type": entry.meeting_type,
                             "family_name": version.family.name,
                             "version_number": version.version_number,
                             "department_name": str(version.department),
                             "approval_date": None, "reviewer": "", "is_active": False,
                             "program_id": section.program_id if section else None})
    return rows, source


def _schedule_report(report, kind, selected, user):
    rows, source = _schedule_rows(user, kind, selected)
    report["source_label"] = source
    if selected["schedule"] is not None and kind != "official":
        version = selected["schedule"]
        report["meta"].append(("Schedule version", f"{version.family.name} · v{version.version_number}"))
    if kind == "room-schedule" and selected["room"] is not None:
        room = selected["room"]
        report["meta"].append(("Current room building/type",
                               f"{room.building or 'Unspecified'} / {room.category or 'Unspecified'}"))
    for name, key in (("faculty", "faculty_id"), ("room", "room_id"),
                      ("section", "section_id")):
        obj = selected[name]
        if obj is not None:
            rows = [row for row in rows if row.get(key) == obj.pk]
    if selected["subject"]:
        rows = [row for row in rows if row.get("subject_code") == selected["subject"].code]
    if selected["program"]:
        section_ids = set(ClassSection.objects.filter(program=selected["program"],
                                                      academic_term=selected["term"]).values_list("pk", flat=True))
        rows = [row for row in rows if row.get("section_id") in section_ids]
    if kind in {"official", "historical"}:
        report["headers"] = ["Family", "Version", "Department", "Approved", "Reviewer", "Subject", "Section", "Faculty", "Room", "Day", "Start", "End", "Type"]
        report["rows"] = [[r["family_name"], r["version_number"], r["department_name"],
                           r["approval_date"], r["reviewer"], r["subject_code"], r["section_code"],
                           r["faculty_name"], r["room_code"], dict(DAYS).get(r["day_of_week"], ""),
                           _excel_time(r["start_time"]), _excel_time(r["end_time"]), r["meeting_type"]] for r in rows]
    else:
        report["headers"] = ["Department", "Subject", "Section", "Faculty", "Room", "Day", "Start", "End", "Type", "Family", "Version"]
        report["rows"] = [[r["department_name"], r["subject_code"], r["section_code"],
                           r["faculty_name"], r["room_code"], dict(DAYS).get(r["day_of_week"], ""),
                           _excel_time(r["start_time"]), _excel_time(r["end_time"]), r["meeting_type"],
                           r["family_name"], r["version_number"]] for r in rows]
    report["rows"].sort(key=lambda r: tuple(str(value) for value in r[:8]))


def build_report(user, kind, params, *, institution, scope):
    definition = authorize(user, kind)
    selected = _filter_context(user, kind, params)
    term = selected["term"]
    report = _base(definition[1], "CURRENT WORKING DATA", term, user, institution, scope)
    if term is None:
        report["empty_message"] = "No academic term is available."
        return report, selected["filters"]
    department, college = selected["department"], selected["college"]
    if kind == "workload":
        people = scoped_faculty(user).filter(is_active=True)
        if department:
            people = people.filter(home_department=department)
        if college:
            people = people.filter(home_department__college=college)
        if selected["faculty"]:
            people = people.filter(pk=selected["faculty"].pk)
        reports = calculate_workloads(people, term)
        if selected["status"]:
            reports = [row for row in reports if row["status"] == selected["status"]]
        report["meta"].extend([
            ("Faculty", len(reports)),
            ("Total teaching units", sum((row["teaching_units"] for row in reports), Decimal("0")).quantize(Decimal("0.01"))),
            ("Total weekly hours", sum((row["teaching_hours"] for row in reports), Decimal("0")).quantize(Decimal("0.01"))),
        ])
        report["headers"] = ["College", "Department", "Faculty", "Category", "Rank", "Assignments", "Teaching units", "Weekly hours", "Weighted load", "Target", "Maximum", "Status", "Utilization %", "Policy source"]
        report["rows"] = [[str(row["faculty"].home_department.college), str(row["faculty"].home_department),
                           str(row["faculty"]), str(row["faculty"].employment_category or ""),
                           str(row["faculty"].academic_rank or ""),
                           "; ".join(f"{a.subject_offering.subject.code} ({a.share})" for a in row["assignments"]),
                           row["teaching_units"].quantize(Decimal("0.01")),
                           row["teaching_hours"].quantize(Decimal("0.01")),
                           row["assigned_load"].quantize(Decimal("0.01")) if row["assigned_load"] is not None else None,
                           row["policy"]["recommended_load"], row["policy"]["maximum_load"],
                           row["status"].replace("_", " ").title(),
                           row["utilization"].quantize(Decimal("0.01")) if row["utilization"] is not None else None,
                           row["policy"]["sources"].get("recommended_load", "Not configured")]
                          for row in reports]
        report["source_label"] = "CURRENT ASSIGNMENTS AND EFFECTIVE POLICY"
    elif kind in SCHEDULE_KINDS:
        _schedule_report(report, kind, selected, user)
    elif kind == "approvals":
        versions = timetable_scope(user, Schedule.objects.filter(academic_term=term))
        if department:
            versions = versions.filter(department=department)
        if college:
            versions = versions.filter(department__college=college)
        if selected["schedule"]:
            versions = versions.filter(family_id=selected["schedule"].family_id)
        events = ScheduleWorkflowEvent.objects.filter(schedule__in=versions).select_related(
            "schedule__family", "actor", "schedule__department",
        ).order_by("created_at", "pk")
        active_ids = set(department_scoped_queryset(user, ActiveSchedule.objects.filter(
            academic_term=term)).values_list("schedule_id", flat=True))
        report["headers"] = ["Family", "Version", "Department", "Action", "Actor", "When", "Remarks", "Current official"]
        report["rows"] = [[event.schedule.family.name, event.schedule.version_number,
                           str(event.schedule.department), event.get_action_display(),
                           event.actor.get_full_name() or event.actor.username, event.created_at,
                           event.remarks, "Yes" if event.schedule_id in active_ids else "No"]
                          for event in events]
        report["source_label"] = "IMMUTABLE WORKFLOW HISTORY"
    elif kind == "generation":
        runs = timetable_scope(user, ScheduleGenerationRun.objects.filter(academic_term=term)).select_related(
            "schedule__family", "requested_by", "department",
        )
        if department:
            runs = runs.filter(department=department)
        if college:
            runs = runs.filter(department__college=college)
        report["headers"] = ["Schedule", "Version", "Department", "Requested by", "Requested at", "Strategy", "Solver", "Runtime seconds", "Status", "Accepted meetings"]
        report["rows"] = [[run.schedule.family.name, run.schedule.version_number,
                           str(run.department), run.requested_by.get_full_name() or run.requested_by.username,
                           run.requested_at, run.get_strategy_display(), run.solver_status or "—",
                           run.runtime_seconds, run.get_status_display(), run.accepted_meeting_count]
                          for run in runs.order_by("-requested_at", "-pk")]
        report["source_label"] = "RECORDED GENERATION HISTORY — NO SOLVER RUN"
    elif kind == "balancing":
        runs = scoped_balancing_runs(user).filter(academic_term=term)
        if department:
            runs = runs.filter(department=department)
        if college:
            runs = runs.filter(department__college=college)
        report["headers"] = ["Run", "Department", "Initiated by", "Created", "Solver", "Status", "Faculty", "Load before", "Load after", "Decision types", "Affected faculty", "Spread before", "Spread after"]
        report["rows"] = []
        for run in runs.order_by("-created_at", "-pk"):
            summary = run.comparison.get("summary", {}) if isinstance(run.comparison, dict) else {}
            decisions = run.proposed_assignments if isinstance(run.proposed_assignments, list) else []
            faculty_rows = run.comparison.get("faculty", []) if isinstance(run.comparison, dict) else []
            if not isinstance(faculty_rows, list) or not faculty_rows:
                faculty_rows = [{}]
            for faculty_row in faculty_rows:
                before = faculty_row.get("before", {}) if isinstance(faculty_row, dict) else {}
                after = faculty_row.get("after", {}) if isinstance(faculty_row, dict) else {}
                report["rows"].append([f"Run #{run.pk}", str(run.department), run.initiated_by.get_full_name() or run.initiated_by.username,
                                       run.created_at, run.raw_solver_status or "—", run.get_status_display(),
                                       faculty_row.get("faculty", ""), _numeric(before.get("assigned_load")), _numeric(after.get("assigned_load")),
                                       "; ".join(sorted({str(item.get("status", "")) for item in decisions if isinstance(item, dict)})),
                                       summary.get("affected_faculty"), _numeric(summary.get("imbalance_before")),
                                       _numeric(summary.get("imbalance_after"))])
        report["source_label"] = "RECORDED RECOMMENDATION HISTORY — NO OPTIMIZER RUN"
    elif kind == "conflicts":
        schedule = selected["schedule"]
        report["headers"] = ["Severity", "Type", "Meeting", "Related meeting", "Explanation", "Remedy"]
        if schedule:
            active = ActiveSchedule.objects.filter(academic_term=term, department=schedule.department).first()
            findings = get_schedule_conflicts(
                schedule, user=user,
                excluded_schedule_ids=(active.schedule_id,) if active and active.schedule_id != schedule.pk else (),
            )
            entry_ids = {pk for item in findings for pk in (item.entry_id, item.other_entry_id) if pk}
            visible = timetable_scope(user, entry_queryset().filter(pk__in=entry_ids))
            entry_labels = {
                entry.pk: (f"{entry.assignment.subject_offering.subject.code} · "
                           f"{dict(DAYS).get(entry.day_of_week, '')} "
                           f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M}")
                for entry in visible
            }
            report["rows"] = [[item.severity, item.code, entry_labels.get(item.entry_id, ""),
                               entry_labels.get(item.other_entry_id, ""), item.message, item.remedy]
                              for item in findings]
            report["source_label"] = "LIVE PHASE 4 VALIDATION — " + ("APPROVED VERSION" if schedule.status == "approved" else "WORKING VERSION")
            report["meta"].append(("Schedule", f"{schedule.family.name} · v{schedule.version_number}"))
        else:
            report["empty_message"] = "Select a schedule version to validate."
    if selected["department"]:
        report["meta"].append(("Department filter", str(selected["department"])))
    if selected["college"]:
        report["meta"].append(("College filter", str(selected["college"])))
    for key in ("faculty", "section", "room", "subject", "program"):
        if selected[key]:
            report["meta"].append((key.title() + " filter", str(selected[key])))
    report["filename_base"] += f"-{term.code.lower()}"
    return report, selected["filters"]

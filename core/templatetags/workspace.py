"""Contextual links built from objects already validated by the current view."""
from urllib.parse import urlencode

from django import template
from django.urls import reverse

register = template.Library()

TERM_ROUTES = {
    "subjects:list",
    "workloads:section-assignments",
    "home", "faculty-management:list", "workloads:monitor", "workloads:offerings",
    "workloads:assignments", "workloads:assignments-add", "workloads:availability",
    "workloads:faculty", "workloads:faculty-availability", "workloads:balancing",
    "timetabling:prepare", "timetabling:generator", "timetabling:section-generator", "timetabling:schedules",
    "timetabling:schedules-add", "timetabling:sections", "timetabling:requirements",
    "timetabling:meeting-requirements", "timetabling:configurations", "timetabling:closures",
    "timetabling:room-eligibility",
    "faculty-management:detail",
    "timetabling:review-queue", "timetabling:official-schedules", "timetabling:my-schedules",
}
DEPARTMENT_ROUTES = {
    "subjects:list",
    "workloads:section-assignments",
    "timetabling:section-generator",
    "workloads:monitor", "workloads:offerings", "workloads:assignments",
    "workloads:availability", "workloads:balancing", "timetabling:schedules",
    "timetabling:sections", "timetabling:requirements", "timetabling:meeting-requirements",
    "timetabling:configurations", "timetabling:closures", "faculty-management:list",
    "timetabling:room-eligibility",
    "timetabling:review-queue", "timetabling:official-schedules", "timetabling:my-schedules",
}
MEETING_ROUTES = {
    "timetabling:schedules-detail", "timetabling:timetable", "timetabling:conflicts",
    "timetabling:unscheduled", "timetabling:schedule-review", "timetabling:schedule-history",
    "timetabling:entries-edit", "timetabling:entries-add",
    "timetabling:validate",
}
TEACHING_RECORD_ROUTES = {
    f"workloads:{section}-{action}"
    for section in ("assignments", "availability", "offerings")
    for action in ("detail", "edit", "delete")
}


@register.simple_tag(takes_context=True)
def workspace_url(context, route, *args, **overrides):
    guide = context.get("scheduling_workspace")
    schedule = context.get("schedule") or context.get("selected_schedule") or (guide["schedule"] if guide else None)
    term = (context.get("term") or context.get("selected_term") or context.get("workload_term")
            or getattr(schedule, "academic_term", None))
    form = context.get("filter_form")
    filters = getattr(form, "cleaned_data", {}) if form and not form.errors else {}
    term = term or filters.get("academic_term")
    department = getattr(schedule, "department", None) or filters.get("department")
    person = (context.get("selected_faculty") or context.get("faculty_context")
              or context.get("faculty") or context.get("person"))
    query = {}
    if route in TERM_ROUTES | TEACHING_RECORD_ROUTES and term:
        query["academic_term"] = term.pk
    if route in DEPARTMENT_ROUTES and department:
        query["department"] = department.pk
    if schedule and (route == "timetabling:prepare" or (
            route in ("timetabling:generator", "timetabling:section-generator") and schedule.status in ("draft", "validated", "needs_revision"))):
        query["schedule"] = schedule.pk
    if route in {"workloads:faculty", "workloads:assignments", "workloads:assignments-add", "workloads:availability", "workloads:faculty-availability", "faculty-management:detail"} | TEACHING_RECORD_ROUTES and schedule:
        query["schedule"] = schedule.pk
    if route in MEETING_ROUTES:
        query.update(context.get("meeting_filters", {}))
    if route in {"workloads:assignments", "workloads:assignments-add", "workloads:availability"} and person:
        query["faculty"] = person.pk
    if route in {"reporting:index", "reporting:detail"}:
        if term:
            query["term"] = term.pk
        if route == "reporting:detail" and schedule:
            query["schedule"] = schedule.pk
        if route == "reporting:detail" and args and args[0] == "faculty-schedule" and person:
            query["faculty"] = person.pk
    if route.startswith("faculty-portal:") and term:
        query["term"] = term.pk
    for key, value in overrides.items():
        if value is None or value == "":
            query.pop(key, None)
        else:
            query[key] = value
    url = reverse(route, args=args)
    from timetabling.workspace import guided_url
    return guided_url(url + ("?" + urlencode(query) if query else ""), guide)


@register.simple_tag(takes_context=True)
def guided_link(context, url):
    from timetabling.workspace import guided_url
    return guided_url(url, context.get("scheduling_workspace"))


@register.simple_tag(takes_context=True)
def available_report(context, kind):
    from reporting.services import available_catalog

    key = "workspace_report_catalog"
    if key not in context.render_context:
        context.render_context[key] = {item["key"] for item in available_catalog(context["request"].user)}
    return kind in context.render_context[key]

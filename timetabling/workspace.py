"""Presentation-only stages and scoped navigation for schedule preparation."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.http import Http404
from django.urls import Resolver404, resolve, reverse
from django.utils.deprecation import MiddlewareMixin

from .queries import get_schedule


STAGES = (
    ("Select schedule context", "Select an academic term, department and schedule version."),
    ("Prepare classes and assignments", "Prepare subject offerings and sections, then assign instructors to their subjects."),
    ("Review teaching units", "Review faculty teaching units and the class scheduling requirements before configuring meetings."),
    ("Configure meeting needs", "Review faculty availability, eligible rooms, blocked room times and weekly meeting patterns."),
    ("Generate timetable", "Generate a department-wide or section-based proposal using the existing timetable engine."),
    ("Review proposals and conflicts", "Review and accept a generated proposal, then resolve conflicts and unscheduled meetings."),
    ("Review & finalize", "Validate the current version, review any warnings, then explicitly finalize and publish it."),
)


def stage_number(raw, default=1):
    try:
        value = int(raw) if raw else default
    except (TypeError, ValueError):
        raise Http404("Invalid workflow stage")
    if not 1 <= value <= len(STAGES):
        raise Http404("Invalid workflow stage")
    return value


def stage_links(term, department, schedule):
    query = {}
    for name, obj in (("academic_term", term), ("department", department), ("schedule", schedule)):
        if obj:
            query[name] = obj.pk
    return [{"number": index, "label": label, "description": description,
             "url": reverse("timetabling:prepare") + "?" + urlencode({**query, "stage": index})}
            for index, (label, description) in enumerate(STAGES, 1)]


def supported_route(route):
    namespace, _, name = route.partition(":")
    if namespace == "timetabling":
        return name not in ("official-schedules", "review-queue", "my-schedules")
    if namespace == "workloads":
        return not name.startswith("balancing")
    return namespace in ("faculty-management", "rooms")


def destination_stage(route, fallback):
    namespace, _, name = route.partition(":")
    if namespace == "workloads":
        if name.startswith(("offerings", "assignments", "section-assignments")):
            return 2
        if name.startswith(("availability", "faculty-availability")):
            return 4
        if name in ("monitor", "faculty"):
            return 3
    if namespace == "rooms":
        return 4
    if namespace == "timetabling":
        if name.startswith("sections"):
            return 2
        if name.startswith("requirements"):
            return 3
        if name.startswith(("meeting-requirements", "configurations", "closures")):
            return 4
    if route in ("timetabling:generator", "timetabling:section-generator"):
        return 5
    if route.startswith("timetabling:generation-run") or route in (
            "timetabling:timetable", "timetabling:conflicts", "timetabling:unscheduled"):
        return 6
    if route in ("timetabling:schedule-review", "timetabling:validate", "timetabling:schedule-finalize"):
        return 7
    return fallback


def guided_url(url, workspace):
    """Carry validated context only onto local scheduling task destinations."""
    if not workspace or not url:
        return url
    parts = urlsplit(str(url))
    if parts.scheme or parts.netloc:
        return url
    try:
        match = resolve(parts.path)
    except Resolver404:
        return url
    if not supported_route(match.view_name) or match.view_name == "timetabling:prepare":
        return url
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    schedule = workspace["schedule"]
    # Links to another version leave this guide; that destination retains its own scope checks.
    destination_schedule = match.kwargs.get("schedule_id")
    if match.view_name.startswith("timetabling:") and match.url_name in (
            "schedules-detail", "timetable", "conflicts", "unscheduled", "schedule-review", "schedule-history"):
        destination_schedule = match.kwargs.get("pk")
    if destination_schedule and destination_schedule != schedule.pk:
        return url
    from core.templatetags.workspace import TERM_ROUTES, DEPARTMENT_ROUTES, TEACHING_RECORD_ROUTES
    if match.view_name in TERM_ROUTES | TEACHING_RECORD_ROUTES or match.namespace in ("timetabling", "workloads"):
        query.setdefault("academic_term", schedule.academic_term_id)
    if match.view_name in DEPARTMENT_ROUTES or match.namespace == "timetabling":
        query.setdefault("department", schedule.department_id)
    if match.view_name == "timetabling:section-generator":
        query.setdefault("year_level", 1)
    query.update(workflow_schedule=schedule.pk, workflow_stage=destination_stage(match.view_name, workspace["stage"]))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class SchedulingWorkspaceMiddleware(MiddlewareMixin):
    """Validate optional guide context before a task, and retain it after redirects.

    All task authorization and mutations remain in their existing views/services.
    Requests outside the guide are unaffected.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        request.scheduling_workspace = None
        if not request.GET.get("workflow_schedule") and not request.GET.get("workflow_stage"):
            return
        if not request.user.is_authenticated or not supported_route(request.resolver_match.view_name):
            return
        try:
            schedule_id = int(request.GET.get("workflow_schedule", ""))
        except (TypeError, ValueError):
            raise Http404("Invalid workflow schedule")
        schedule = get_schedule(request.user, schedule_id)
        stage = destination_stage(request.resolver_match.view_name, stage_number(request.GET.get("workflow_stage")))
        for name, expected in (("academic_term", schedule.academic_term_id), ("department", schedule.department_id),
                               ("schedule", schedule.pk)):
            if name == "department" and request.resolver_match.namespace == "rooms":
                continue  # Room ownership filters are independent of the schedule department.
            for data in (request.GET, request.POST if request.method == "POST" else {}):
                if data.get(name):
                    try:
                        valid = int(data[name]) == expected
                    except (TypeError, ValueError):
                        valid = False
                    if not valid:
                        raise Http404("The workflow context does not match this selection")
        match = request.resolver_match
        path_schedule = view_kwargs.get("schedule_id")
        if match.namespace == "timetabling" and match.url_name in (
                "schedules-detail", "timetable", "conflicts", "unscheduled", "validate",
                "schedule-review", "schedule-history", "schedule-finalize", "schedule-revise"):
            path_schedule = view_kwargs.get("pk")
        if path_schedule and path_schedule != schedule.pk:
            raise Http404("The workflow context does not match this version")
        if match.namespace == "timetabling" and match.url_name.startswith("generation-run-"):
            from .views import _visible_run
            if _visible_run(request.user, view_kwargs["pk"]).schedule_id != schedule.pk:
                raise Http404("The proposal belongs to another schedule version")
        request.scheduling_workspace = {"schedule": schedule, "stage": stage}

    def process_response(self, request, response):
        workspace = getattr(request, "scheduling_workspace", None)
        if workspace and response.status_code in (302, 303):
            response["Location"] = guided_url(response["Location"], workspace)
        return response


def guided_form_class(request, form_class):
    """Restrict existing form choices to the selected class context in the guide."""
    workspace = getattr(request, "scheduling_workspace", None)
    if not workspace:
        return form_class
    schedule = workspace["schedule"]
    filters = {
        "department": {"pk": schedule.department_id},
        "academic_term": {"pk": schedule.academic_term_id},
        "subject": {"owning_department_id": schedule.department_id},
        "program": {"department_id": schedule.department_id},
        "subject_offering": {"department_id": schedule.department_id, "academic_term_id": schedule.academic_term_id},
        "section": {"department_id": schedule.department_id, "academic_term_id": schedule.academic_term_id},
        "assignment": {"subject_offering__department_id": schedule.department_id,
                       "subject_offering__academic_term_id": schedule.academic_term_id},
        "faculty": {"home_department_id": schedule.department_id},
    }

    class GuidedForm(form_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for name, lookup in filters.items():
                field = self.fields.get(name)
                if field is not None and hasattr(field, "queryset"):
                    field.queryset = field.queryset.filter(**lookup)

    return GuidedForm


def workspace_navigation(request):
    workspace = getattr(request, "scheduling_workspace", None)
    if not workspace:
        return {}
    schedule = workspace["schedule"]
    stages = stage_links(schedule.academic_term, schedule.department, schedule)
    stage = workspace["stage"]
    return {"scheduling_workspace": workspace, "guide_stages": stages,
            "guide_current": stages[stage - 1],
            "guide_back": stages[stage - 2] if stage > 1 else None,
            "guide_continue": stages[stage] if stage < len(stages) else None}

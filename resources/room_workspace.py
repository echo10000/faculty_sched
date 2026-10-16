"""Validated navigation and presentation for the existing room resources."""
from urllib.parse import parse_qsl, urlencode

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from .forms import ResourceFilterForm


DIRECTORY_FILTERS = ("q", "status", "college", "department", "category", "building")


def context_id(request, name):
    try:
        return int(request.GET[name])
    except (ValueError, TypeError):
        raise Http404("Invalid room workspace context")


def room_navigation(request):
    from timetabling.queries import get_schedule
    from timetabling.room_eligibility import can_preview_rooms
    from workloads.models import SubjectOffering
    from workloads.selectors import accessible_terms, scoped_records

    guide = getattr(request, "scheduling_workspace", None)
    schedule = guide["schedule"] if guide else None
    term = offering = None
    if request.GET.get("schedule"):
        selected = get_schedule(request.user, context_id(request, "schedule"))
        if schedule and selected.pk != schedule.pk:
            raise Http404("Schedule context does not match")
        schedule = selected
    if request.user.has_perm("academics.view_academicterm"):
        terms = accessible_terms(request.user)
        if request.GET.get("academic_term"):
            term = get_object_or_404(terms, pk=context_id(request, "academic_term"))
        else:
            term = schedule.academic_term if schedule else terms.filter(is_active=True).first() or terms.first()
    if schedule and term and schedule.academic_term_id != term.pk:
        raise Http404("Schedule does not belong to the selected term")
    if request.GET.get("subject_offering") and can_preview_rooms(request.user):
        offering = get_object_or_404(scoped_records(request.user, SubjectOffering.objects.all()).select_related("subject", "academic_term"),
                                   pk=context_id(request, "subject_offering"))
        if (term and offering.academic_term_id != term.pk) or (schedule and offering.department_id != schedule.department_id):
            raise Http404("Class does not match the selected context")
        term = offering.academic_term

    navigation = {}
    if term:
        navigation["academic_term"] = term.pk
    if schedule:
        navigation["schedule"] = schedule.pk
    if offering:
        navigation["subject_offering"] = offering.pk
    raw = request.GET if request.resolver_match.url_name == "list" else dict(parse_qsl(request.GET.get("directory", "")))
    filters = {key: raw[key] for key in DIRECTORY_FILTERS if raw.get(key)}
    # Store only valid scoped filters, never a browser-supplied return URL.
    if filters:
        if ResourceFilterForm(filters, user=request.user, kind="rooms").is_valid():
            navigation["directory"] = urlencode(filters)
        else:
            filters = {}
    list_query = {**navigation, **filters}
    list_query.pop("directory", None)
    return {"term": term, "schedule": schedule, "room_offering": offering,
            "can_compare_rooms": can_preview_rooms(request.user), "room_navigation": navigation,
            "list_url": reverse("rooms:list") + ("?" + urlencode(list_query) if list_query else "")}


def configuration_failures(room):
    """Explain existing resource checks; this never establishes time availability."""
    from timetabling.room_rules import room_resource_failures

    failures = room_resource_failures(room)
    if not room.is_active:
        failures.append("Room is inactive")
    if (room.owner_department_id and (not room.owner_department.is_active or not room.owner_department.college.is_active)) or (room.owner_college_id and not room.owner_college.is_active):
        failures.append("Owning department or college is inactive")
    return failures

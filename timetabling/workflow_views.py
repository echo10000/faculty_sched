"""Scoped staff review and publication. Services own every state transition."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.permissions import department_scoped_queryset, require_access
from .models import (
    ActiveSchedule, Schedule, ScheduleApprovalSnapshot, ScheduleEntry,
    ScheduleWorkflowEvent,
)
from .queries import authorized, entry_queryset, get_schedule, scoped
from .workflow import (
    finalize_schedule, revise_approved_schedule,
)


def _visible_schedules(user):
    authorized(user, Schedule)
    return scoped(user, Schedule.objects.select_related(
        "academic_term", "department", "family", "submitted_by", "created_by",
    ))


def _actions(user, schedule):
    return {
        "can_finalize": (schedule.status in ('draft', 'validated', 'needs_revision', 'under_review')
                         and user.has_perm('timetabling.finalize_schedule')
                         and user.has_perm('timetabling.change_schedule')),
        "can_revise": (schedule.status == "approved"
                       and user.has_perm("timetabling.revise_schedule")
                       and user.has_perm("timetabling.change_schedule")),
    }


@login_required
def review_queue(request):
    require_access(request.user, "timetabling.finalize_schedule")
    schedules = _visible_schedules(request.user).filter(status__in=("draft", "validated", "needs_revision", "under_review")).order_by(
        "submitted_at", "pk",
    )
    page = Paginator(schedules, 20).get_page(request.GET.get("page"))
    return render(request, "timetabling/workflow_list.html", {
        "title": "Schedules to finalize", "page_obj": page, "kind": "review",
    })


@login_required
def my_submissions(request):
    require_access(request.user, "timetabling.finalize_schedule")
    schedules = _visible_schedules(request.user).filter(
        Q(submitted_by=request.user) | Q(created_by=request.user)
        | Q(approval_snapshot__approved_by=request.user),
    ).distinct().order_by("-submitted_at", "-pk")
    page = Paginator(schedules, 20).get_page(request.GET.get("page"))
    return render(request, "timetabling/workflow_list.html", {
        "title": "My Scheduling Work", "page_obj": page, "kind": "mine",
    })


@login_required
def official_schedules(request):
    require_access(request.user, "timetabling.view_schedule")
    require_access(request.user, "academics.view_academicterm")
    selections = department_scoped_queryset(
        request.user,
        ActiveSchedule.objects.select_related(
            "schedule__family", "academic_term", "department", "schedule__submitted_by",
            "schedule__approval_snapshot__approved_by",
        ),
    ).order_by("-academic_term__start_date", "department__name")
    page = Paginator(selections, 20).get_page(request.GET.get("page"))
    return render(request, "timetabling/workflow_list.html", {
        "title": "Official schedules", "page_obj": page, "kind": "official",
    })


@login_required
def schedule_history(request, pk):
    schedule = get_schedule(request.user, pk)
    versions = _visible_schedules(request.user).filter(family_id=schedule.family_id).order_by(
        "version_number", "pk",
    )
    events = ScheduleWorkflowEvent.objects.filter(schedule__in=versions).select_related(
        "schedule", "actor",
    ).order_by("-created_at", "-pk")
    active = ActiveSchedule.objects.filter(
        academic_term_id=schedule.academic_term_id,
        department_id=schedule.department_id,
    ).first()
    return render(request, "timetabling/workflow_history.html", {
        "schedule": schedule, "versions": versions, "events": events,
        "active_schedule_id": active.schedule_id if active else None,
    })


@login_required
def schedule_review(request, pk):
    schedule = get_schedule(request.user, pk)
    authorized(request.user, ScheduleEntry)
    active = ActiveSchedule.objects.filter(
        academic_term_id=schedule.academic_term_id,
        department_id=schedule.department_id,
    ).first()
    entries = scoped(request.user, entry_queryset()).filter(schedule=schedule).order_by(
        "day_of_week", "start_time", "pk",
    )
    from .finalization_validation import publication_conflicts
    conflicts = publication_conflicts(
        schedule, request.user,
        excluded_schedule_ids=(active.schedule_id,) if active and active.schedule_id != schedule.pk else (),
    )
    warning_codes = sorted({item.code for item in conflicts if item.severity == "WARNING"})
    events = ScheduleWorkflowEvent.objects.filter(schedule=schedule).select_related("actor").order_by(
        "-created_at", "-pk",
    )
    snapshot = ScheduleApprovalSnapshot.objects.filter(schedule=schedule).first()
    frozen_entries = snapshot.payload.get("entries", []) if snapshot and isinstance(snapshot.payload, dict) else []
    return render(request, "timetabling/workflow_review.html", {
        "schedule": schedule, "entries": entries, "conflicts": conflicts,
        "warning_codes": warning_codes, "events": events,
        "snapshot": snapshot, "frozen_entries": frozen_entries,
        "active_schedule_id": active.schedule_id if active else None,
        **_actions(request.user, schedule),
    })


@login_required
def transition(request, pk, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    # This lookup yields 404 before any workflow response for an outside ID.
    get_schedule(request.user, pk)
    operations = {
        "finalize": finalize_schedule,
        "revise": revise_approved_schedule,
    }
    operation = operations[action]
    kwargs = {
        "user": request.user, "schedule_id": pk,
        "revision_token": request.POST.get("revision_token", ""),
    }
    if action == 'finalize':
        kwargs['acknowledged_warnings'] = request.POST.getlist('acknowledged_warnings')
        kwargs['remarks'] = request.POST.get('remarks', '').strip()
    try:
        updated = operation(**kwargs)
    except ValidationError as error:
        return render(request, "timetabling/workflow_error.html", {
            "errors": error.messages,
            "back_url": reverse("timetabling:schedule-review", args=[pk]),
        }, status=409)
    messages.success(request, {
        "finalize": "Schedule finalized, published and selected as official.",
        "revise": "Editable schedule version created.",
    }[action])
    return redirect("timetabling:schedule-review", updated.pk)

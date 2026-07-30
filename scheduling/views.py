from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import DetailView
from django.views.generic import TemplateView
from django.db.models import Count, F

from accounts.permissions import department_scoped_queryset

from core.models import Department
from faculty.services import compute_department_load_summary

from academics.models import CurriculumSubject, Subject
from faculty.models import Faculty

from .autoscheduler import generate_schedule_suggestions
from .models import Assignment, Block, Room, Term, TimeSlot
from .services import validate_assignment


DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT")


def build_timetable_grid(assignments):
    """Build table-ready rows from the time slots actually used in assignments."""
    assignments = list(assignments.select_related("subject", "room", "time_slot"))
    slot_rows = sorted({(item.start_time, item.end_time) for item in assignments})
    by_slot = {
        (item.start_time, item.end_time, item.day_of_week): item
        for item in assignments
    }
    return [
        {
            "label": f"{start:%H:%M}–{end:%H:%M}",
            "cells": [
                {"assignment": by_slot.get((start, end, day))}
                for day in DAYS
            ],
        }
        for start, end in slot_rows
    ]


class TimetableContextMixin:
    def add_grid_context(self, context, assignments):
        context["days"] = DAYS
        context["grid_rows"] = build_timetable_grid(assignments)
        return context


class BlockTimetableView(LoginRequiredMixin, TimetableContextMixin, DetailView):
    model = Block
    template_name = "scheduling/block_timetable.html"
    context_object_name = "block"
    pk_url_kwarg = "block_id"

    def get_queryset(self):
        return department_scoped_queryset(
            self.request.user,
            Block.objects.select_related("curriculum__program__department", "term"),
            "curriculum__program__department",
        )

    def get_object(self, queryset=None):
        block = get_object_or_404(Block.objects.select_related("curriculum__program__department", "term"),
            pk=self.kwargs[self.pk_url_kwarg]
        )
        allowed = self.get_queryset().filter(pk=block.pk).exists()
        if not allowed:
            raise PermissionDenied("You cannot view a block outside your department.")
        return block

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assignments = self.object.assignments.all()
        context["title"] = f"Block timetable: {self.object}"
        return self.add_grid_context(context, assignments)


class FacultyTimetableView(LoginRequiredMixin, TimetableContextMixin, DetailView):
    model = Faculty
    template_name = "scheduling/faculty_timetable.html"
    context_object_name = "faculty"
    pk_url_kwarg = "faculty_id"

    def get_queryset(self):
        return department_scoped_queryset(
            self.request.user,
            Faculty.objects.select_related("home_department"),
            "home_department",
        )

    def get_object(self, queryset=None):
        faculty = get_object_or_404(Faculty.objects.select_related("home_department"), pk=self.kwargs[self.pk_url_kwarg])
        allowed = self.get_queryset().filter(pk=faculty.pk).exists()
        if not allowed:
            raise PermissionDenied("You cannot view faculty outside your department.")
        return faculty

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_term = Term.objects.filter(is_active=True).first()
        if active_term is None:
            raise Http404("There is no active term.")
        assignments = self.object.assignments.filter(term=active_term)
        context["title"] = f"Faculty timetable: {self.object} ({active_term})"
        context["term"] = active_term
        return self.add_grid_context(context, assignments)


class ConflictDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "scheduling/conflict_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_term = Term.objects.filter(is_active=True).first()
        context["active_term"] = active_term
        if active_term is None:
            context["capacity_conflicts"] = []
            context["load_warnings"] = []
            return context

        assignments = Assignment.objects.filter(term=active_term).select_related(
            "block__curriculum__program__department", "room", "subject"
        )
        assignments = department_scoped_queryset(
            self.request.user,
            assignments,
            "block__curriculum__program__department",
        )
        context["capacity_conflicts"] = assignments.annotate(
            block_enrolled_count=Count("block__students", distinct=True)
        ).filter(room__capacity__lt=F("block_enrolled_count"))

        departments = Department.objects.filter(
            faculty_members__in=department_scoped_queryset(
                self.request.user, Faculty.objects.all(), "home_department"
            )
        ).distinct()
        load_warnings = []
        for department in departments:
            for summary in compute_department_load_summary(department, active_term, user=self.request.user):
                if summary["status"] != "on_target":
                    load_warnings.append(summary)
        context["load_warnings"] = load_warnings
        return context


class AutoScheduleSuggestionView(LoginRequiredMixin, TemplateView):
    template_name = "scheduling/auto_schedule_suggestions.html"

    def get_term(self):
        term_id = self.request.GET.get("term")
        if term_id:
            return get_object_or_404(Term, pk=term_id)
        term = Term.objects.filter(is_active=True).first()
        if term is None:
            raise Http404("There is no active term.")
        return term

    def scoped_blocks(self, term):
        return department_scoped_queryset(
            self.request.user,
            Block.objects.filter(term=term).select_related("curriculum__program__department"),
            "curriculum__program__department",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.get_term()
        blocks = list(self.scoped_blocks(term))
        block_ids = [block.id for block in blocks]
        result = generate_schedule_suggestions(term, block_ids=block_ids)
        block_by_id = {block.id: block for block in blocks}
        subject_by_id = Subject.objects.in_bulk(
            [item["subject_id"] for item in result["proposals"] + result["unfilled"]]
        )
        rows = []
        for proposal in result["proposals"]:
            rows.append({
                "block": block_by_id[proposal["block_id"]],
                "subject": subject_by_id[proposal["subject_id"]],
                "proposal": proposal,
                "unfilled": False,
                "reason": "",
            })
        for unfilled in result["unfilled"]:
            rows.append({
                "block": block_by_id[unfilled["block_id"]],
                "subject": subject_by_id[unfilled["subject_id"]],
                "proposal": {},
                "unfilled": True,
                "reason": unfilled["reason"],
            })
        rows.sort(key=lambda row: (str(row["block"]), row["subject"].code))
        previous_block_id = None
        for index, row in enumerate(rows):
            row["index"] = index
            row["show_block_header"] = row["block"].id != previous_block_id
            previous_block_id = row["block"].id

        context.update({
            "term": term,
            "rows": rows,
            "faculties": Faculty.objects.filter(is_active=True).order_by("last_name", "first_name"),
            "rooms": Room.objects.order_by("name"),
            "time_slots": TimeSlot.objects.order_by("day_of_week", "start_time"),
        })
        return context


class CommitAutoScheduleSuggestionsView(LoginRequiredMixin, TemplateView):
    """Save independently validated suggestion rows without all-or-nothing failure."""

    def post(self, request, *args, **kwargs):
        try:
            row_count = int(request.POST.get("row_count", "0"))
            term = Term.objects.get(pk=request.POST["term_id"])
        except (KeyError, Term.DoesNotExist, ValueError):
            messages.error(request, "The submitted suggestion batch is invalid.")
            return redirect("scheduling:auto-schedule-suggestions")

        scoped_blocks = department_scoped_queryset(
            request.user,
            Block.objects.filter(term=term).select_related("curriculum__program__department"),
            "curriculum__program__department",
        )
        created_count = 0
        for index in range(min(row_count, 5000)):
            if f"accept_{index}" not in request.POST:
                continue
            try:
                block = scoped_blocks.get(pk=request.POST[f"block_{index}"])
                subject = Subject.objects.get(pk=request.POST[f"subject_{index}"])
                if not CurriculumSubject.objects.filter(
                    curriculum=block.curriculum,
                    year_level=block.year_level,
                    term=term.term_name,
                    subject=subject,
                ).exists():
                    raise ValidationError(f"{subject.code} is not in {block}'s curriculum placement.")
                faculty = Faculty.objects.get(pk=request.POST[f"faculty_{index}"])
                room = Room.objects.get(pk=request.POST[f"room_{index}"])
                time_slot = TimeSlot.objects.get(pk=request.POST[f"time_slot_{index}"])
                validate_assignment(
                    faculty, subject, block, room, term, time_slot, subject.units
                )
                Assignment.objects.create(
                    faculty=faculty,
                    subject=subject,
                    block=block,
                    room=room,
                    term=term,
                    time_slot=time_slot,
                    units_credited=subject.units,
                    created_by=request.user,
                )
                created_count += 1
            except (Block.DoesNotExist, Subject.DoesNotExist, Faculty.DoesNotExist, Room.DoesNotExist, TimeSlot.DoesNotExist, ValueError):
                messages.error(request, f"Row {index + 1}: a selected record no longer exists.")
            except ValidationError as exc:
                messages.error(request, f"Row {index + 1}: {'; '.join(exc.messages)}")
            except IntegrityError:
                messages.error(request, f"Row {index + 1}: a scheduling conflict was detected while saving.")

        if created_count:
            messages.success(request, f"Created {created_count} assignment(s).")
        return redirect(f"{reverse('scheduling:auto-schedule-suggestions')}?term={term.id}")

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView
from django.views.generic import TemplateView
from django.db.models import Count, F

from accounts.permissions import department_scoped_queryset

from core.models import Department
from faculty.services import compute_department_load_summary

from .models import Assignment, Block, Term
from faculty.models import Faculty


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

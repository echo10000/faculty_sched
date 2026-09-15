from django.db.models import Q
from django.shortcuts import get_object_or_404

from accounts.permissions import department_scoped_queryset, require_access
from resources.selectors import scope_resources
from scheduling.models import Room
from workloads.selectors import scoped_records as teaching_scope, accessible_terms
from workloads.models import FacultySubjectAssignment
from .models import Schedule, ScheduleEntry, ClassSection, OfferingRequirement, RoomUnavailability


def scoped(user, queryset):
    model = queryset.model
    if model in (Schedule, ClassSection):
        return department_scoped_queryset(user, queryset)
    if model is OfferingRequirement:
        return department_scoped_queryset(user, department_scoped_queryset(user, queryset, "subject_offering__department"), "section__department")
    if model is RoomUnavailability:
        return queryset.filter(room__in=scope_resources(user, Room.objects.all()))
    if model is ScheduleEntry:
        qs = department_scoped_queryset(user, queryset, "schedule__department")
        return qs.filter(assignment__in=teaching_scope(user, FacultySubjectAssignment.objects.all()), room__in=scope_resources(user, Room.objects.all()))
    raise ValueError("Unsupported timetable record")


def authorized(user, model, action="view"):
    require_access(user, f"timetabling.{action}_{model._meta.model_name}")
    require_access(user, "academics.view_academicterm")


def get_schedule(user, pk, action="view", lock=False):
    authorized(user, Schedule, action)
    qs = Schedule.objects.select_for_update(of=("self",)) if lock else Schedule.objects.all()
    return get_object_or_404(scoped(user, qs).select_related("academic_term__academic_year", "academic_term__semester", "department__college"), pk=pk)


def entry_queryset():
    return ScheduleEntry.objects.select_related("schedule__academic_term__academic_year", "schedule__academic_term__semester", "schedule__department__college", "assignment__faculty__home_department__college", "assignment__subject_offering__subject", "assignment__subject_offering__academic_term", "room__category", "room__building", "room__owner_department__college", "room__owner_college")

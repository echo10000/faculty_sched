from django.contrib import admin

from accounts.permissions import department_scoped_queryset
from core.admin_mixins import AuditedAdmin
from core.models import Department
from workloads.models import FacultySubjectAssignment
from workloads.selectors import accessible_terms, scoped_records

from .models import (
    AssignmentMeetingRequirement,
    ScheduleGenerationRun,
    SchedulingConfiguration,
)


class IdentityLockedAdmin(AuditedAdmin):
    identity_fields = ()

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.pk:
            fields.extend(self.identity_fields)
        return tuple(dict.fromkeys(fields))


@admin.register(AssignmentMeetingRequirement)
class AssignmentMeetingRequirementAdmin(IdentityLockedAdmin):
    identity_fields = ("assignment", "meeting_type")
    list_display = ("assignment", "meeting_type", "meetings_per_week", "duration_minutes")
    list_filter = ("meeting_type", "assignment__subject_offering__academic_term")
    search_fields = (
        "assignment__faculty__employee_id",
        "assignment__faculty__first_name",
        "assignment__faculty__last_name",
        "assignment__subject_offering__subject__code",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "assignment":
            queryset = FacultySubjectAssignment.objects.select_related(
                "faculty__home_department__college",
                "subject_offering__subject",
                "subject_offering__academic_term",
                "subject_offering__department",
            )
            kwargs["queryset"] = scoped_records(request.user, queryset)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(SchedulingConfiguration)
class SchedulingConfigurationAdmin(IdentityLockedAdmin):
    identity_fields = ("academic_term", "department")
    list_display = (
        "academic_term",
        "department",
        "earliest_start",
        "latest_end",
        "slot_increment_minutes",
        "solver_time_limit_seconds",
    )
    list_filter = ("academic_term", "department__college", "department")
    search_fields = ("academic_term__code", "department__code", "department__name")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "academic_term":
            kwargs["queryset"] = accessible_terms(request.user, active=True)
        elif db_field.name == "department":
            kwargs["queryset"] = department_scoped_queryset(
                request.user,
                Department.objects.filter(is_active=True, college__is_active=True),
                "pk",
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ScheduleGenerationRun)
class ScheduleGenerationRunAdmin(admin.ModelAdmin):
    list_display = (
        "requested_at",
        "schedule",
        "strategy",
        "status",
        "solver_status",
        "requested_by",
    )
    list_filter = ("status", "solver_status", "strategy", "academic_term", "department")
    search_fields = ("schedule__name", "requested_by__username", "source_signature")
    readonly_fields = tuple(
        field.name for field in ScheduleGenerationRun._meta.concrete_fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

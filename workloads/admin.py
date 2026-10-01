from django.contrib import admin
from core.admin_mixins import AuditedAdmin
from .models import FacultyTermCapacity, WorkloadPolicy


class CapacityAdmin(AuditedAdmin):
    readonly_fields = ("created_at", "updated_at", "created_by")
    list_filter = ("academic_term",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkloadPolicy)
class WorkloadPolicyAdmin(CapacityAdmin):
    list_display = ("academic_term", "college", "department", "recommended_load", "maximum_load", "enforce_maximum")
    search_fields = ("college__name", "department__name", "academic_term__code")


@admin.register(FacultyTermCapacity)
class FacultyTermCapacityAdmin(CapacityAdmin):
    list_display = ("faculty", "academic_term", "recommended_load", "maximum_load", "enforce_maximum")
    search_fields = ("faculty__employee_id", "faculty__last_name")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "faculty":
            from faculty.models import Faculty
            # Existing inactive records remain editable; new overrides use active faculty.
            if not request.resolver_match.kwargs.get("object_id"):
                kwargs["queryset"] = Faculty.objects.filter(is_active=True, home_department__is_active=True, home_department__college__is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

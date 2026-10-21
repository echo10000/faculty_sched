from django.contrib import admin
from django.db import transaction

from .models import College, Department, Program, SystemSetting
from .admin_mixins import AuditedAdmin


@admin.register(College)
class CollegeAdmin(AuditedAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    search_fields = ("code", "name")


@admin.register(Department)
class DepartmentAdmin(AuditedAdmin):
    list_display = ("code", "name", "college", "is_active")
    list_filter = ("college",)
    search_fields = ("code", "name", "college__code")


@admin.register(Program)
class ProgramAdmin(AuditedAdmin):
    readonly_fields = ()  # Program has no tracking columns; mutations are audited.
    list_select_related = ("department__college",)
    list_display = ("code", "name", "department", "college")
    list_filter = ("department__college", "department")
    search_fields = ("code", "name", "department__code")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            from timetabling.locking import scheduling_lock
            # Validate under the same lock as section writes, so a concurrent
            # new link produces a field error before save/audit, rather than a 500.
            with transaction.atomic():
                scheduling_lock()
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)

    @admin.display(ordering="department__college", description="College")
    def college(self, obj):
        return obj.department.college


@admin.register(SystemSetting)
class SystemSettingAdmin(AuditedAdmin):
    list_display = ("key", "value", "updated_at")

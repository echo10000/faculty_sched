from django.contrib import admin

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
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department", "college")
    list_filter = ("department__college", "department")
    search_fields = ("code", "name", "department__code")

    @admin.display(ordering="department__college", description="College")
    def college(self, obj):
        return obj.department.college


@admin.register(SystemSetting)
class SystemSettingAdmin(AuditedAdmin):
    list_display = ("key", "value", "updated_at")

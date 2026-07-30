from django.contrib import admin

from .models import College, Department, Program


@admin.register(College)
class CollegeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_at", "updated_at")
    search_fields = ("code", "name")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "college")
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

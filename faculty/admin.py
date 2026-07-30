from django.contrib import admin

from .models import Designation, Faculty, FacultyQualification


@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ("name", "units_released")


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "home_department", "employment_type", "designation", "effective_load_units")
    list_filter = ("employment_type", "is_active", "home_department")
    search_fields = ("employee_id", "first_name", "last_name")


@admin.register(FacultyQualification)
class FacultyQualificationAdmin(admin.ModelAdmin):
    list_display = ("faculty", "subject")

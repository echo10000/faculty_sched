from django.contrib import admin

from .models import Curriculum, CurriculumSubject, IrregularEnrollment, Student, Subject


class CurriculumSubjectInline(admin.TabularInline):
    model = CurriculumSubject
    extra = 1


@admin.register(Curriculum)
class CurriculumAdmin(admin.ModelAdmin):
    list_display = ("program", "version_year", "is_active")
    list_filter = ("is_active", "program__department")
    inlines = (CurriculumSubjectInline,)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "units", "is_general_education", "owning_department")
    list_filter = ("is_general_education", "owning_department")
    search_fields = ("code", "title")


@admin.register(CurriculumSubject)
class CurriculumSubjectAdmin(admin.ModelAdmin):
    list_display = ("curriculum", "subject", "year_level", "term")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_number", "last_name", "first_name", "program", "year_level", "status", "block")
    list_filter = ("status", "is_active", "program")
    search_fields = ("student_number", "first_name", "last_name")


@admin.register(IrregularEnrollment)
class IrregularEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "subject", "term")

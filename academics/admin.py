from django.contrib import admin

from .models import Curriculum, CurriculumSubject, IrregularEnrollment, Student, Subject
from .models import AcademicYear, AcademicTerm, Semester
from core.admin_mixins import AuditedAdmin
from django.forms.models import BaseInlineFormSet
from django import forms
from django.core.exceptions import ValidationError
from .models import AcademicCalendar, CalendarTermPattern


class CalendarPatternForm(forms.ModelForm):
    class Meta:
        model = CalendarTermPattern
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The formset compares all edited rows together, including new/deleted rows.
        self.instance._validated_with_calendar_formset = True


class CalendarPatternFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        ranges = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            start, end = form.instance.dates_for_year(2001, calendar=self.instance)
            if any(start <= other_end and end >= other_start for other_start, other_end in ranges):
                raise ValidationError("Term dates must not overlap.")
            ranges.append((start, end))
        if self.instance.is_enabled and not ranges:
            raise ValidationError("Add at least one term pattern before enabling automatic creation.")


class CalendarTermPatternInline(admin.TabularInline):
    model = CalendarTermPattern
    form = CalendarPatternForm
    formset = CalendarPatternFormSet
    fields = ("semester", "code", "start_month", "start_day", "end_month", "end_day")
    extra = 0


@admin.register(AcademicCalendar)
class AcademicCalendarAdmin(AuditedAdmin):
    list_display = ("name", "is_enabled", "year_start_month", "year_start_day", "last_rollover_date")
    readonly_fields = (*AuditedAdmin.readonly_fields, "last_rollover_date")
    inlines = (CalendarTermPatternInline,)
    fieldsets = ((None, {"fields": ("name", "is_enabled", "year_start_month", "year_start_day"),
        "description": "The academic year ends the day before the next year's start. Each enabled calendar creates the current and next year on the first signed-in visit each day. Staff use the term whose dates include today. Date changes apply to newly created periods; edit existing academic years and terms separately. Students and teaching assignments are not copied."}),
        ("Creation history", {"fields": readonly_fields, "classes": ("collapse",)}))


@admin.register(AcademicYear)
class AcademicYearAdmin(AuditedAdmin):
    list_display = ("label", "start_date", "end_date", "is_active")
    search_fields = ("label",)


@admin.register(Semester)
class SemesterAdmin(AuditedAdmin):
    list_display = ("code", "name", "is_active")


@admin.register(AcademicTerm)
class AcademicTermAdmin(AuditedAdmin):
    list_display = ("code", "academic_year", "semester", "start_date", "end_date", "is_active")
    list_filter = ("academic_year", "semester", "is_active")
    list_select_related = ("academic_year", "semester")


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

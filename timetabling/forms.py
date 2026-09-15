from django import forms

from accounts.permissions import department_scoped_queryset
from core.models import Department, Program
from faculty.models import Faculty
from resources.forms import StyledFormMixin
from resources.models import RoomType
from resources.selectors import available_resources, scope_resources
from scheduling.models import Room
from workloads.models import SubjectOffering, FacultySubjectAssignment
from workloads.selectors import accessible_terms, scoped_records, scoped_faculty
from .models import ClassSection, OfferingRequirement, RoomUnavailability, Schedule, ScheduleEntry
from .queries import scoped
from .intervals import DAYS


class ScopedForm(StyledFormMixin, forms.ModelForm):
    def __init__(self, *args, user, term=None, **kwargs):
        super().__init__(*args, **kwargs)
        if "department" in self.fields:
            self.fields["department"].queryset = department_scoped_queryset(user, Department.objects.filter(is_active=True, college__is_active=True), "pk")
        if "academic_term" in self.fields:
            self.fields["academic_term"].queryset = accessible_terms(user, active=True)
            if term:
                self.fields["academic_term"].queryset = self.fields["academic_term"].queryset.filter(pk=term.pk)
                self.initial["academic_term"] = term.pk
        self.style_fields()


class ScheduleForm(ScopedForm):
    class Meta:
        model = Schedule
        fields = ["academic_term", "department", "name"]


class SectionForm(ScopedForm):
    class Meta:
        model = ClassSection
        fields = ["academic_term", "department", "program", "code", "year_level", "expected_size", "is_active"]

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["program"].queryset = department_scoped_queryset(user, Program.objects.all())


class RequirementForm(ScopedForm):
    class Meta:
        model = OfferingRequirement
        fields = ["subject_offering", "section", "room_type", "room_type_mandatory", "capacity_is_hard"]

    def __init__(self, *args, user, term=None, **kwargs):
        super().__init__(*args, user=user, term=term, **kwargs)
        self.fields["subject_offering"].queryset = scoped_records(user, SubjectOffering.objects.filter(is_active=True, subject__is_active=True, department__is_active=True, department__college__is_active=True, academic_term__is_active=True, academic_term__academic_year__is_active=True, academic_term__semester__is_active=True)).select_related("subject", "academic_term")
        self.fields["section"].queryset = scoped(user, ClassSection.objects.filter(is_active=True))
        if term:
            self.fields["subject_offering"].queryset = self.fields["subject_offering"].queryset.filter(academic_term=term)
            self.fields["section"].queryset = self.fields["section"].queryset.filter(academic_term=term)
        self.fields["room_type"].queryset = RoomType.objects.filter(is_active=True)


class ClosureForm(ScopedForm):
    class Meta:
        model = RoomUnavailability
        fields = ["academic_term", "room", "day_of_week", "start_time", "end_time", "notes"]
        widgets = {name: forms.TimeInput(format="%H:%M", attrs={"type": "time"}) for name in ("start_time", "end_time")}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["room"].queryset = available_resources(user, Room)


class EntryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ScheduleEntry
        fields = ["assignment", "room", "day_of_week", "start_time", "end_time", "meeting_type", "notes"]
        widgets = {name: forms.TimeInput(format="%H:%M", attrs={"type": "time"}) for name in ("start_time", "end_time")}

    def __init__(self, *args, user, schedule, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.schedule = schedule
        self.fields["assignment"].queryset = scoped_records(user, FacultySubjectAssignment.objects.filter(subject_offering__academic_term=schedule.academic_term,
            subject_offering__department=schedule.department, subject_offering__is_active=True, subject_offering__subject__is_active=True,
            faculty__is_active=True, faculty__home_department__is_active=True, faculty__home_department__college__is_active=True)).select_related("faculty", "subject_offering__subject", "subject_offering__academic_term")
        self.fields["room"].queryset = available_resources(user, Room)
        self.style_fields()


class TimetableFilter(StyledFormMixin, forms.Form):
    academic_term = forms.ModelChoiceField(queryset=None, required=False)
    department = forms.ModelChoiceField(queryset=None, required=False)
    faculty = forms.ModelChoiceField(queryset=None, required=False)
    room = forms.ModelChoiceField(queryset=None, required=False)
    section = forms.ModelChoiceField(queryset=None, required=False)
    day_of_week = forms.TypedChoiceField(required=False, coerce=int, empty_value=None, choices=[("", "All days"), *DAYS])
    q = forms.CharField(required=False, label="Search")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_term"].queryset = accessible_terms(user)
        self.fields["department"].queryset = department_scoped_queryset(user, Department.objects.all(), "pk")
        self.fields["faculty"].queryset = scoped_faculty(user)
        self.fields["room"].queryset = scope_resources(user, Room.objects.all())
        self.fields["section"].queryset = scoped(user, ClassSection.objects.all())
        self.style_fields()

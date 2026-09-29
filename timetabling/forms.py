from copy import copy

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.permissions import department_scoped_queryset
from core.models import Department, Program
from faculty.models import Faculty
from resources.forms import StyledFormMixin
from resources.models import RoomType
from resources.selectors import available_resources, scope_resources
from scheduling.models import Room
from workloads.models import SubjectOffering, FacultySubjectAssignment
from workloads.selectors import accessible_terms, scoped_records, scoped_faculty
from .models import (AssignmentMeetingRequirement, ClassSection, OfferingRequirement,
                     RoomUnavailability, Schedule, ScheduleEntry, ScheduleGenerationRun,
                     SchedulingConfiguration)
from .queries import get_schedule, scoped
from .generation_inputs import GenerationOverrides, require_generation_access
from .intervals import DAYS


class ScopedForm(StyledFormMixin, forms.ModelForm):
    def __init__(self, *args, user, term=None, **kwargs):
        super().__init__(*args, **kwargs)
        if "department" in self.fields:
            self.fields["department"].queryset = department_scoped_queryset(user, Department.objects.filter(is_active=True, college__is_active=True), "pk")
            self.fields["department"].label_from_instance = lambda department: f"{department.name} ({department.code})"
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


class MeetingRequirementForm(ScopedForm):
    class Meta:
        model = AssignmentMeetingRequirement
        fields = ["assignment", "meeting_type", "meetings_per_week", "duration_minutes"]

    def __init__(self, *args, user, term=None, **kwargs):
        super().__init__(*args, user=user, term=term, **kwargs)
        choices = scoped_records(user, FacultySubjectAssignment.objects.filter(
            subject_offering__is_active=True, subject_offering__subject__is_active=True,
            subject_offering__department__is_active=True,
            subject_offering__academic_term__is_active=True,
            faculty__is_active=True, faculty__home_department__is_active=True,
        )).select_related("faculty", "subject_offering__subject", "subject_offering__academic_term")
        if term:
            choices = choices.filter(subject_offering__academic_term=term)
        if self.instance.pk:
            choices = scoped_records(user, FacultySubjectAssignment.objects.filter(pk=self.instance.assignment_id))
            self.fields["assignment"].disabled = True
            self.fields["meeting_type"].disabled = True
        self.fields["assignment"].queryset = choices


class SchedulingConfigurationForm(ScopedForm):
    allowed_weekdays = forms.TypedMultipleChoiceField(
        choices=DAYS, coerce=int, widget=forms.CheckboxSelectMultiple,
        label="Allowed weekdays", help_text="Choose the days on which meetings may be placed.",
    )

    class Meta:
        model = SchedulingConfiguration
        fields = ["academic_term", "department", "allowed_weekdays", "earliest_start",
                  "latest_end", "slot_increment_minutes", "solver_time_limit_seconds",
                  "random_seed", "worker_count", *SchedulingConfiguration.WEIGHT_FIELDS]
        widgets = {name: forms.TimeInput(format="%H:%M", attrs={"type": "time"})
                   for name in ("earliest_start", "latest_end")}

    def __init__(self, *args, user, term=None, **kwargs):
        super().__init__(*args, user=user, term=term, **kwargs)
        if self.instance.pk:
            self.fields["academic_term"].disabled = True
            self.fields["department"].disabled = True
        self.fields["allowed_weekdays"].widget.attrs.pop("class", None)


class GenerationRequestForm(StyledFormMixin, forms.Form):
    academic_term = forms.ModelChoiceField(queryset=None, required=False, label="Academic term")
    schedule = forms.ModelChoiceField(queryset=None)
    strategy = forms.ChoiceField(choices=ScheduleGenerationRun.Strategy.choices)
    solver_time_limit_seconds = forms.IntegerField(required=False, min_value=1, max_value=300)
    faculty_preference_weight = forms.IntegerField(required=False, min_value=0)
    faculty_gap_weight = forms.IntegerField(required=False, min_value=0)
    section_gap_weight = forms.IntegerField(required=False, min_value=0)
    meeting_distribution_weight = forms.IntegerField(required=False, min_value=0)
    room_fit_weight = forms.IntegerField(required=False, min_value=0)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        terms = accessible_terms(user, active=True)
        self.fields["academic_term"].queryset = terms
        schedules = scoped(user, Schedule.objects.filter(
            academic_term__is_active=True, department__is_active=True,
            status__in=(Schedule.Status.DRAFT, Schedule.Status.VALIDATED, Schedule.Status.NEEDS_REVISION),
        )).select_related("academic_term", "department")
        raw = (self.data if self.is_bound else self.initial).get("academic_term")
        if raw:
            try:
                term = terms.get(pk=int(raw))
            except (TypeError, ValueError, terms.model.DoesNotExist):
                schedules = schedules.none()
            else:
                schedules = schedules.filter(academic_term=term)
        self.fields["schedule"].queryset = schedules
        self.style_fields()

    def clean(self):
        data = super().clean()
        schedule = data.get("schedule")
        if schedule is None:
            return data
        schedule = get_schedule(self.user, schedule.pk, action="change")
        term = data.get("academic_term")
        if term and term.pk != schedule.academic_term_id:
            self.add_error("schedule", "Schedule does not belong to the selected academic term.")
        strategy = data.get("strategy")
        if strategy == ScheduleGenerationRun.Strategy.REPLACE_UNLOCKED:
            try:
                require_generation_access(self.user, strategy)
            except PermissionDenied:
                self.add_error("strategy", "Replacing unlocked meetings requires delete meeting permission.")
        configuration = scoped(self.user, SchedulingConfiguration.objects.filter(
            academic_term_id=schedule.academic_term_id,
            department_id=schedule.department_id,
        )).first()
        if configuration is None:
            self.add_error("schedule", "This schedule needs a scheduling configuration.")
        overrides = GenerationOverrides(**{
            name: data.get(name) for name in GenerationOverrides.__dataclass_fields__
        })
        if configuration:
            effective = copy(configuration)
            for name in GenerationOverrides.__dataclass_fields__:
                value = getattr(overrides, name)
                if value is not None:
                    setattr(effective, name, value)
            try:
                effective.full_clean(validate_unique=False, validate_constraints=False)
            except ValidationError:
                self.add_error(None, "The effective scheduling configuration is invalid.")
        data["overrides"] = overrides
        return data


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
        self.fields["department"].label_from_instance = lambda department: f"{department.name} ({department.code})"
        self.fields["faculty"].queryset = scoped_faculty(user)
        self.fields["room"].queryset = scope_resources(user, Room.objects.all())
        self.fields["section"].queryset = scoped(user, ClassSection.objects.all())
        self.style_fields()

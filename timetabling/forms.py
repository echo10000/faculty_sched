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
from academics.calendar import use_current_term_field
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
        use_current_term_field(self, user)
        self.style_fields()


class ScheduleForm(ScopedForm):
    class Meta:
        model = Schedule
        fields = ["academic_term", "department", "name"]


class SectionGenerationForm(StyledFormMixin, forms.Form):
    academic_term = forms.ModelChoiceField(queryset=None, label="Academic term")
    department = forms.ModelChoiceField(queryset=None, label="Department / program")
    year_level = forms.TypedChoiceField(
        choices=[(year, str(year)) for year in range(1, 5)], coerce=int, label="Year level")
    section = forms.ModelChoiceField(queryset=None, required=False, label="Section")
    schedule = forms.ModelChoiceField(queryset=None, required=False, label="Draft schedule")
    strategy = forms.ChoiceField(choices=ScheduleGenerationRun.Strategy.choices,
                                initial=ScheduleGenerationRun.Strategy.FILL_GAPS,
                                label="Generation mode")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_term"].queryset = accessible_terms(user, active=True)
        self.fields["department"].queryset = department_scoped_queryset(
            user, Department.objects.filter(is_active=True, college__is_active=True).select_related("college"), "pk")
        self.fields["department"].label_from_instance = lambda obj: f"{obj.college.name} · {obj.name}"
        sections = scoped(user, ClassSection.objects.filter(is_active=True,
            academic_term__is_active=True, department__is_active=True,
            department__college__is_active=True)).select_related("program", "department__college",
                "academic_term__academic_year", "academic_term__semester")
        schedules = scoped(user, Schedule.objects.filter(status__in=("draft", "validated", "needs_revision"))
            .select_related("academic_term", "department"))
        values = self.data if self.is_bound else self.initial
        for name in ("academic_term", "department", "year_level"):
            raw = values.get(name)
            if raw:
                try:
                    value = int(raw)
                except (ValueError, TypeError):
                    sections, schedules = sections.none(), schedules.none()
                    break
                sections = sections.filter(**{name: value})
                if name != "year_level":
                    schedules = schedules.filter(**{name: value})
        self.fields["section"].queryset = sections
        self.fields["section"].label_from_instance = lambda obj: obj.code
        self.fields["schedule"].queryset = schedules.order_by("-updated_at", "-pk")
        self.fields["schedule"].label_from_instance = lambda obj: f"{obj.name} · v{obj.version_number}"
        use_current_term_field(self, user)
        self.style_fields()

    def clean(self):
        data = super().clean()
        section, schedule = data.get("section"), data.get("schedule")
        for obj, field in ((section, "section"), (schedule, "schedule")):
            if obj and (obj.academic_term != data.get("academic_term") or
                        obj.department != data.get("department")):
                self.add_error(field, "Choose a record for the selected term and department.")
        if section and section.year_level != data.get("year_level"):
            self.add_error("section", "Choose a section for the selected year level.")
        return data


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
        labels = {"room_type": "Room specialization / type (optional)",
                  "room_type_mandatory": "Require this specialization (mandatory)",
                  "capacity_is_hard": "Require enough seats (mandatory capacity)"}
        help_texts = {
            "room_type": "Applies to both lecture and laboratory meetings. Blank inherits the subject catalog's mandatory type, if configured; otherwise any type is allowed.",
            "room_type_mandatory": "Checked: other types are excluded. Unchecked with a type selected: preferred, and other rooms remain eligible. Room-fit weight controls its influence on generation.",
            "capacity_is_hard": "Checked: rooms must seat the section's expected student count. Unchecked: too few seats produces a warning. A missing class size cannot be checked.",
            "section": "The section supplies the expected class size for capacity checks.",
        }

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
                  "latest_end", "slot_increment_minutes", "lunch_break_enabled", "lunch_start", "lunch_end",
                  "faculty_rest_minutes", "solver_time_limit_seconds",
                  "random_seed", "worker_count", *SchedulingConfiguration.WEIGHT_FIELDS]
        widgets = {name: forms.TimeInput(format="%H:%M", attrs={"type": "time"})
                   for name in ("earliest_start", "latest_end", "lunch_start", "lunch_end")}
        labels = {"lunch_break_enabled": "Enable daily lunch break", "lunch_start": "Lunch starts",
                  "lunch_end": "Lunch ends", "faculty_rest_minutes": "Preferred time between faculty classes",
                  "faculty_gap_weight": "Faculty compactness weight"}
        help_texts = {
            "lunch_break_enabled": "Hard rule when enabled: no meetings may overlap this interval on any day in this department and term. Existing meetings stay in place and are flagged for review.",
            "lunch_start": "Required when lunch is enabled. Choose the authorized start time; 12:00 is only an example, not institutional policy.",
            "lunch_end": "Required when lunch is enabled. Meetings may end exactly at lunch start or begin exactly at lunch end. Off-grid lunch times are supported.",
            "faculty_rest_minutes": "Soft preference between the actual end of one faculty class and the next start on the same day, including other sections. Short gaps are minimized first; existing weighted preferences, including compactness, come second. It is not guaranteed.",
            "faculty_gap_weight": "Existing gap setting: minimizes total daily idle slots and favors compact schedules. It does not provide a minimum rest period. When a rest preference is enabled, this weight is secondary.",
        }

    def __init__(self, *args, user, term=None, **kwargs):
        super().__init__(*args, user=user, term=term, **kwargs)
        if self.is_bound:
            # Older clients have none of the new controls. Preserve saved policy;
            # the current form submits start/end fields even when lunch is off.
            self.data = self.data.copy()
            if "faculty_rest_minutes" not in self.data:
                self.data["faculty_rest_minutes"] = self.instance.faculty_rest_minutes
            if not any(name in self.data for name in ("lunch_break_enabled", "lunch_start", "lunch_end")):
                self.data["lunch_break_enabled"] = self.instance.lunch_break_enabled
                for name in ("lunch_start", "lunch_end"):
                    value = getattr(self.instance, name)
                    self.data[name] = value.strftime("%H:%M") if value else ""
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
    faculty_gap_weight = forms.IntegerField(required=False, min_value=0, label="Faculty compactness weight",
        help_text="Minimizes total idle slots; distinct from the saved minimum-rest preference. Rest takes priority when enabled.")
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
        use_current_term_field(self, user)
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

    def __init__(self, *args, user, academic_browse=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_term"].queryset = accessible_terms(user)
        self.fields["department"].queryset = department_scoped_queryset(user, Department.objects.all(), "pk")
        self.fields["department"].label_from_instance = lambda department: f"{department.name} ({department.code})"
        self.fields["faculty"].queryset = scoped_faculty(user)
        self.fields["room"].queryset = scope_resources(user, Room.objects.all())
        self.fields["section"].queryset = scoped(user, ClassSection.objects.all())
        if academic_browse:
            from resources.browsing import YEAR_CHOICES, narrow_section_choices
            self.fields["year_level"] = forms.ChoiceField(required=False, choices=YEAR_CHOICES, label="Year level")
            self.fields["department"].label = "Program / department"
            from accounts.permissions import is_system_admin
            if not is_system_admin(user):
                self.fields["department"].label_from_instance = lambda department: department.name
            narrow_section_choices(self.fields["section"], self.data if self.is_bound else self.initial)
        use_current_term_field(self, user)
        self.style_fields()


class RoomEligibilityForm(TimetableFilter):
    subject_offering = forms.ModelChoiceField(queryset=None, required=False, label="Class to compare")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields = {name: self.fields[name] for name in
                       ("academic_term", "department", "subject_offering")}
        self.fields["academic_term"].label = "Academic term"
        self.fields["department"].label = "Program / department"
        terms = self.fields["academic_term"].queryset
        if self.is_bound and not self.data.get("academic_term") and "academic_term" not in self.data:
            self.data = self.data.copy()
            term = accessible_terms(user, active=True).first() or terms.first()
            self.data["academic_term"] = term.pk if term else ""
        choices = scoped_records(user, SubjectOffering.objects.filter(is_active=True,
            subject__is_active=True, department__is_active=True, department__college__is_active=True,
            academic_term__is_active=True, academic_term__academic_year__is_active=True,
            academic_term__semester__is_active=True)).select_related("subject", "academic_term", "department")
        for name in ("academic_term", "department"):
            raw = self.data.get(name)
            if raw:
                try:
                    choices = choices.filter(**{name + "_id": int(raw)})
                except (ValueError, TypeError):
                    choices = choices.none()
        self.fields["subject_offering"].queryset = choices.order_by("subject__code", "code", "pk")
        self.fields["subject_offering"].label_from_instance = lambda obj: f"{obj.subject.code} · {obj.subject.title} · {obj.code} · {obj.department.name}"
        self.fields["subject_offering"].empty_label = "Select a class"
        self.fields["subject_offering"].help_text = "After changing term or department, select a class in that context and compare again."
        self.style_fields()

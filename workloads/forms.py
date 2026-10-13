from django import forms

from accounts.permissions import department_scoped_queryset
from academics.models import Subject
from resources.forms import ResourceFilterForm, StyledFormMixin
from .calculation import STATUSES
from .models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering
from .selectors import accessible_terms, scoped_faculty, scoped_records
from .monitoring import CONDITIONS
from academics.calendar import use_current_term_field


class TermForm(StyledFormMixin, forms.ModelForm):
    def __init__(self, *args, user, term, **kwargs):
        super().__init__(*args, **kwargs)
        self.term = term
        if "faculty" in self.fields:
            self.fields["faculty"].queryset = scoped_faculty(user).filter(is_active=True, home_department__is_active=True, home_department__college__is_active=True)
        if "academic_term" in self.fields:
            self.fields["academic_term"].queryset = accessible_terms(user, active=True).filter(pk=term.pk)
            self.initial["academic_term"] = term.pk
        use_current_term_field(self, user)
        self.style_fields()

    def clean(self):
        data = super().clean()
        if "academic_term" in data and data["academic_term"] != self.term:
            self.add_error("academic_term", "The record must belong to the selected term.")
        return data


class AvailabilityForm(TermForm):
    class Meta:
        model = FacultyAvailability
        fields = ["faculty", "academic_term", "day_of_week", "start_time", "end_time", "availability_type", "notes"]
        widgets = {"start_time": forms.TimeInput(format="%H:%M", attrs={"type": "time"}), "end_time": forms.TimeInput(format="%H:%M", attrs={"type": "time"})}
        help_texts = {"availability_type": "Available: can teach. Unavailable: generator must avoid. Preferred: requested times, without a guarantee."}


class OfferingForm(TermForm):
    class Meta:
        model = SubjectOffering
        fields = ["subject", "academic_term", "code", "lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours", "is_active", "notes"]

    def __init__(self, *args, user, term, **kwargs):
        super().__init__(*args, user=user, term=term, **kwargs)
        self.fields["subject"].queryset = department_scoped_queryset(user, Subject.objects.filter(is_active=True, owning_department__is_active=True, owning_department__college__is_active=True), "owning_department")
        for name in ("lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours"):
            self.fields[name].required = False
            self.fields[name].help_text = "On creation, blank copies the catalog value. Existing offering values are independent of later catalog edits."

    def clean(self):
        data = super().clean()
        subject = data.get("subject")
        if subject:
            self.instance.department = subject.owning_department
            for name in ("lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours"):
                if data.get(name) is None:
                    if self.instance.pk:
                        self.add_error(name, "Specify the offering value when editing.")
                    elif getattr(subject, name) is None:
                        self.add_error(name, "The catalog breakdown is unconfigured. Supply an explicit value.")
                    else:
                        data[name] = getattr(subject, name)
        return data


class AssignmentForm(TermForm):
    academic_term = forms.ModelChoiceField(queryset=None)

    class Meta:
        model = FacultySubjectAssignment
        fields = ["faculty", "academic_term", "subject_offering", "share", "notes"]
        labels = {"subject_offering": "Class to teach", "share": "Teaching share"}
        help_texts = {"share": "Enter 1 for the whole class (100%), or 0.5 for half (50%). Shared teaching must total 1 (100%)."}

    def __init__(self, *args, user, term, offering=None, **kwargs):
        super().__init__(*args, user=user, term=term, **kwargs)
        self.fields["subject_offering"].queryset = scoped_records(user, SubjectOffering.objects.filter(academic_term=term, is_active=True, subject__is_active=True, department__is_active=True, department__college__is_active=True)).select_related("subject", "academic_term")
        value = (self.data if self.is_bound else self.initial).get("subject_offering") or self.instance.subject_offering_id
        if offering is None:
            try:
                offering = self.fields["subject_offering"].queryset.filter(pk=value).first() if value else None
            except (ValueError, TypeError):
                offering = None
        if offering:
            self.fields["faculty"].queryset = self.fields["faculty"].queryset.filter(home_department_id=offering.department_id)
        self.fields["faculty"].empty_label = "Choose an instructor"


class WorkloadFilterForm(ResourceFilterForm):
    academic_term = forms.ModelChoiceField(queryset=None, label="Academic term")
    faculty = forms.ModelChoiceField(queryset=None, required=False)
    day_of_week = forms.TypedChoiceField(required=False, coerce=int, empty_value=None, choices=[("", "All days"), *FacultyAvailability.DAYS])
    availability_type = forms.ChoiceField(required=False, choices=[("", "All availability types"), *FacultyAvailability.Kind.choices])
    workload_status = forms.ChoiceField(required=False, choices=[("", "All workload statuses"), *[(s, s.replace("_", " ").title()) for s in STATUSES]])

    def __init__(self, *args, user, section, **kwargs):
        super().__init__(*args, user=user, kind="faculty", **kwargs)
        self.fields["academic_term"].queryset = accessible_terms(user)
        self.fields["academic_term"].label = "Academic term"
        self.fields["q"].label = "Find faculty or class" if section == "assignments" else "Search"
        self.fields["faculty"].queryset = scoped_faculty(user)
        self.fields.pop("status")
        if section != "availability":
            self.fields.pop("day_of_week")
            self.fields.pop("availability_type")
        if section != "monitor":
            self.fields.pop("workload_status")
            self.fields.pop("employment_category")
            self.fields.pop("academic_rank")
        if section == "offerings":
            self.fields.pop("faculty")
            from timetabling.models import ClassSection
            from timetabling.queries import scoped
            from resources.browsing import YEAR_CHOICES, narrow_section_choices
            self.fields["department"].label = "Program / department"
            from accounts.permissions import is_system_admin
            if not is_system_admin(user):
                self.fields["department"].label_from_instance = lambda department: department.name
            self.fields["year_level"] = forms.ChoiceField(required=False, choices=YEAR_CHOICES, label="Year level")
            self.fields["section"] = forms.ModelChoiceField(required=False, queryset=scoped(user, ClassSection.objects.all()))
            narrow_section_choices(self.fields["section"], self.data if self.is_bound else self.initial)
        use_current_term_field(self, user)
        self.style_fields()


class WorkloadMonitorFilterForm(WorkloadFilterForm):
    """Keep existing filters while adding the capacity-focused display filter."""

    condition = forms.ChoiceField(required=False, label="Workload condition", choices=[
        ("", "All conditions"), *[(key, label) for key, label, _, _ in CONDITIONS],
        ("above", "Above target, within maximum"),
    ])

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, section="monitor", **kwargs)
        self.fields["q"].label = "Find a faculty member"
        self.fields["q"].widget = forms.TextInput(attrs={
            "type": "search", "placeholder": "Find a faculty member or employee code"})
        self.fields["department"].empty_label = "All permitted departments"
        self.fields["department"].label_from_instance = lambda department: department.name
        self.fields["employment_category"].empty_label = "All categories"
        self.fields["workload_status"].label = "Legacy calculator status"
        self.style_fields()

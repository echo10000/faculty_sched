from django import forms
from django.db.models import Q

from accounts.permissions import department_scoped_queryset, is_system_admin, scoped_colleges
from academics.models import Subject
from core.models import College, Department
from faculty.models import AcademicRank, EmploymentCategory, Faculty
from scheduling.models import Room
from .models import Building, RoomType


class StyledFormMixin:
    def style_fields(self):
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = 3


class ScopedModelForm(StyledFormMixin, forms.ModelForm):
    college = forms.ModelChoiceField(queryset=College.objects.none(), help_text="Department must belong to this college.")
    department_field = None

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["college"].queryset = scoped_colleges(user, College.objects.filter(is_active=True))
        if self.department_field:
            self.fields[self.department_field].queryset = department_scoped_queryset(user, Department.objects.filter(is_active=True, college__is_active=True), "pk")
            self.fields[self.department_field].required = True
            if self.instance.pk and getattr(self.instance, self.department_field + "_id"):
                self.initial["college"] = getattr(self.instance, self.department_field).college_id
        for name in ("employment_category", "academic_rank", "category", "building"):
            if name in self.fields:
                existing_id = getattr(self.instance, name + "_id", None)
                self.fields[name].queryset = self.fields[name].queryset.filter(Q(is_active=True) | Q(pk=existing_id))
        self.style_fields()

    def clean(self):
        cleaned = super().clean()
        if self.department_field:
            department, college = cleaned.get(self.department_field), cleaned.get("college")
            if department and college and department.college_id != college.pk:
                self.add_error(self.department_field, "Department must belong to the selected college.")
        return cleaned


class FacultyForm(ScopedModelForm):
    department_field = "home_department"

    class Meta:
        model = Faculty
        fields = ["employee_id", "first_name", "middle_name", "last_name", "suffix", "email", "contact_number", "college", "home_department", "employment_category", "academic_rank", "recommended_load", "maximum_load", "notes"]
        help_texts = {"recommended_load": "Teaching load units. Blank inherits a term policy; zero means zero.", "maximum_load": "Teaching load units. Blank inherits a term policy."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employment_category"].required = True


class SubjectForm(ScopedModelForm):
    department_field = "owning_department"

    class Meta:
        model = Subject
        fields = ["code", "title", "description", "college", "owning_department", "lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lecture_units"].required = True


class RoomForm(ScopedModelForm):
    class Meta:
        model = Room
        fields = ["code", "name", "building", "category", "capacity", "college", "owner_department"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = True
        self.fields["category"].required = True
        self.fields["college"].required = not is_system_admin(self.user)
        self.fields["college"].help_text = "Choose a college and optionally an owning department. Only system administrators can leave both blank for an institution-owned room."
        self.fields["owner_department"].queryset = department_scoped_queryset(self.user, Department.objects.filter(is_active=True, college__is_active=True), "pk")
        if self.instance.pk and self.instance.college:
            self.initial["college"] = self.instance.college.pk

    def clean(self):
        cleaned = super().clean()
        department, college = cleaned.get("owner_department"), cleaned.get("college")
        if department and (not college or department.college_id != college.pk):
            self.add_error("owner_department", "Department must belong to the selected college.")
        self.instance.owner_college = college if not department else None
        return cleaned


class ResourceFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, max_length=100, label="Search")
    status = forms.ChoiceField(required=False, choices=[("", "All statuses"), ("active", "Active"), ("inactive", "Inactive")])
    college = forms.ModelChoiceField(queryset=College.objects.none(), required=False)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)

    def __init__(self, *args, user, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["college"].queryset = scoped_colleges(user, College.objects.all())
        self.fields["department"].queryset = department_scoped_queryset(user, Department.objects.all(), "pk")
        self.fields["college"].label_from_instance = lambda college: f"{college.name} ({college.code})"
        self.fields["department"].label_from_instance = lambda department: f"{department.name} ({department.code})"
        if kind == "faculty":
            if user.has_perm("academics.view_academicterm") and user.has_perm("workloads.view_workload"):
                from workloads.selectors import accessible_terms
                self.fields["academic_term"] = forms.ModelChoiceField(queryset=accessible_terms(user), required=False, label="Workload term")
            self.fields["employment_category"] = forms.ModelChoiceField(queryset=EmploymentCategory.objects.all(), required=False)
            self.fields["academic_rank"] = forms.ModelChoiceField(queryset=AcademicRank.objects.all(), required=False)
        if kind == "rooms":
            self.fields["category"] = forms.ModelChoiceField(queryset=RoomType.objects.all(), required=False, label="Room type")
            self.fields["building"] = forms.ModelChoiceField(queryset=Building.objects.all(), required=False)
        self.style_fields()

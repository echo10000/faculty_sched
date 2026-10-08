"""Scoped selection for a workload recommendation request."""

from django import forms

from accounts.permissions import department_scoped_queryset
from academics.models import AcademicTerm
from core.models import Department
from .selectors import accessible_terms
from academics.calendar import use_current_term_field


class BalancingRequestForm(forms.Form):
    academic_term = forms.ModelChoiceField(queryset=AcademicTerm.objects.none(), label="Academic term")
    department = forms.ModelChoiceField(queryset=Department.objects.none(), label="Department")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_term"].queryset = accessible_terms(user, active=True)
        self.fields["department"].queryset = department_scoped_queryset(
            user,
            Department.objects.filter(is_active=True, college__is_active=True).select_related("college"),
            "pk",
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select"
        use_current_term_field(self, user)

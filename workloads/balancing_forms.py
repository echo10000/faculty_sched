"""Scoped selection for a workload recommendation request."""

from django import forms

from accounts.permissions import department_scoped_queryset
from academics.models import AcademicTerm
from core.models import Department


class BalancingRequestForm(forms.Form):
    academic_term = forms.ModelChoiceField(queryset=AcademicTerm.objects.none(), label="Academic term")
    department = forms.ModelChoiceField(queryset=Department.objects.none(), label="Department")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_term"].queryset = AcademicTerm.objects.filter(
            is_active=True, academic_year__is_active=True, semester__is_active=True,
        ).select_related("academic_year", "semester")
        self.fields["department"].queryset = department_scoped_queryset(
            user,
            Department.objects.filter(is_active=True, college__is_active=True).select_related("college"),
            "pk",
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select"

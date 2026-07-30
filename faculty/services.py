from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from accounts.permissions import department_scoped_queryset

from .models import Faculty


def _load_breakdown(faculty, assigned_units):
    """Build the public load result from a faculty object and aggregate value."""
    assigned_units = assigned_units or Decimal("0.0")
    base_load = faculty.base_load_units
    units_released = faculty.designation.units_released if faculty.designation else Decimal("0.0")
    required_load = faculty.effective_load_units
    difference = assigned_units - required_load

    if difference < 0:
        status = "underload"
    elif difference > 0:
        status = "overload"
    else:
        status = "on_target"

    return {
        "base_load": base_load,
        "units_released": units_released,
        "required_load": required_load,
        "assigned_units": assigned_units,
        "status": status,
        "difference": difference,
    }


def compute_faculty_load(faculty, term):
    """Return the teaching-load breakdown for one faculty member and term."""
    assigned_units = faculty.assignments.filter(term=term).aggregate(total=Sum("units_credited"))["total"]
    return _load_breakdown(faculty, assigned_units)


def compute_department_load_summary(department, term, user=None):
    """Return load breakdowns for a department, respecting an optional admin scope."""
    faculty_queryset = Faculty.objects.filter(home_department=department)
    if user is not None:
        faculty_queryset = department_scoped_queryset(user, faculty_queryset, "home_department")

    decimal_output = DecimalField(max_digits=6, decimal_places=1)
    faculty_queryset = faculty_queryset.select_related("designation").annotate(
        assigned_units=Coalesce(
            Sum("assignments__units_credited", filter=Q(assignments__term=term)),
            Value(Decimal("0.0"), output_field=decimal_output),
            output_field=decimal_output,
        )
    )
    return [
        {"faculty": faculty, **_load_breakdown(faculty, faculty.assigned_units)}
        for faculty in faculty_queryset
    ]

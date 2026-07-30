from decimal import Decimal

from django.db.models import Sum

from accounts.permissions import department_scoped_queryset

from .models import Faculty


def compute_faculty_load(faculty, term):
    """Return the teaching-load breakdown for one faculty member and term."""
    assigned_units = faculty.assignments.filter(term=term).aggregate(total=Sum("units_credited"))["total"]
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


def compute_department_load_summary(department, term, user=None):
    """Return load breakdowns for a department, respecting an optional admin scope."""
    faculty_queryset = Faculty.objects.filter(home_department=department).select_related("designation")
    if user is not None:
        faculty_queryset = department_scoped_queryset(user, faculty_queryset, "home_department")

    return [
        {"faculty": faculty, **compute_faculty_load(faculty, term)}
        for faculty in faculty_queryset
    ]

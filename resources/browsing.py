"""Presentation helpers for the existing program/section relationships."""
from accounts.permissions import is_system_admin


YEAR_CHOICES = [("", "All year levels"), *[(str(year), f"Year {year}") for year in range(1, 5)]]


def academic_group_label(user, department, years=()):
    if department is None:
        return "No program assigned"
    program = department.name
    if is_system_admin(user):
        program = f"{department.college.name} · {program}"
    years = sorted(set(year for year in years if year is not None))
    if years:
        label = ("Year " if len(years) == 1 else "Years ") + ", ".join(map(str, years))
    else:
        label = "Year not assigned"
    return f"{program} · {label}"


def narrow_section_choices(field, values):
    """Retain scope and narrow optional section choices to the selected context."""
    sections = field.queryset
    for name in ("academic_term", "department", "year_level"):
        raw = values.get(name)
        if raw:
            try:
                sections = sections.filter(**{name: int(raw)})
            except (ValueError, TypeError):
                sections = sections.none()
                break
    field.queryset = sections.select_related("academic_term")
    field.label_from_instance = lambda section: section.code

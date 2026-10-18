from accounts.permissions import department_scoped_queryset, require_access
from academics.models import AcademicTerm
from faculty.models import Faculty
from academics.calendar import constrain_term_queryset, staff_uses_current_term, current_terms, calendar_enabled, prioritize_current_term


def scoped_records(user, queryset):
    name = queryset.model._meta.model_name
    if name == "subjectoffering":
        return constrain_term_queryset(user, department_scoped_queryset(user, queryset))
    qs = department_scoped_queryset(user, queryset, "faculty__home_department")
    if name == "facultysubjectassignment":
        qs = department_scoped_queryset(user, qs, "subject_offering__department")
    lookup = "subject_offering__academic_term" if name == "facultysubjectassignment" else "academic_term"
    return constrain_term_queryset(user, qs, lookup)


def scoped_faculty(user):
    return department_scoped_queryset(user, Faculty.objects.select_related("home_department__college", "employment_category", "academic_rank", "designation"), "home_department")


def accessible_terms(user, *, active=False):
    require_access(user, "academics.view_academicterm")
    qs = AcademicTerm.objects.select_related("academic_year", "semester")
    if staff_uses_current_term(user):
        qs = qs.filter(pk__in=current_terms())
    elif calendar_enabled():
        qs = prioritize_current_term(qs)
    if active:
        qs = qs.filter(is_active=True, academic_year__is_active=True, semester__is_active=True)
    return qs

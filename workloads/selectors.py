from accounts.permissions import department_scoped_queryset, require_access
from academics.models import AcademicTerm
from faculty.models import Faculty


def scoped_records(user, queryset):
    name = queryset.model._meta.model_name
    if name == "subjectoffering":
        return department_scoped_queryset(user, queryset)
    qs = department_scoped_queryset(user, queryset, "faculty__home_department")
    if name == "facultysubjectassignment":
        qs = department_scoped_queryset(user, qs, "subject_offering__department")
    return qs


def scoped_faculty(user):
    return department_scoped_queryset(user, Faculty.objects.select_related("home_department__college", "employment_category", "academic_rank"), "home_department")


def accessible_terms(user, *, active=False):
    require_access(user, "academics.view_academicterm")
    qs = AcademicTerm.objects.select_related("academic_year", "semester")
    if active:
        qs = qs.filter(is_active=True, academic_year__is_active=True, semester__is_active=True)
    return qs

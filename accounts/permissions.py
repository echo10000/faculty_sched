from django.core.exceptions import PermissionDenied

from .models import AdminProfile


def department_scoped_queryset(user, queryset, department_field="department"):
    """Limit a queryset to the authenticated administrator's department."""
    try:
        profile = user.admin_profile
    except AdminProfile.DoesNotExist as exc:
        raise PermissionDenied("An administrator profile is required.") from exc

    if profile.role == AdminProfile.Role.SUPER_ADMIN:
        return queryset
    if profile.role == AdminProfile.Role.DEPARTMENT_ADMIN:
        return queryset.filter(**{department_field: profile.department})
    raise PermissionDenied("The administrator role is not recognized.")

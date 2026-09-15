from django.db.models import Q

from accounts.permissions import department_scoped_queryset, is_system_admin, profile_for


def scope_resources(user, queryset):
    """Apply ownership scope before lookups, filtering, counts, or writes."""
    label = queryset.model._meta.label_lower
    if label == "faculty.faculty":
        return department_scoped_queryset(user, queryset, "home_department")
    if label == "academics.subject":
        return department_scoped_queryset(user, queryset, "owning_department")
    if label != "scheduling.room":
        raise ValueError("Unsupported master-data model")
    if is_system_admin(user):
        return queryset
    profile = profile_for(user)
    if not profile:
        return queryset.none()
    if profile.department_id:
        return queryset.filter(owner_department_id=profile.department_id)
    return queryset.filter(Q(owner_college_id=profile.college_id) | Q(owner_department__college_id=profile.college_id))


def available_resources(user, model):
    """Active, owned records eligible for future selection (no assignments here)."""
    qs = scope_resources(user, model.objects.filter(is_active=True))
    label = model._meta.label_lower
    if label == "faculty.faculty":
        return qs.filter(home_department__is_active=True, home_department__college__is_active=True)
    if label == "academics.subject":
        return qs.filter(owning_department__is_active=True, owning_department__college__is_active=True)
    return qs.filter(Q(owner_department__isnull=True) | Q(owner_department__is_active=True, owner_department__college__is_active=True)).filter(Q(owner_college__isnull=True) | Q(owner_college__is_active=True))

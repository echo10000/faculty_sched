from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from accounts.permissions import is_system_admin, profile_for, require_access
from audit.services import record_event
from .selectors import scope_resources


def assert_owner(user, instance):
    if is_system_admin(user):
        return
    profile = profile_for(user)
    department = getattr(instance, "home_department", None) or getattr(instance, "owning_department", None) or getattr(instance, "owner_department", None)
    college = department.college if department else getattr(instance, "owner_college", None)
    if not profile or (profile.department_id and (not department or department.pk != profile.department_id)) or (profile.college_id and (not college or college.pk != profile.college_id)):
        raise PermissionDenied("This organizational unit is outside your scope.")


def permission_for(model, action):
    return f"{model._meta.app_label}.{action}_{model._meta.model_name}"


@transaction.atomic
def save_resource(*, user, form_class, data, pk=None):
    """Rebuild the form against a locked scoped row; never trust an earlier UI check."""
    model = form_class._meta.model
    require_access(user, permission_for(model, "change" if pk else "add"))
    instance = get_object_or_404(scope_resources(user, model.objects.select_for_update(of=("self",))), pk=pk) if pk else model()
    original_department = getattr(instance, "home_department_id", None) or getattr(instance, "owning_department_id", None)
    form = form_class(data, instance=instance, user=user)
    if not form.is_valid():
        return None, form
    instance = form.save(commit=False)
    assert_owner(user, instance)
    new_department = getattr(instance, "home_department_id", None) or getattr(instance, "owning_department_id", None)
    if pk and original_department != new_department:
        if model._meta.label_lower == "faculty.faculty" and (instance.teaching_assignments.exists() or instance.availability_records.exists()):
            raise ValidationError("Faculty with term teaching records cannot be transferred here. A reviewed organizational transfer is required.")
        if model._meta.label_lower == "academics.subject" and instance.term_offerings.exists():
            raise ValidationError("A subject with term offerings cannot change department. Create a new catalog record for the new department.")
    if not pk:
        instance.created_by = user
    instance.save()
    record_event(f"{model._meta.model_name}.{'updated' if pk else 'created'}", actor=user, obj=instance, details={"fields": [name for name in form.changed_data if name != "college"]})
    return instance, form


@transaction.atomic
def set_resource_status(*, user, model, pk, active):
    if not isinstance(active, bool):
        raise ValidationError("A valid active status is required.")
    require_access(user, permission_for(model, "activate"))
    instance = get_object_or_404(scope_resources(user, model.objects.select_for_update(of=("self",))), pk=pk)
    assert_owner(user, instance)
    if instance.is_active != active:
        instance.is_active = active
        instance.save()
        record_event(f"{model._meta.model_name}.{'activated' if active else 'deactivated'}", actor=user, obj=instance)
    return instance

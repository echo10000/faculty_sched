from django.contrib.auth.backends import ModelBackend

from .permissions import profile_for, role_permissions


class ScopedRoleBackend(ModelBackend):
    """Django permissions remain additive; organizational scope is always checked separately."""

    def get_all_permissions(self, user_obj, obj=None):
        if obj is not None or not self.user_can_authenticate(user_obj) or user_obj.is_anonymous:
            return set()
        profile = profile_for(user_obj)
        if not user_obj.is_superuser and profile is None:
            return set()
        return super().get_all_permissions(user_obj, obj) | role_permissions(profile)

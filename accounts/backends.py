from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

from .permissions import profile_for, role_permissions
from .throttle import clear_account_failures, is_blocked, record_failure


class ScopedRoleBackend(ModelBackend):
    """Django permissions remain additive; organizational scope is always checked separately."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username if username is not None else kwargs.get(get_user_model().USERNAME_FIELD, "")
        if request is not None and is_blocked(request, identifier):
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if request is not None:
            if user is None:
                record_failure(request, identifier)
            else:
                clear_account_failures(request, identifier)
        return user

    def get_all_permissions(self, user_obj, obj=None):
        if obj is not None or not self.user_can_authenticate(user_obj) or user_obj.is_anonymous:
            return set()
        profile = profile_for(user_obj)
        if not user_obj.is_superuser and profile is None:
            return set()
        return super().get_all_permissions(user_obj, obj) | role_permissions(profile)

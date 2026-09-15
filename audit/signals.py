from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import record_event


@receiver(user_logged_in)
def signed_in(sender, request, user, **kwargs):
    record_event("auth.login", actor=user)


@receiver(user_logged_out)
def signed_out(sender, request, user, **kwargs):
    record_event("auth.logout", actor=user)


@receiver(user_login_failed)
def rejected_login(sender, credentials, request, **kwargs):
    # Credentials are intentionally never persisted, including attempted usernames.
    record_event("auth.login_failed")

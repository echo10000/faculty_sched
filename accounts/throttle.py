"""Database-backed login failure limits without storing account or IP text."""

from datetime import timedelta
from hashlib import sha256
import hmac

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginFailureBucket


WINDOW = timedelta(minutes=15)
ACCOUNT_LIMIT = 5
SOURCE_LIMIT = 100


def _key(kind, value):
    message = f"{kind}:{value}".encode("utf-8", errors="replace")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, sha256).hexdigest()


def _keys(request, username):
    keys = [(_key("account", str(username).strip().casefold()), ACCOUNT_LIMIT)]
    # REMOTE_ADDR is the address observed by the web server. Forwarded headers
    # are caller-controlled unless a trusted proxy is configured separately.
    source = request.META.get("REMOTE_ADDR", "")
    if source:
        keys.append((_key("source", source), SOURCE_LIMIT))
    return keys


def is_blocked(request, username):
    if request is None:
        return False
    now = timezone.now()
    limits = dict(_keys(request, username))
    return any(
        bucket.expires_at > now and bucket.failures >= limits[bucket.key]
        for bucket in LoginFailureBucket.objects.filter(pk__in=limits)
    )


def record_failure(request, username):
    if request is None:
        return
    now = timezone.now()
    with transaction.atomic():
        for key, limit in sorted(_keys(request, username)):
            bucket, _ = LoginFailureBucket.objects.select_for_update().get_or_create(
                pk=key, defaults={"failures": 0, "expires_at": now + WINDOW},
            )
            if bucket.expires_at <= now:
                bucket.failures = 0
                bucket.expires_at = now + WINDOW
            bucket.failures = min(limit, bucket.failures + 1)
            bucket.save(update_fields=["failures", "expires_at"])


def clear_account_failures(request, username):
    if request is not None:
        LoginFailureBucket.objects.filter(pk=_key("account", str(username).strip().casefold())).update(
            failures=0, expires_at=timezone.now() + WINDOW,
        )

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit records are immutable.")

    def delete(self):
        raise ValidationError("Audit records cannot be deleted.")


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    college = models.ForeignKey("core.College", null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        default_permissions = ("view",)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records cannot be deleted.")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AdminProfile(models.Model):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "System Admin"
        DEAN = "dean", "College Dean"
        DEPT_CHAIR = "dept_chair", "Department Chair"
        STAFF = "staff", "Authorized Staff"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_profile")
    role = models.CharField(max_length=20, choices=Role.choices)
    college = models.ForeignKey("core.College", null=True, blank=True, on_delete=models.PROTECT, related_name="administrators")
    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="admins")
    is_enabled = models.BooleanField(default=True, help_text="Disabled profiles cannot access the application.")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(role__in=["super_admin", "dean", "dept_chair", "staff"]), name="profile_known_role"),
            models.CheckConstraint(
                condition=Q(is_enabled=False)
                | Q(role="super_admin", college__isnull=True, department__isnull=True)
                | Q(role="dean", college__isnull=False, department__isnull=True)
                | Q(role="dept_chair", college__isnull=True, department__isnull=False)
                | (Q(role="staff") & (Q(college__isnull=False, department__isnull=True) | Q(college__isnull=True, department__isnull=False))),
                name="profile_valid_organizational_scope",
            )
        ]

    def clean(self):
        super().clean()
        if self.is_enabled:
            if self.role == self.Role.SUPER_ADMIN and (self.college_id or self.department_id):
                raise ValidationError("System administrators must have institution-wide scope.")
            if self.role == self.Role.DEAN and (not self.college_id or self.department_id):
                raise ValidationError("A dean requires one college and no department.")
            if self.role == self.Role.DEPT_CHAIR and (not self.department_id or self.college_id):
                raise ValidationError("A chair requires one department; its college is derived.")
            if self.role == self.Role.STAFF and bool(self.college_id) == bool(self.department_id):
                raise ValidationError("Staff require either a college or a department scope.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"


class LoginFailureBucket(models.Model):
    """Opaque, short-lived counter shared by all application workers."""

    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        default_permissions = ()

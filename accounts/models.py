from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import Department


class AdminProfile(models.Model):
    class Role(models.TextChoices):
        DEPARTMENT_ADMIN = "department_admin", "Department admin"
        SUPER_ADMIN = "super_admin", "Super admin"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="admin_profile")
    role = models.CharField(max_length=20, choices=Role.choices)
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="admins",
    )

    def clean(self):
        super().clean()
        if self.role == self.Role.DEPARTMENT_ADMIN and self.department is None:
            raise ValidationError({"department": "Department admins must belong to a department."})
        if self.role == self.Role.SUPER_ADMIN and self.department is not None:
            raise ValidationError({"department": "Super admins cannot be limited to a department."})

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"

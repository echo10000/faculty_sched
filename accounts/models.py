from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import Department


class AdminProfile(models.Model):
    """Administrative workflow hierarchy.

    Department admins create and edit draft schedules. Department chairs review
    drafts and submit them for approval. Deans approve submissions campus-wide;
    approved assignments must be explicitly unlocked by a dean before revision.
    Super admins retain campus-wide administrative access.
    """
    class Role(models.TextChoices):
        DEPARTMENT_ADMIN = "department_admin", "Department admin"
        DEPT_CHAIR = "dept_chair", "Department chair"
        DEAN = "dean", "Dean"
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
        if self.role in (self.Role.DEPARTMENT_ADMIN, self.Role.DEPT_CHAIR) and self.department is None:
            raise ValidationError({"department": "Department administrators and chairs must belong to a department."})
        if self.role in (self.Role.SUPER_ADMIN, self.Role.DEAN) and self.department is not None:
            raise ValidationError({"department": "Deans and super admins cannot be limited to a department."})

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"

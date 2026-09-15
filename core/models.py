from django.db import models
from django.core.exceptions import ValidationError

from .base import TrackedModel


class College(TrackedModel):
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=10, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        permissions = [("view_dashboard", "Can access the dashboard")]

    def __str__(self):
        return self.code


class Department(TrackedModel):
    college = models.ForeignKey(College, on_delete=models.PROTECT, related_name="departments")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["college", "code"], name="unique_department_code_per_college")]

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        if self.is_active and self.college_id and not self.college.is_active:
            raise ValidationError({"college": "An active department needs an active college."})


class Program(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="programs")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=10)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["department", "code"], name="unique_program_code_per_department")]

    def __str__(self):
        return self.code


class SystemSetting(TrackedModel):
    class Key(models.TextChoices):
        INSTITUTION_NAME = "institution_name", "Institution name"
        SUPPORT_EMAIL = "support_email", "Support email"

    key = models.CharField(max_length=50, unique=True, choices=Key.choices)
    value = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.CheckConstraint(condition=models.Q(key__in=["institution_name", "support_email"]), name="setting_known_key")]

    def clean(self):
        super().clean()
        if self.key == self.Key.SUPPORT_EMAIL:
            from django.core.validators import validate_email
            validate_email(self.value)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.get_key_display()

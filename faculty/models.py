from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from academics.models import Subject
from core.models import Department


class Designation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    units_released = models.DecimalField(max_digits=3, decimal_places=1, default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Faculty(models.Model):
    class EmploymentType(models.TextChoices):
        FULL_TIME = "full_time", "Full-time"
        PART_TIME = "part_time", "Part-time"

    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)
    employee_id = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    home_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="faculty_members")
    employment_type = models.CharField(max_length=10, choices=EmploymentType.choices)
    base_load_units = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("24.0"))
    designation = models.ForeignKey(Designation, null=True, blank=True, on_delete=models.SET_NULL, related_name="faculty_members")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    @property
    def effective_load_units(self):
        """Target teaching load after designation release, not actual assigned units."""
        return self.base_load_units - (self.designation.units_released if self.designation else Decimal("0"))

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"


class FacultyQualification(models.Model):
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="qualifications")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="qualified_faculty")

    class Meta:
        ordering = ["faculty", "subject"]
        constraints = [models.UniqueConstraint(fields=["faculty", "subject"], name="unique_faculty_subject_qualification")]

    def __str__(self):
        return f"{self.faculty} - {self.subject.code}"

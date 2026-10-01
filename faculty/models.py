from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db.models.functions import Lower

from academics.models import Subject
from core.models import Department
from core.base import TrackedModel


class EmploymentCategory(TrackedModel):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AcademicRank(TrackedModel):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Designation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    units_released = models.DecimalField(max_digits=3, decimal_places=1, default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Faculty(TrackedModel):
    class EmploymentType(models.TextChoices):
        FULL_TIME = "full_time", "Full-time"
        PART_TIME = "part_time", "Part-time"

    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)
    employee_id = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    suffix = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=40, blank=True)
    academic_rank = models.ForeignKey(AcademicRank, null=True, blank=True, on_delete=models.PROTECT)
    employment_category = models.ForeignKey(EmploymentCategory, null=True, blank=True, on_delete=models.PROTECT)
    recommended_load = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    maximum_load = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    notes = models.TextField(blank=True)
    home_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="faculty_members")
    # Compatibility fields for the retained legacy services; new forms use category/capacity.
    employment_type = models.CharField(max_length=40, default="", blank=True)
    base_load_units = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("0.0"))
    designation = models.ForeignKey(Designation, null=True, blank=True, on_delete=models.SET_NULL, related_name="faculty_members")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        permissions = [("activate_faculty", "Can activate or deactivate faculty"),
                       ("view_own_teaching", "Can view and download own published teaching records")]
        constraints = [
            models.UniqueConstraint(Lower("employee_id"), name="faculty_employee_id_case_unique"),
            models.CheckConstraint(condition=models.Q(recommended_load__isnull=True) | models.Q(recommended_load__gte=0), name="faculty_recommended_nonnegative"),
            models.CheckConstraint(condition=models.Q(maximum_load__isnull=True) | models.Q(maximum_load__gte=0), name="faculty_maximum_nonnegative"),
            models.CheckConstraint(condition=models.Q(recommended_load__isnull=True) | models.Q(maximum_load__isnull=True) | models.Q(recommended_load__lte=models.F("maximum_load")), name="faculty_capacity_ordered"),
            models.CheckConstraint(condition=models.Q(base_load_units__gte=0), name="faculty_legacy_load_nonnegative"),
        ]

    @property
    def college(self):
        return self.home_department.college

    def clean(self):
        super().clean()
        self.employee_id = self.employee_id.strip().upper()
        if self.is_active and self.home_department_id and (not self.home_department.is_active or not self.home_department.college.is_active):
            raise ValidationError({"home_department": "Choose an active department and college."})
        if self.employment_category_id:
            self.employment_type = self.employment_category.code

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

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

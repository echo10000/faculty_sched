from django.db import models

from core.models import Department, Program
from core.base import TrackedModel
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db.models.functions import Lower
from decimal import Decimal


class AcademicYear(TrackedModel):
    label = models.CharField(max_length=40, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="academic_year_ordered_dates")]

    def clean(self):
        super().clean()
        if self.pk and self.start_date and self.end_date and self.terms.exclude(start_date__gte=self.start_date, end_date__lte=self.end_date).exists():
            raise ValidationError("Year dates must contain every existing academic term.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.label


class Semester(TrackedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AcademicTerm(TrackedModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="terms")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="terms")
    code = models.CharField(max_length=30)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date", "code"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "code"], name="term_code_per_year"),
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="academic_term_ordered_dates"),
        ]

    def clean(self):
        super().clean()
        if self.academic_year_id and self.start_date and self.end_date:
            year = self.academic_year
            if self.start_date < year.start_date or self.end_date > year.end_date:
                raise ValidationError("Term dates must be within the academic year.")
        if self.is_active and self.semester_id and not self.semester.is_active:
            raise ValidationError({"semester": "Choose an active semester type."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.academic_year} - {self.semester} ({self.code})"


class Curriculum(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="curricula")
    version_year = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["program__name", "-version_year"]
        constraints = [models.UniqueConstraint(fields=["program", "version_year"], name="unique_curriculum_version_per_program")]

    def __str__(self):
        return f"{self.program.code} {self.version_year}"


class Subject(TrackedModel):
    class RequiredRoomType(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        LABORATORY = "laboratory", "Laboratory"
        SPECIALIZED = "specialized", "Specialized"

    code = models.CharField(max_length=15, unique=True)
    title = models.CharField(max_length=255)
    # Retained total column for legacy queries, derived from components when known.
    units = models.DecimalField(max_digits=6, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    lecture_units = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    laboratory_units = models.DecimalField(max_digits=6, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    lecture_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    laboratory_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_general_education = models.BooleanField(default=False)
    required_room_type = models.CharField(
        max_length=12,
        choices=RequiredRoomType.choices,
        null=True,
        blank=True,
    )
    owning_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_subjects")

    class Meta:
        ordering = ["code"]
        permissions = [("activate_subject", "Can activate or deactivate subjects")]
        constraints = [
            models.UniqueConstraint(Lower("code"), name="subject_code_case_unique"),
            models.CheckConstraint(condition=models.Q(units__gte=0), name="subject_total_nonnegative"),
            models.CheckConstraint(condition=models.Q(lecture_units__isnull=True) | models.Q(lecture_units__gte=0), name="subject_lecture_units_nonnegative"),
            models.CheckConstraint(condition=models.Q(laboratory_units__gte=0), name="subject_lab_units_nonnegative"),
            models.CheckConstraint(condition=models.Q(lecture_hours__gte=0), name="subject_lecture_hours_nonnegative"),
            models.CheckConstraint(condition=models.Q(laboratory_hours__gte=0), name="subject_lab_hours_nonnegative"),
            models.CheckConstraint(condition=models.Q(lecture_units__isnull=True) | models.Q(units=models.F("lecture_units") + models.F("laboratory_units")), name="subject_total_matches_components"),
        ]

    @property
    def total_units(self):
        return self.units if self.lecture_units is None else self.lecture_units + (self.laboratory_units or Decimal("0"))

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()
        self.units = self.total_units
        if self.is_active and self.owning_department_id and (not self.owning_department.is_active or not self.owning_department.college.is_active):
            raise ValidationError({"owning_department": "Choose an active department and college."})

    def save(self, *args, **kwargs):
        self.units = self.total_units
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.title}"


class CurriculumSubject(models.Model):
    class Term(models.TextChoices):
        FIRST = "1st", "1st"
        SECOND = "2nd", "2nd"
        SUMMER = "summer", "Summer"

    curriculum = models.ForeignKey(Curriculum, on_delete=models.CASCADE, related_name="curriculum_subjects")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="curriculum_subjects")
    year_level = models.PositiveSmallIntegerField()
    term = models.CharField(max_length=6, choices=Term.choices)

    class Meta:
        ordering = ["year_level", "term", "subject__code"]
        constraints = [models.UniqueConstraint(fields=["curriculum", "subject", "term", "year_level"], name="unique_subject_placement_in_curriculum")]

    def __str__(self):
        return f"{self.curriculum}: {self.subject.code}"


class Student(models.Model):
    class Status(models.TextChoices):
        REGULAR = "regular", "Regular"
        IRREGULAR = "irregular", "Irregular"

    student_number = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="students")
    curriculum = models.ForeignKey(Curriculum, on_delete=models.PROTECT, related_name="students")
    block = models.ForeignKey("scheduling.Block", null=True, blank=True, on_delete=models.SET_NULL, related_name="students")
    year_level = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REGULAR)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.student_number} - {self.last_name}, {self.first_name}"


class IrregularEnrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="irregular_enrollments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="irregular_enrollments")
    term = models.ForeignKey("scheduling.Term", on_delete=models.CASCADE, related_name="irregular_enrollments")

    class Meta:
        ordering = ["student", "subject"]
        constraints = [models.UniqueConstraint(fields=["student", "subject", "term"], name="unique_irregular_enrollment")]

    def __str__(self):
        return f"{self.student} - {self.subject.code}"

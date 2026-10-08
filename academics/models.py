from django.db import models

from core.models import Department, Program
from core.base import TrackedModel
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db.models.functions import Lower
from decimal import Decimal
from datetime import date, timedelta
import calendar as calendar_names
from django.core.validators import MaxValueValidator


MONTHS = [(month, calendar_names.month_name[month]) for month in range(1, 13)]


class AcademicCalendar(TrackedModel):
    name = models.CharField(max_length=100, default="Institution academic calendar")
    is_enabled = models.BooleanField(default=True, help_text="Automatically create calendar periods and use the current term for staff.")
    year_start_month = models.PositiveSmallIntegerField(choices=MONTHS, default=6)
    year_start_day = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(31)])
    last_rollover_date = models.DateField(null=True, blank=True, editable=False)
    academic_years = models.ManyToManyField("AcademicYear", blank=True, related_name="automatic_calendars", editable=False)

    class Meta:
        verbose_name = "automatic academic calendar"
        constraints = [models.UniqueConstraint(fields=["is_enabled"], condition=models.Q(is_enabled=True), name="one_enabled_academic_calendar")]

    def clean(self):
        super().clean()
        try:
            date(2001, self.year_start_month, self.year_start_day)
        except (ValueError, TypeError):
            raise ValidationError({"year_start_day": "Enter a date that exists every year."})
        if self.pk:
            ranges = [pattern.dates_for_year(2001, calendar=self) for pattern in self.patterns.all()]
            for index, (start, end) in enumerate(ranges):
                if any(start <= other_end and end >= other_start for other_start, other_end in ranges[index + 1:]):
                    raise ValidationError("Term dates must not overlap. Edit the term patterns first.")

    def save(self, *args, **kwargs):
        self.last_rollover_date = None
        self.full_clean()
        return super().save(*args, **kwargs)

    def year_dates(self, start_year):
        start = date(start_year, self.year_start_month, self.year_start_day)
        end = date(start_year + 1, self.year_start_month, self.year_start_day) - timedelta(days=1)
        return start, end

    def __str__(self):
        return self.name


class CalendarTermPattern(TrackedModel):
    calendar = models.ForeignKey(AcademicCalendar, on_delete=models.CASCADE, related_name="patterns")
    semester = models.ForeignKey("Semester", on_delete=models.PROTECT)
    code = models.CharField(max_length=30, help_text="Code used for newly created academic terms.")
    start_month = models.PositiveSmallIntegerField(choices=MONTHS)
    start_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)])
    end_month = models.PositiveSmallIntegerField(choices=MONTHS)
    end_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)])

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["calendar", "semester"], name="calendar_semester_pattern_unique"),
                       models.UniqueConstraint(fields=["calendar", "code"], name="calendar_term_code_unique")]

    def dates_for_year(self, start_year, *, calendar=None):
        calendar = calendar or self.calendar
        year_start = (calendar.year_start_month, calendar.year_start_day)
        try:
            start = date(start_year + ((self.start_month, self.start_day) < year_start), self.start_month, self.start_day)
            end = date(start_year + ((self.end_month, self.end_day) < year_start), self.end_month, self.end_day)
        except (ValueError, TypeError):
            raise ValidationError("Term month and day must describe a date that exists every year.")
        if end < start:
            raise ValidationError("Term end must follow its start within the academic year.")
        return start, end

    def clean(self):
        super().clean()
        if not self.calendar_id:
            return
        start, end = self.dates_for_year(2001)
        if not self.semester.is_active:
            raise ValidationError({"semester": "Choose an active semester type."})
        if getattr(self, "_validated_with_calendar_formset", False):
            return
        for other in self.calendar.patterns.exclude(pk=self.pk):
            other_start, other_end = other.dates_for_year(2001)
            if start <= other_end and end >= other_start:
                raise ValidationError("Term dates must not overlap another pattern.")

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        AcademicCalendar.objects.filter(pk=self.calendar_id).update(last_rollover_date=None)
        return result

    def delete(self, *args, **kwargs):
        calendar_id = self.calendar_id
        result = super().delete(*args, **kwargs)
        AcademicCalendar.objects.filter(pk=calendar_id).update(last_rollover_date=None)
        return result

    def __str__(self):
        return f"{self.semester}: {self.code}"


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

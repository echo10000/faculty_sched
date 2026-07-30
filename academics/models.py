from django.db import models

from core.models import Department, Program


class Curriculum(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="curricula")
    version_year = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["program__name", "-version_year"]
        constraints = [models.UniqueConstraint(fields=["program", "version_year"], name="unique_curriculum_version_per_program")]

    def __str__(self):
        return f"{self.program.code} {self.version_year}"


class Subject(models.Model):
    class RequiredRoomType(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        LABORATORY = "laboratory", "Laboratory"
        SPECIALIZED = "specialized", "Specialized"

    code = models.CharField(max_length=15, unique=True)
    title = models.CharField(max_length=255)
    units = models.DecimalField(max_digits=3, decimal_places=1)
    is_general_education = models.BooleanField(default=False)
    required_room_type = models.CharField(
        max_length=12,
        choices=RequiredRoomType.choices,
        null=True,
        blank=True,
    )
    owning_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_subjects")

    class Meta:
        ordering = ["code"]

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

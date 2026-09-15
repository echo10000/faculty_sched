from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from academics.models import Subject
from faculty.models import Faculty

from academics.models import Curriculum
from core.models import Department
from core.base import TrackedModel
from django.db.models.functions import Lower


class Room(TrackedModel):
    class RoomType(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        LABORATORY = "laboratory", "Laboratory"
        SPECIALIZED = "specialized", "Specialized"

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=100, blank=True, default="")
    category = models.ForeignKey("resources.RoomType", null=True, blank=True, on_delete=models.PROTECT)
    building = models.ForeignKey("resources.Building", null=True, blank=True, on_delete=models.PROTECT)
    owner_college = models.ForeignKey("core.College", null=True, blank=True, on_delete=models.PROTECT, related_name="owned_rooms")
    owner_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_rooms")
    is_active = models.BooleanField(default=True)
    # Legacy type is mirrored from the authoritative category for old readers.
    room_type = models.CharField(max_length=40, blank=True, default="")
    capacity = models.PositiveIntegerField()
    restricted_to_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="restricted_rooms")

    class Meta:
        ordering = ["name"]
        permissions = [("activate_room", "Can activate or deactivate rooms")]
        constraints = [
            models.UniqueConstraint(Lower("code"), condition=~models.Q(code=""), name="room_code_case_unique"),
            models.CheckConstraint(condition=models.Q(owner_college__isnull=True) | models.Q(owner_department__isnull=True), name="room_single_owner_scope"),
            models.CheckConstraint(condition=models.Q(capacity__gte=0), name="room_capacity_nonnegative"),
        ]

    @property
    def college(self):
        return self.owner_department.college if self.owner_department_id else self.owner_college

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()
        if self.category_id:
            self.room_type = self.category.code
        if self.is_active:
            if self.owner_department_id and (not self.owner_department.is_active or not self.owner_department.college.is_active):
                raise ValidationError({"owner_department": "Choose an active department and college."})
            if self.owner_college_id and not self.owner_college.is_active:
                raise ValidationError({"owner_college": "Choose an active college."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Term(models.Model):
    class TermName(models.TextChoices):
        FIRST = "1st", "1st"
        SECOND = "2nd", "2nd"
        SUMMER = "summer", "Summer"

    academic_year = models.CharField(max_length=9)
    term_name = models.CharField(max_length=6, choices=TermName.choices)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["-academic_year", "term_name"]
        constraints = [models.UniqueConstraint(fields=["academic_year", "term_name"], name="unique_academic_term")]

    def __str__(self):
        return f"{self.academic_year} {self.term_name}"


class Block(models.Model):
    curriculum = models.ForeignKey(Curriculum, on_delete=models.CASCADE, related_name="blocks")
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="blocks")
    section_code = models.CharField(max_length=10)
    year_level = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["curriculum", "year_level", "section_code"]
        constraints = [models.UniqueConstraint(fields=["curriculum", "term", "section_code", "year_level"], name="unique_block_section_per_term")]

    @property
    def enrolled_count(self):
        return self.students.count()

    def __str__(self):
        return f"{self.curriculum.program.code}-{self.year_level}{self.section_code}"


class TimeSlot(models.Model):
    class DayOfWeek(models.TextChoices):
        MONDAY = "MON", "Monday"
        TUESDAY = "TUE", "Tuesday"
        WEDNESDAY = "WED", "Wednesday"
        THURSDAY = "THU", "Thursday"
        FRIDAY = "FRI", "Friday"
        SATURDAY = "SAT", "Saturday"

    day_of_week = models.CharField(max_length=3, choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ("day_of_week", "start_time", "end_time")
        constraints = [
            models.UniqueConstraint(
                fields=("day_of_week", "start_time", "end_time"),
                name="unique_time_slot",
            )
        ]

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "End time must be after start time."})

    def __str__(self):
        return f"{self.day_of_week} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class Assignment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"

    faculty = models.ForeignKey(Faculty, on_delete=models.PROTECT, related_name="assignments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="assignments")
    block = models.ForeignKey(Block, on_delete=models.PROTECT, related_name="assignments")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="assignments")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="assignments")
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, related_name="assignments")
    units_credited = models.DecimalField(max_digits=3, decimal_places=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_assignments",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    # Populated from time_slot by the database trigger in migration 0003.
    # These indexed values let PostgreSQL enforce overlap constraints without a join.
    day_of_week = models.CharField(max_length=3, editable=False)
    start_time = models.TimeField(editable=False)
    end_time = models.TimeField(editable=False)

    class Meta:
        ordering = ("term", "day_of_week", "start_time", "block")

    def clean(self):
        super().clean()
        from .services import validate_assignment

        validate_assignment(
            faculty=self.faculty,
            subject=self.subject,
            block=self.block,
            room=self.room,
            term=self.term,
            time_slot=self.time_slot,
            units_credited=self.units_credited,
            exclude_assignment_id=self.pk,
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject.code} / {self.block} / {self.time_slot}"


class AssignmentStatusLog(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="status_logs")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assignment_status_logs")
    old_status = models.CharField(max_length=20, choices=Assignment.Status.choices)
    new_status = models.CharField(max_length=20, choices=Assignment.Status.choices)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.assignment} unlocked by {self.changed_by}"

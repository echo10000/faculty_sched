from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from core.base import TrackedModel
from .intervals import DAYS


class CheckedRecord(TrackedModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


def fixed_identity(obj, fields):
    if obj.pk:
        old = type(obj).objects.get(pk=obj.pk)
        if any(getattr(old, field) != getattr(obj, field) for field in fields):
            raise ValidationError("The original term and organizational identity cannot be changed.")


class ClassSection(CheckedRecord):
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    program = models.ForeignKey("core.Program", null=True, blank=True, on_delete=models.PROTECT)
    code = models.CharField(max_length=40)
    year_level = models.PositiveSmallIntegerField(null=True, blank=True)
    expected_size = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code", "pk"]
        constraints = [models.UniqueConstraint(Lower("code"), "academic_term", "department", name="section_code_term_department")]

    def clean(self):
        self.code = self.code.strip().upper()
        fixed_identity(self, ("academic_term_id", "department_id"))
        if self.program_id and self.program.department_id != self.department_id:
            raise ValidationError({"program": "Program must belong to the section department."})

    def __str__(self):
        return f"{self.code} · {self.academic_term.code}"


class OfferingRequirement(CheckedRecord):
    subject_offering = models.OneToOneField("workloads.SubjectOffering", on_delete=models.PROTECT, related_name="scheduling_requirement")
    section = models.ForeignKey(ClassSection, on_delete=models.PROTECT, related_name="offering_requirements")
    room_type = models.ForeignKey("resources.RoomType", null=True, blank=True, on_delete=models.PROTECT)
    room_type_mandatory = models.BooleanField(default=False)
    capacity_is_hard = models.BooleanField(default=False)

    @property
    def academic_term(self):
        return self.subject_offering.academic_term

    @property
    def department(self):
        return self.subject_offering.department

    def clean(self):
        fixed_identity(self, ("subject_offering_id",))
        if self.subject_offering_id and self.section_id:
            if (self.section.academic_term_id, self.section.department_id) != (self.subject_offering.academic_term_id, self.subject_offering.department_id):
                raise ValidationError({"section": "Section must match the offering term and department."})
        if self.room_type_mandatory and not self.room_type_id:
            raise ValidationError({"room_type": "Choose a room type for a mandatory requirement."})

    def __str__(self):
        return f"{self.subject_offering} · {self.section.code}"


class Schedule(CheckedRecord):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"

    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    name = models.CharField(max_length=150)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, editable=False)
    validated_signature = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["-academic_term__start_date", "name", "pk"]
        permissions = [("validate_schedule", "Can validate a manual schedule")]
        constraints = [models.CheckConstraint(condition=models.Q(status__in=["draft", "validated"]), name="manual_schedule_known_status")]

    def clean(self):
        fixed_identity(self, ("academic_term_id", "department_id"))

    def __str__(self):
        return f"{self.name} · {self.academic_term.code}"


class WeeklyInterval(CheckedRecord):
    day_of_week = models.PositiveSmallIntegerField(choices=DAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    notes = models.TextField(blank=True)

    class Meta:
        abstract = True
        ordering = ["day_of_week", "start_time", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(start_time__lt=models.F("end_time")), name="%(class)s_ordered_times"),
            models.CheckConstraint(condition=models.Q(day_of_week__gte=1, day_of_week__lte=7), name="%(class)s_valid_weekday"),
        ]

    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({"end_time": "End time must be after start time; split overnight meetings across days."})


class RoomUnavailability(WeeklyInterval):
    room = models.ForeignKey("scheduling.Room", on_delete=models.PROTECT, related_name="unavailable_periods")
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)

    class Meta(WeeklyInterval.Meta):
        abstract = False
        constraints = WeeklyInterval.Meta.constraints + [models.UniqueConstraint(fields=["room", "academic_term", "day_of_week", "start_time", "end_time"], name="room_closure_unique")]

    @property
    def department(self):
        return self.room.owner_department

    @property
    def college(self):
        return self.room.college

    def clean(self):
        super().clean()
        fixed_identity(self, ("room_id", "academic_term_id"))

    def __str__(self):
        return f"{self.room.code} · {self.get_day_of_week_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"


class ScheduleEntry(WeeklyInterval):
    schedule = models.ForeignKey(Schedule, on_delete=models.PROTECT, related_name="entries")
    assignment = models.ForeignKey("workloads.FacultySubjectAssignment", on_delete=models.PROTECT, related_name="meetings")
    room = models.ForeignKey("scheduling.Room", on_delete=models.PROTECT, related_name="meetings")
    meeting_type = models.CharField(max_length=12, choices=[("lecture", "Lecture"), ("laboratory", "Laboratory")])

    class Meta(WeeklyInterval.Meta):
        abstract = False
        constraints = WeeklyInterval.Meta.constraints + [models.CheckConstraint(condition=models.Q(meeting_type__in=["lecture", "laboratory"]), name="meeting_known_type")]

    @property
    def academic_term(self):
        return self.schedule.academic_term

    @property
    def department(self):
        return self.schedule.department

    @property
    def faculty(self):
        return self.assignment.faculty

    @property
    def offering(self):
        return self.assignment.subject_offering

    def clean(self):
        super().clean()
        fixed_identity(self, ("schedule_id",))
        if self.assignment_id and self.schedule_id:
            if self.assignment.subject_offering.academic_term_id != self.schedule.academic_term_id:
                raise ValidationError({"assignment": "Assignment and schedule must belong to the same academic term."})
            if self.assignment.subject_offering.department_id != self.schedule.department_id or self.faculty.home_department_id != self.schedule.department_id:
                raise ValidationError({"assignment": "Assignment must belong to the schedule department."})

    def __str__(self):
        return f"{self.offering.subject.code} · {self.get_day_of_week_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

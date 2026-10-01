from decimal import Decimal
import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import Func
from django.db.models.functions import Lower
from django.utils import timezone

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


def new_revision_token():
    return uuid.uuid4().hex


class ScheduleFamily(CheckedRecord):
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["-academic_term__start_date", "name", "pk"]

    def clean(self):
        fixed_identity(self, ("academic_term_id", "department_id"))

    def __str__(self):
        return f"{self.name} · {self.academic_term.code}"


class Schedule(CheckedRecord):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        UNDER_REVIEW = "under_review", "Under review"
        NEEDS_REVISION = "needs_revision", "Needs revision"
        APPROVED = "approved", "Published"

    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    family = models.ForeignKey(ScheduleFamily, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField(default=1)
    parent_version = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="revisions")
    revision_token = models.CharField(max_length=32, default=new_revision_token, editable=False)
    name = models.CharField(max_length=150)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, editable=False)
    validated_signature = models.CharField(max_length=64, blank=True, editable=False)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="submitted_schedules")
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_revision_token = models.CharField(max_length=32, blank=True, editable=False)
    submitted_signature = models.CharField(max_length=64, blank=True, editable=False)
    submitted_warning_codes = models.JSONField(default=list, editable=False)
    submitted_active_schedule = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        ordering = ["-academic_term__start_date", "name", "pk"]
        permissions = [
            ("validate_schedule", "Can validate a manual schedule"),
            ("generate_schedule", "Can generate a schedule"),
            ("finalize_schedule", "Can finalize and publish a schedule"),
            ("submit_schedule", "Can submit a schedule for review"),
            ("review_schedule", "Can review a submitted schedule"),
            ("approve_schedule", "Can approve a submitted schedule"),
            ("revise_schedule", "Can revise an approved schedule"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=["draft", "validated", "under_review", "needs_revision", "approved"]), name="manual_schedule_known_status"),
            models.CheckConstraint(condition=models.Q(version_number__gt=0), name="schedule_version_positive"),
            models.UniqueConstraint(fields=["family", "version_number"], name="schedule_family_version_unique"),
        ]

    def clean(self):
        fixed_identity(self, ("academic_term_id", "department_id"))
        if self.family_id and (self.family.academic_term_id != self.academic_term_id or self.family.department_id != self.department_id):
            raise ValidationError({"family": "Schedule family must match the term and department."})
        if self.parent_version_id and self.parent_version.family_id != self.family_id:
            raise ValidationError({"parent_version": "Parent version must belong to the same family."})

    def save(self, *args, **kwargs):
        if not self.family_id:
            from django.db import transaction
            with transaction.atomic():
                self.family = ScheduleFamily.objects.create(
                    academic_term_id=self.academic_term_id,
                    department_id=self.department_id,
                    name=self.name,
                    created_by=self.created_by,
                )
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

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
    MEETING_TYPES = [("lecture", "Lecture"), ("laboratory", "Laboratory")]

    schedule = models.ForeignKey(Schedule, on_delete=models.PROTECT, related_name="entries")
    assignment = models.ForeignKey("workloads.FacultySubjectAssignment", on_delete=models.PROTECT, related_name="meetings")
    room = models.ForeignKey("scheduling.Room", on_delete=models.PROTECT, related_name="meetings")
    meeting_type = models.CharField(max_length=12, choices=MEETING_TYPES)
    is_locked = models.BooleanField(default=True)
    generation_run = models.ForeignKey(
        "ScheduleGenerationRun",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="generated_entries",
    )

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


class AssignmentMeetingRequirement(CheckedRecord):
    assignment = models.ForeignKey(
        "workloads.FacultySubjectAssignment",
        on_delete=models.PROTECT,
        related_name="meeting_requirements",
    )
    meeting_type = models.CharField(max_length=12, choices=ScheduleEntry.MEETING_TYPES)
    meetings_per_week = models.PositiveSmallIntegerField()
    duration_minutes = models.PositiveIntegerField()

    class Meta:
        ordering = ["assignment", "meeting_type", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "meeting_type"],
                name="assignment_meeting_type_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(meetings_per_week__gt=0),
                name="meeting_requirement_count_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="meeting_requirement_duration_positive",
            ),
        ]

    @property
    def academic_term(self):
        return self.assignment.subject_offering.academic_term

    @property
    def department(self):
        return self.assignment.subject_offering.department

    def clean(self):
        super().clean()
        if (
            not self.assignment_id
            or self.meeting_type not in dict(ScheduleEntry.MEETING_TYPES)
            or type(self.meetings_per_week) is not int
            or self.meetings_per_week <= 0
            or type(self.duration_minutes) is not int
            or self.duration_minutes <= 0
        ):
            return
        try:
            offering = self.assignment.subject_offering
        except ObjectDoesNotExist:
            return
        component_hours = getattr(offering, f"{self.meeting_type}_hours")
        required_minutes = component_hours * self.assignment.share * 60
        if Decimal(self.meetings_per_week * self.duration_minutes) != required_minutes:
            raise ValidationError(
                "Meeting count and duration must equal this assignment's component minutes."
            )

    def __str__(self):
        return f"{self.assignment} · {self.get_meeting_type_display()}"


class SchedulingConfiguration(CheckedRecord):
    MAX_SOLVER_TIME_LIMIT_SECONDS = 300
    MAX_WORKER_COUNT = 64
    WEIGHT_FIELDS = (
        "faculty_preference_weight",
        "faculty_gap_weight",
        "section_gap_weight",
        "meeting_distribution_weight",
        "room_fit_weight",
    )

    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    allowed_weekdays = models.JSONField()
    earliest_start = models.TimeField()
    latest_end = models.TimeField()
    slot_increment_minutes = models.PositiveSmallIntegerField()
    solver_time_limit_seconds = models.PositiveIntegerField()
    random_seed = models.IntegerField()
    worker_count = models.PositiveSmallIntegerField()
    faculty_preference_weight = models.PositiveIntegerField(default=0)
    faculty_gap_weight = models.PositiveIntegerField(default=0)
    section_gap_weight = models.PositiveIntegerField(default=0)
    meeting_distribution_weight = models.PositiveIntegerField(default=0)
    room_fit_weight = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-academic_term__start_date", "department", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_term", "department"],
                name="scheduling_configuration_term_department_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(earliest_start__lt=models.F("latest_end")),
                name="scheduling_configuration_ordered_times",
            ),
            models.CheckConstraint(
                condition=models.Q(slot_increment_minutes__gt=0),
                name="scheduling_configuration_slot_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    solver_time_limit_seconds__gt=0,
                    solver_time_limit_seconds__lte=300,
                ),
                name="scheduling_configuration_time_limit",
            ),
            models.CheckConstraint(
                condition=models.Q(worker_count__gt=0, worker_count__lte=64),
                name="scheduling_configuration_worker_count",
            ),
            models.CheckConstraint(
                condition=models.Q(faculty_preference_weight__gte=0),
                name="scheduling_faculty_preference_weight_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(faculty_gap_weight__gte=0),
                name="scheduling_faculty_gap_weight_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(section_gap_weight__gte=0),
                name="scheduling_section_gap_weight_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(meeting_distribution_weight__gte=0),
                name="scheduling_meeting_distribution_weight_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(room_fit_weight__gte=0),
                name="scheduling_room_fit_weight_nonnegative",
            ),
        ]

    @staticmethod
    def _minutes_from_midnight(value):
        return value.hour * 60 + value.minute

    def clean(self):
        super().clean()
        fixed_identity(self, ("academic_term_id", "department_id"))
        errors = {}

        weekdays = self.allowed_weekdays
        if not isinstance(weekdays, list) or not weekdays:
            errors["allowed_weekdays"] = "Choose at least one weekday from 1 through 7."
        elif any(type(day) is not int or day < 1 or day > 7 for day in weekdays):
            errors["allowed_weekdays"] = "Weekdays must be integers from 1 through 7."
        elif len(set(weekdays)) != len(weekdays):
            errors["allowed_weekdays"] = "Weekdays cannot contain duplicates."
        else:
            self.allowed_weekdays = sorted(weekdays)

        increment = self.slot_increment_minutes
        if type(increment) is not int or increment <= 0:
            errors["slot_increment_minutes"] = "Slot increment must be a positive integer."

        start = self.earliest_start
        end = self.latest_end
        if start and (start.second or start.microsecond):
            errors["earliest_start"] = "The scheduling window must use whole minutes."
        if end and (end.second or end.microsecond):
            errors["latest_end"] = "The scheduling window must use whole minutes."
        if start and end:
            if start >= end:
                errors["latest_end"] = "Latest end must be after earliest start on the same day."
            if type(increment) is int and increment > 0:
                start_minute = self._minutes_from_midnight(start)
                end_minute = self._minutes_from_midnight(end)
                if start_minute % increment:
                    errors["earliest_start"] = "Earliest start must align to the slot grid from midnight."
                if end_minute % increment:
                    errors["latest_end"] = "Latest end must align to the slot grid from midnight."
                if end_minute > start_minute and (end_minute - start_minute) % increment:
                    errors["latest_end"] = "The scheduling window must contain a whole number of slots."

        if (
            type(self.solver_time_limit_seconds) is not int
            or not 1 <= self.solver_time_limit_seconds <= self.MAX_SOLVER_TIME_LIMIT_SECONDS
        ):
            errors["solver_time_limit_seconds"] = "Solver time limit must be from 1 through 300 seconds."
        if type(self.worker_count) is not int or not 1 <= self.worker_count <= self.MAX_WORKER_COUNT:
            errors["worker_count"] = "Worker count must be from 1 through 64."
        for field_name in self.WEIGHT_FIELDS:
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                errors[field_name] = "Objective weights must be nonnegative integers."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.department} · {self.academic_term.code}"


class ScheduleGenerationRun(TrackedModel):
    class Strategy(models.TextChoices):
        FILL_GAPS = "FILL_GAPS", "Fill gaps"
        REPLACE_UNLOCKED = "REPLACE_UNLOCKED", "Replace unlocked meetings"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        PROPOSAL_READY = "PROPOSAL_READY", "Proposal ready"
        ACCEPTED = "ACCEPTED", "Accepted"
        DISCARDED = "DISCARDED", "Discarded"
        INPUT_INVALID = "INPUT_INVALID", "Input invalid"
        INFEASIBLE = "INFEASIBLE", "Infeasible"
        STALE = "STALE", "Stale"
        VALIDATION_FAILED = "VALIDATION_FAILED", "Validation failed"
        FAILED = "FAILED", "Failed"

    class SolverStatus(models.TextChoices):
        OPTIMAL = "OPTIMAL", "Optimal"
        FEASIBLE = "FEASIBLE", "Feasible"
        INFEASIBLE = "INFEASIBLE", "Infeasible"
        MODEL_INVALID = "MODEL_INVALID", "Model invalid"
        UNKNOWN = "UNKNOWN", "Unknown"

    schedule = models.ForeignKey(Schedule, on_delete=models.PROTECT, related_name="generation_runs")
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_schedule_generation_runs",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accepted_schedule_generation_runs",
    )
    strategy = models.CharField(max_length=20, choices=Strategy.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    solver_status = models.CharField(max_length=20, choices=SolverStatus.choices, blank=True, default="")
    requested_at = models.DateTimeField(default=timezone.now, editable=False)
    started_at = models.DateTimeField(null=True, blank=True, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True, editable=False)
    accepted_at = models.DateTimeField(null=True, blank=True, editable=False)
    discarded_at = models.DateTimeField(null=True, blank=True, editable=False)
    solver_time_limit_seconds = models.PositiveIntegerField(null=True, blank=True, editable=False)
    runtime_seconds = models.FloatField(null=True, blank=True, editable=False)
    objective_value = models.FloatField(null=True, blank=True, editable=False)
    best_bound = models.FloatField(null=True, blank=True, editable=False)
    configuration_snapshot = models.JSONField(default=dict, editable=False)
    input_summary = models.JSONField(default=dict, editable=False)
    source_signature = models.CharField(max_length=64, blank=True, editable=False)
    proposed_meetings = models.JSONField(default=list, editable=False)
    penalty_breakdown = models.JSONField(default=dict, editable=False)
    diagnostics = models.JSONField(default=list, editable=False)
    solver_statistics = models.JSONField(default=dict, editable=False)
    proposed_meeting_count = models.PositiveIntegerField(default=0, editable=False)
    accepted_meeting_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(strategy__in=["FILL_GAPS", "REPLACE_UNLOCKED"]),
                name="generation_run_known_strategy",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "PENDING",
                        "RUNNING",
                        "PROPOSAL_READY",
                        "ACCEPTED",
                        "DISCARDED",
                        "INPUT_INVALID",
                        "INFEASIBLE",
                        "STALE",
                        "VALIDATION_FAILED",
                        "FAILED",
                    ]
                ),
                name="generation_run_known_status",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    solver_status__in=[
                        "",
                        "OPTIMAL",
                        "FEASIBLE",
                        "INFEASIBLE",
                        "MODEL_INVALID",
                        "UNKNOWN",
                    ]
                ),
                name="generation_run_known_solver_status",
            ),
            models.CheckConstraint(
                condition=models.Q(solver_time_limit_seconds__isnull=True)
                | models.Q(solver_time_limit_seconds__gt=0, solver_time_limit_seconds__lte=300),
                name="generation_run_time_limit",
            ),
            models.CheckConstraint(
                condition=models.Q(proposed_meeting_count__gte=0),
                name="generation_run_proposed_count_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(accepted_meeting_count__gte=0),
                name="generation_run_accepted_count_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.schedule} · {self.get_strategy_display()} · {self.status}"


class ScheduleWorkflowEvent(models.Model):
    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        RESUBMITTED = "resubmitted", "Resubmitted"
        RETURNED = "returned", "Returned for revision"
        APPROVED = "approved", "Approved"
        REPLACED = "replaced", "Official schedule replaced"
        REVISED = "revised", "Revision created"
        FINALIZED = "finalized", "Finalized and published"

    schedule = models.ForeignKey(Schedule, on_delete=models.PROTECT, related_name="workflow_events")
    action = models.CharField(max_length=16, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="schedule_workflow_events")
    revision_token = models.CharField(max_length=32)
    remarks = models.TextField(blank=True)
    warning_codes = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(action__in=["submitted", "resubmitted", "returned", "approved", "replaced", "revised", "finalized"]), name="schedule_workflow_known_action")]


class ScheduleApprovalSnapshot(models.Model):
    schedule = models.OneToOneField(Schedule, on_delete=models.PROTECT, related_name="approval_snapshot")
    revision_token = models.CharField(max_length=32)
    dependency_signature = models.CharField(max_length=64)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="schedule_approval_snapshots")
    approved_at = models.DateTimeField(default=timezone.now, editable=False)
    payload = models.JSONField()


class ActiveSchedule(models.Model):
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    schedule = models.ForeignKey(Schedule, on_delete=models.PROTECT, related_name="active_selections")
    selected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="selected_official_schedules")
    selected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["academic_term", "department"], name="active_schedule_term_department_unique")]


class BookingTimeRange(Func):
    output_field = DateTimeRangeField()

    def as_sql(self, compiler, connection, **extra_context):
        start, start_params = compiler.compile(self.source_expressions[0])
        end, end_params = compiler.compile(self.source_expressions[1])
        return f"tsrange(DATE '2000-01-01' + {start}, DATE '2000-01-01' + {end}, '[)')", [*start_params, *end_params]


class OfficialResourceBooking(models.Model):
    schedule_entry = models.ForeignKey(ScheduleEntry, on_delete=models.PROTECT, related_name="official_bookings")
    booking_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    faculty = models.ForeignKey("faculty.Faculty", on_delete=models.PROTECT)
    room = models.ForeignKey("scheduling.Room", on_delete=models.PROTECT)
    section = models.ForeignKey(ClassSection, on_delete=models.PROTECT)

    class Meta:
        ordering = ["booking_date", "start_time", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(start_time__lt=models.F("end_time")), name="official_booking_ordered_times"),
            models.UniqueConstraint(fields=["schedule_entry", "booking_date"], name="official_booking_entry_date_unique"),
            ExclusionConstraint(name="official_faculty_booking_no_overlap", expressions=[("faculty", "="), ("booking_date", "="), (BookingTimeRange("start_time", "end_time"), "&&")]),
            ExclusionConstraint(name="official_room_booking_no_overlap", expressions=[("room", "="), ("booking_date", "="), (BookingTimeRange("start_time", "end_time"), "&&")]),
            ExclusionConstraint(name="official_section_booking_no_overlap", expressions=[("section", "="), ("booking_date", "="), (BookingTimeRange("start_time", "end_time"), "&&")]),
        ]

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.conf import settings

from core.base import TrackedModel
from django.contrib.postgres.constraints import ExclusionConstraint
from .db_functions import AvailabilityRange


class CapacityFields(TrackedModel):
    recommended_load = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    maximum_load = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(condition=models.Q(recommended_load__isnull=True) | models.Q(recommended_load__gte=0), name="%(app_label)s_%(class)s_rec_positive"),
            models.CheckConstraint(condition=models.Q(maximum_load__isnull=True) | models.Q(maximum_load__gte=0), name="%(app_label)s_%(class)s_max_positive"),
            models.CheckConstraint(condition=models.Q(recommended_load__isnull=True) | models.Q(maximum_load__isnull=True) | models.Q(recommended_load__lte=models.F("maximum_load")), name="%(app_label)s_%(class)s_ordered"),
        ]

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class WorkloadPolicy(CapacityFields):
    enforce_maximum = models.BooleanField(null=True, blank=True, help_text="Yes blocks assignments above the effective maximum. No warns. Unknown inherits; if unconfigured, warnings only.")
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    college = models.ForeignKey("core.College", null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT)
    lecture_weight = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], help_text="Workload units per lecture unit. Blank inherits a broader policy.")
    laboratory_weight = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], help_text="Workload units per laboratory unit. Blank inherits a broader policy.")
    notes = models.TextField(blank=True)

    class Meta(CapacityFields.Meta):
        ordering = ["academic_term", "pk"]
        constraints = CapacityFields.Meta.constraints + [
            models.CheckConstraint(condition=models.Q(college__isnull=True) | models.Q(department__isnull=True), name="policy_single_scope"),
            models.CheckConstraint(condition=models.Q(lecture_weight__isnull=True) | models.Q(lecture_weight__gte=0), name="policy_lecture_nonnegative"),
            models.CheckConstraint(condition=models.Q(laboratory_weight__isnull=True) | models.Q(laboratory_weight__gte=0), name="policy_lab_nonnegative"),
            models.UniqueConstraint(fields=["academic_term"], condition=models.Q(college__isnull=True, department__isnull=True), name="policy_term_institution_unique"),
            models.UniqueConstraint(fields=["academic_term", "college"], condition=models.Q(college__isnull=False), name="policy_term_college_unique"),
            models.UniqueConstraint(fields=["academic_term", "department"], condition=models.Q(department__isnull=False), name="policy_term_department_unique"),
        ]

    def __str__(self):
        return f"{self.academic_term} / {self.department or self.college or 'Institution'}"


class FacultyTermCapacity(CapacityFields):
    enforce_maximum = models.BooleanField(null=True, blank=True, help_text="Override maximum enforcement for this faculty and term; unknown inherits policy.")
    faculty = models.ForeignKey("faculty.Faculty", on_delete=models.PROTECT, related_name="term_capacities")
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    notes = models.TextField(blank=True)

    class Meta(CapacityFields.Meta):
        ordering = ["academic_term", "pk"]
        constraints = CapacityFields.Meta.constraints + [
            models.UniqueConstraint(fields=["faculty", "academic_term"], name="capacity_faculty_term_unique"),
        ]

    def clean(self):
        super().clean()
        if self.faculty_id and self.academic_term_id:
            from .services import resolve_capacity
            resolve_capacity(self.faculty, self.academic_term, override=self)

    def __str__(self):
        return f"{self.faculty} / {self.academic_term}"


def validate_active_term(term):
    if not term.is_active or not term.academic_year.is_active or not term.semester.is_active:
        raise ValidationError("Choose an active academic term, year and semester.")


def validate_active_faculty(faculty):
    if not faculty.is_active or not faculty.home_department.is_active or not faculty.home_department.college.is_active:
        raise ValidationError("Choose active faculty in an active department and college.")


class TermRecord(TrackedModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class FacultyAvailability(TermRecord):
    class Kind(models.TextChoices):
        AVAILABLE = "available", "Available"
        UNAVAILABLE = "unavailable", "Unavailable (hard restriction)"
        PREFERRED = "preferred", "Preferred"

    DAYS = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"), (5, "Friday"), (6, "Saturday"), (7, "Sunday")]
    faculty = models.ForeignKey("faculty.Faculty", on_delete=models.PROTECT, related_name="availability_records")
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    day_of_week = models.PositiveSmallIntegerField(choices=DAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    availability_type = models.CharField(max_length=15, choices=Kind.choices)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["day_of_week", "start_time", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(start_time__lt=models.F("end_time")), name="availability_ordered_times"),
            models.CheckConstraint(condition=models.Q(day_of_week__gte=1, day_of_week__lte=7), name="availability_valid_day"),
            models.CheckConstraint(condition=models.Q(availability_type__in=["available", "unavailable", "preferred"]), name="availability_valid_kind"),
            ExclusionConstraint(name="availability_same_kind_no_overlap", expressions=[("faculty", "="), ("academic_term", "="), ("day_of_week", "="), ("availability_type", "="), (AvailabilityRange("start_time", "end_time"), "&&")]),
            ExclusionConstraint(name="availability_hard_soft_no_overlap", expressions=[("faculty", "="), ("academic_term", "="), ("day_of_week", "="), (models.Case(models.When(availability_type="unavailable", then=models.Value(True)), default=models.Value(False), output_field=models.BooleanField()), "<>"), (AvailabilityRange("start_time", "end_time"), "&&")]),
        ]

    @property
    def department(self):
        return self.faculty.home_department

    def clean(self):
        super().clean()
        if self.faculty_id:
            validate_active_faculty(self.faculty)
        if self.academic_term_id:
            validate_active_term(self.academic_term)
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if (self.faculty_id, self.academic_term_id) != (original.faculty_id, original.academic_term_id):
                raise ValidationError("Faculty and term cannot be changed. Remove this record and create a new one.")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({"end_time": "Start time must be earlier than end time. Split overnight availability into separate days."})
        if self.faculty_id and self.academic_term_id and self.start_time and self.end_time:
            overlaps = type(self).objects.filter(faculty_id=self.faculty_id, academic_term_id=self.academic_term_id, day_of_week=self.day_of_week, start_time__lt=self.end_time, end_time__gt=self.start_time).exclude(pk=self.pk)
            if self.availability_type != self.Kind.UNAVAILABLE:
                overlaps = overlaps.filter(models.Q(availability_type=self.availability_type) | models.Q(availability_type=self.Kind.UNAVAILABLE))
            if overlaps.exists():
                raise ValidationError("This interval duplicates or overlaps incompatible availability. Preferred intervals may overlap available intervals only.")

    def __str__(self):
        return f"{self.faculty} · {self.get_day_of_week_display()} {self.start_time}–{self.end_time}"


class SubjectOffering(TermRecord):
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT, related_name="term_offerings")
    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    code = models.SlugField(max_length=30, default="MAIN", help_text="Offering identifier within this subject and term, e.g. MAIN or A.")
    lecture_units = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    laboratory_units = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    lecture_hours = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    laboratory_hours = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["subject__code", "code", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["subject", "academic_term", "code"], name="offering_subject_term_code_unique"),
            *[models.CheckConstraint(condition=models.Q(**{f"{field}__gte": 0}), name=f"offering_{field}_nonnegative") for field in ("lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours")],
        ]

    @property
    def total_units(self):
        return self.lecture_units + self.laboratory_units

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()
        if self.academic_term_id and (not self.pk or self.is_active):
            validate_active_term(self.academic_term)
        if self.subject_id and self.department_id:
            if self.department_id != self.subject.owning_department_id:
                raise ValidationError("The offering department must match the catalog subject department.")
            if self.is_active and (not self.subject.is_active or not self.department.is_active or not self.department.college.is_active):
                raise ValidationError("Active offerings require an active subject and organization.")
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if (self.subject_id, self.academic_term_id, self.department_id) != (original.subject_id, original.academic_term_id, original.department_id):
                raise ValidationError("An offering's subject, term and department are permanent. Create another offering instead.")
            if self.teaching_assignments.exists() and any(getattr(self, f) != getattr(original, f) for f in ("lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours")):
                raise ValidationError("Remove teaching assignments before changing offering units or hours.")

    def __str__(self):
        return f"{self.subject.code} · {self.code} · {self.academic_term.code}"


class FacultySubjectAssignment(TermRecord):
    faculty = models.ForeignKey("faculty.Faculty", on_delete=models.PROTECT, related_name="teaching_assignments")
    subject_offering = models.ForeignKey(SubjectOffering, on_delete=models.PROTECT, related_name="teaching_assignments")
    share = models.DecimalField(max_digits=5, decimal_places=2, default=1, help_text="Fraction of this offering's teaching, greater than zero and at most 1. Total shares cannot exceed 1.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["faculty__last_name", "pk"]
        permissions = [("view_workload", "Can monitor faculty teaching workloads")]
        constraints = [
            models.UniqueConstraint(fields=["faculty", "subject_offering"], name="faculty_offering_assignment_unique"),
            models.CheckConstraint(condition=models.Q(share__gt=0, share__lte=1), name="assignment_valid_share"),
        ]

    @property
    def academic_term(self):
        return self.subject_offering.academic_term

    @property
    def department(self):
        return self.faculty.home_department

    def clean(self):
        super().clean()
        if self.faculty_id:
            validate_active_faculty(self.faculty)
        if self.subject_offering_id:
            offering = self.subject_offering
            validate_active_term(offering.academic_term)
            if not offering.is_active or not offering.subject.is_active or not offering.department.is_active or not offering.department.college.is_active:
                raise ValidationError("Choose an active offering, catalog subject and organization.")
            if self.faculty_id and self.faculty.home_department_id != offering.department_id:
                raise ValidationError("Faculty and offering must belong to the same department. Cross-department teaching is not enabled.")
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if (self.faculty_id, self.subject_offering_id) != (original.faculty_id, original.subject_offering_id):
                raise ValidationError("Faculty and offering cannot be changed. Remove the assignment and create a new one.")

    def __str__(self):
        return f"{self.faculty} → {self.subject_offering}"


class WorkloadRecommendationRun(models.Model):
    """A proposal-only balancing run; acceptance is handled by the service layer."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
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

    academic_term = models.ForeignKey("academics.AcademicTerm", on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="initiated_workload_recommendation_runs",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="accepted_workload_recommendation_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    raw_solver_status = models.CharField(max_length=20, choices=SolverStatus.choices, blank=True, default="")
    source_signature = models.CharField(max_length=64, blank=True, default="")
    proposed_assignments = models.JSONField(default=list)
    input_summary = models.JSONField(default=dict)
    comparison = models.JSONField(default=dict)
    diagnostics = models.JSONField(default=list)
    solver_stats = models.JSONField(default=dict)
    accepted_at = models.DateTimeField(null=True, blank=True)
    discarded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [("generate_workloadrecommendation", "Can generate faculty workload recommendations")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[
                    "PENDING", "PROPOSAL_READY", "ACCEPTED", "DISCARDED",
                    "INPUT_INVALID", "INFEASIBLE", "STALE", "VALIDATION_FAILED", "FAILED",
                ]),
                name="workload_recommendation_known_status",
            ),
            models.CheckConstraint(
                condition=models.Q(raw_solver_status__in=[
                    "", "OPTIMAL", "FEASIBLE", "INFEASIBLE", "MODEL_INVALID", "UNKNOWN",
                ]),
                name="workload_recommendation_known_solver_status",
            ),
        ]

    def __str__(self):
        return f"{self.department} · {self.academic_term.code} · {self.status}"

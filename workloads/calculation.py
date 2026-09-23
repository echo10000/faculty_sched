from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import FacultySubjectAssignment, FacultyTermCapacity, WorkloadPolicy
from .services import resolve_capacity

STATUSES = ("UNCONFIGURED", "UNDERLOAD", "WITHIN_LOAD", "AT_CAPACITY", "OVERLOAD")


def resolve_policy(faculty, term):
    """Extend Phase 2 resolution with provenance/enforcement without changing its contract."""
    values = resolve_capacity(faculty, term)
    override = FacultyTermCapacity.objects.filter(faculty=faculty, academic_term=term).first()
    policies = list(WorkloadPolicy.objects.filter(academic_term=term).filter(
        Q(department=faculty.home_department) | Q(college=faculty.home_department.college) |
        Q(department__isnull=True, college__isnull=True)))
    policies.sort(key=lambda p: 0 if p.department_id else 1 if p.college_id else 2)
    levels = [(override, "Faculty term override"), (faculty, "Faculty baseline")] + [
        (p, "Department workload policy" if p.department_id else "College workload policy" if p.college_id else "Institution workload policy") for p in policies]
    sources = {}
    for field in values:
        candidates = levels if field.endswith("load") else levels[2:]
        sources[field] = next((label for obj, label in candidates if obj and getattr(obj, field, None) is not None), "Not configured")
    mode_levels = [levels[0], *levels[2:]]
    configured = next(((obj.enforce_maximum, label) for obj, label in mode_levels if obj and obj.enforce_maximum is not None), (False, "Unconfigured: warnings only"))
    return {**values, "sources": sources, "enforce_maximum": configured[0], "enforcement_source": configured[1]}


def weighted_load_for(offering, share, policy):
    """Return the policy-weighted load of an offering share without rounding it."""
    load = Decimal("0")
    for units, weight in (
        (offering.lecture_units, policy["lecture_weight"]),
        (offering.laboratory_units, policy["laboratory_weight"]),
    ):
        if units and weight is None:
            return None
        load += units * share * (weight if weight is not None else Decimal("0"))
    return load


def calculate_workload(faculty, term, *, proposed=None, exclude_pk=None, assignments_override=None):
    """Current-policy teaching load, never schedule hours or legacy assignments.

    Offering hours are weekly contact hours. Preserve Decimal arithmetic without
    per-assignment rounding; UI formatting does not affect hard-limit checks.
    """
    assignments = (list(assignments_override) if assignments_override is not None else
                   list(FacultySubjectAssignment.objects.filter(faculty=faculty, subject_offering__academic_term=term).exclude(pk=exclude_pk).select_related("subject_offering__subject", "subject_offering__academic_term")))
    if proposed is not None:
        assignments.append(proposed)
    warnings = []
    try:
        policy = resolve_policy(faculty, term)
    except ValidationError as error:
        policy = {**dict.fromkeys(["recommended_load", "maximum_load", "lecture_weight", "laboratory_weight"]), "sources": {}, "enforce_maximum": False, "enforcement_source": "Invalid configuration"}
        warnings.extend(error.messages)
    totals = {field: sum((getattr(a.subject_offering, field) * a.share for a in assignments), Decimal("0")) for field in ("lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours")}
    load = Decimal("0")
    for assignment in assignments:
        contribution = weighted_load_for(assignment.subject_offering, assignment.share, policy)
        if contribution is None:
            load = None
            warnings.append("Workload unit weights are not configured for all assigned teaching components.")
            break
        load += contribution
    recommended, maximum = policy["recommended_load"], policy["maximum_load"]
    status = "UNCONFIGURED"
    if recommended is None and maximum is None:
        warnings.append("Workload policy not configured: no recommended or maximum load.")
    elif load is not None:
        if maximum is not None and load > maximum:
            status = "OVERLOAD"
            warnings.append("Assigned workload exceeds the configured maximum.")
        elif maximum is not None and load == maximum:
            status = "AT_CAPACITY"
        elif recommended is not None and load < recommended:
            status = "UNDERLOAD"
        elif maximum is None and recommended is not None and load > recommended:
            status = "OVERLOAD"
            warnings.append("Assigned workload exceeds the recommended target; no hard maximum is configured.")
        else:
            status = "WITHIN_LOAD"
            if recommended is not None and load > recommended:
                warnings.append("Assigned workload is above the recommended target and below the maximum.")
    denominator = maximum if maximum is not None else recommended
    return {
        "faculty": faculty, "term": term, "assignments": assignments, "assignment_count": len(assignments),
        **totals, "teaching_units": totals["lecture_units"] + totals["laboratory_units"],
        "teaching_hours": totals["lecture_hours"] + totals["laboratory_hours"], "assigned_load": load,
        "policy": policy, "remaining_capacity": maximum - load if maximum is not None and load is not None else None,
        "utilization": load / denominator * 100 if denominator is not None and denominator > 0 and load is not None else None,
        "status": status, "warnings": list(dict.fromkeys(warnings)),
    }


def validate_workload(faculty, term, proposed, exclude_pk=None):
    # Invalid inherited limits must never turn into a permissive assignment save.
    policy = resolve_policy(faculty, term)
    result = calculate_workload(faculty, term, proposed=proposed, exclude_pk=exclude_pk)
    if policy["enforce_maximum"]:
        if policy["maximum_load"] is None:
            raise ValidationError("Hard-limit enforcement is enabled but no maximum is configured.")
        if result["assigned_load"] is None:
            raise ValidationError("Configure the lecture/laboratory weights before assigning against a hard maximum.")
        if result["assigned_load"] > policy["maximum_load"]:
            raise ValidationError("The resulting workload exceeds the configured hard maximum.")
    return result

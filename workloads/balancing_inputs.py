"""Build a scoped, immutable optimization input from the Phase 3 workload records.

Only workload allocation is modeled here. Existing timetable dependencies pin an
offering's allocation; they are never edited by a workload recommendation.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from faculty.models import Faculty, FacultyQualification
from timetabling.models import AssignmentMeetingRequirement, ScheduleEntry

from .balancing_solver import (
    AssignmentOption,
    BalancingInput,
    FacultyLoad,
    FacultyShare,
    OfferingDemand,
)
from .calculation import calculate_workload, resolve_policy, weighted_load_for
from .models import FacultySubjectAssignment, SubjectOffering


# Two decimal places each for units, policy weights, and assignment shares.
LOAD_SCALE = 1_000_000


@dataclass(frozen=True)
class PreparedBalancingInput:
    solver_input: BalancingInput | None
    current_assignments: tuple[dict, ...]
    mutable_offering_ids: tuple[int, ...]
    fixed_offering_ids: tuple[int, ...]
    faculty: tuple[Faculty, ...]
    offerings: tuple[SubjectOffering, ...]
    policies: dict[int, dict]
    diagnostics: tuple[dict, ...]


def _issue(code, message, *, faculty_id=None, offering_id=None):
    result = {"code": code, "severity": "ERROR", "message": message}
    if faculty_id is not None:
        result["faculty_id"] = faculty_id
    if offering_id is not None:
        result["offering_id"] = offering_id
    return result


def _scaled(value):
    scaled = Decimal(value) * LOAD_SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError("Workload precision exceeds the configured exact solver scale.")
    return int(scaled)


def _centis(share):
    cents = Decimal(share) * 100
    if cents != cents.to_integral_value() or not 0 < cents <= 100:
        raise ValueError("Assignment shares must be hundredths between 0 and 1.")
    return int(cents)


def is_faculty_eligible_for_offering(faculty, offering):
    """Mirror the Phase 3 active, term, and home-department assignment rules."""
    return (
        faculty.is_active
        and faculty.home_department_id == offering.department_id
        and faculty.home_department.is_active
        and faculty.home_department.college.is_active
        and offering.is_active
        and offering.subject.is_active
        and offering.subject.owning_department_id == offering.department_id
        and offering.department.is_active
        and offering.department.college.is_active
        and offering.academic_term.is_active
        and offering.academic_term.academic_year.is_active
        and offering.academic_term.semester.is_active
    )


def _option(offering, shares, current, policies, qualifications):
    """Make one full allocation, using the authoritative weighted-load formula."""
    allocation = tuple(sorted((faculty_id, amount) for faculty_id, amount in shares.items() if amount))
    parts = []
    for faculty_id, amount in allocation:
        load = weighted_load_for(offering, Decimal(amount) / 100, policies[faculty_id])
        if load is None:
            raise ValueError("Workload policy weights are incomplete.")
        parts.append(FacultyShare(faculty_id, amount, _scaled(load)))
    return AssignmentOption(
        offering_id=offering.pk,
        shares=tuple(parts),
        changed=allocation != current,
        qualification_match_count=sum((faculty_id, offering.subject_id) in qualifications for faculty_id, _ in allocation),
    )


def prepare_balancing_input(*, academic_term, department):
    """Validate source readiness and construct a deterministic CP-SAT input.

    Missing qualification rows are unknown, not evidence of ineligibility: the
    authoritative Phase 3 assignment workflow does not require those rows.
    """
    diagnostics = []
    if not academic_term.is_active or not academic_term.academic_year.is_active or not academic_term.semester.is_active:
        diagnostics.append(_issue("INACTIVE_TERM", "The academic term, year, and semester must be active."))
    if not department.is_active or not department.college.is_active:
        diagnostics.append(_issue("INACTIVE_DEPARTMENT", "The department and college must be active."))

    faculty = tuple(Faculty.objects.filter(home_department=department, is_active=True).select_related(
        "home_department__college",
    ).order_by("pk"))
    if not faculty:
        diagnostics.append(_issue("NO_ACTIVE_FACULTY", "The department has no active faculty to receive workload."))
    all_offerings = tuple(SubjectOffering.objects.filter(
        academic_term=academic_term, department=department,
    ).select_related("subject", "academic_term__academic_year", "academic_term__semester", "department__college").order_by("pk"))
    offerings = tuple(item for item in all_offerings if item.is_active)
    if not offerings:
        diagnostics.append(_issue("NO_ACTIVE_OFFERINGS", "The department has no active subject offerings in this term."))

    for offering in all_offerings:
        if offering.is_active and (not offering.subject.is_active or offering.subject.owning_department_id != department.pk):
            diagnostics.append(_issue("INVALID_OFFERING", "An active offering has an inactive or cross-department subject.", offering_id=offering.pk))

    assignments = tuple(FacultySubjectAssignment.objects.filter(
        subject_offering__academic_term=academic_term,
        subject_offering__department=department,
    ).select_related("faculty__home_department", "subject_offering").order_by("subject_offering_id", "faculty_id", "pk"))
    current_assignments = tuple({
        "assignment_id": item.pk,
        "offering_id": item.subject_offering_id,
        "faculty_id": item.faculty_id,
        "share": str(item.share),
    } for item in assignments)
    by_offering = {item.pk: [] for item in all_offerings}
    for assignment in assignments:
        by_offering[assignment.subject_offering_id].append(assignment)
        if not assignment.subject_offering.is_active:
            diagnostics.append(_issue("INACTIVE_ASSIGNED_OFFERING", "An inactive offering still has a teaching assignment.", offering_id=assignment.subject_offering_id))
        if not is_faculty_eligible_for_offering(assignment.faculty, assignment.subject_offering):
            diagnostics.append(_issue("INVALID_ASSIGNMENT_FACULTY", "An assignment uses inactive or out-of-department faculty.", faculty_id=assignment.faculty_id, offering_id=assignment.subject_offering_id))
        try:
            _centis(assignment.share)
        except ValueError:
            diagnostics.append(_issue("INVALID_SHARE", "An assignment has an invalid teaching share.", offering_id=assignment.subject_offering_id))

    assignment_ids = [item.pk for item in assignments]
    protected_ids = set(ScheduleEntry.objects.filter(assignment_id__in=assignment_ids).values_list("assignment__subject_offering_id", flat=True))
    protected_ids.update(AssignmentMeetingRequirement.objects.filter(assignment_id__in=assignment_ids).values_list("assignment__subject_offering_id", flat=True))
    fixed_offering_ids = tuple(item.pk for item in offerings if item.pk in protected_ids)
    mutable_offering_ids = tuple(item.pk for item in offerings if item.pk not in protected_ids)
    active_faculty_ids = {item.pk for item in faculty}
    for offering in offerings:
        share_total = sum((assignment.share for assignment in by_offering[offering.pk]), Decimal("0"))
        if share_total > 1:
            diagnostics.append(_issue("OVER_ALLOCATED", "Existing assignment shares exceed 100% of an offering.", offering_id=offering.pk))
        if offering.pk in protected_ids and share_total != 1:
            diagnostics.append(_issue("INCOMPLETE_PROTECTED_OFFERING", "An offering linked to timetable entries or meeting requirements must already have complete teaching shares.", offering_id=offering.pk))
        elif 0 < share_total < 1:
            diagnostics.append(_issue("INCOMPLETE_SHARES", "Existing teaching shares do not fully cover this offering. Complete or remove them before generating recommendations.", offering_id=offering.pk))

    policies = {}
    loads = []
    selected_ids = {item.pk for item in offerings}
    all_faculty_assignments = tuple(FacultySubjectAssignment.objects.filter(
        faculty_id__in=active_faculty_ids,
        subject_offering__academic_term=academic_term,
    ).select_related("subject_offering").order_by("pk"))
    for assignment in all_faculty_assignments:
        if assignment.subject_offering.department_id != department.pk:
            diagnostics.append(_issue(
                "CROSS_SCOPE_ASSIGNMENT", "A department faculty member has an assignment in another department.",
                faculty_id=assignment.faculty_id, offering_id=assignment.subject_offering_id,
            ))
    for member in faculty:
        try:
            policy = resolve_policy(member, academic_term)
        except ValidationError as error:
            diagnostics.append(_issue("INVALID_POLICY", "; ".join(error.messages), faculty_id=member.pk))
            continue
        policies[member.pk] = policy
        for component, weight in (("lecture_units", "lecture_weight"), ("laboratory_units", "laboratory_weight")):
            if policy[weight] is None and any(getattr(item, component) > 0 for item in offerings):
                diagnostics.append(_issue("MISSING_WEIGHT", f"Configure a {weight.replace('_', ' ')} for all candidate faculty.", faculty_id=member.pk))
        target = policy["recommended_load"] if policy["recommended_load"] is not None and policy["recommended_load"] > 0 else policy["maximum_load"]
        if target is None or target <= 0:
            diagnostics.append(_issue("MISSING_TARGET", "Configure a positive recommended or maximum teaching load.", faculty_id=member.pk))
            continue
        if policy["enforce_maximum"] and policy["maximum_load"] is None:
            diagnostics.append(_issue("MISSING_HARD_MAXIMUM", "Hard-limit enforcement needs an effective maximum load.", faculty_id=member.pk))
            continue
        baseline_assignments = [item for item in all_faculty_assignments if item.faculty_id == member.pk and item.subject_offering_id not in selected_ids]
        baseline = calculate_workload(member, academic_term, assignments_override=baseline_assignments)["assigned_load"]
        if baseline is None:
            diagnostics.append(_issue("UNCONFIGURED_BASELINE", "Current workload cannot be calculated until component weights are configured.", faculty_id=member.pk))
            continue
        try:
            loads.append(FacultyLoad(
                faculty_id=member.pk,
                baseline_load_scaled=_scaled(baseline),
                target_load_scaled=_scaled(target),
                hard_max_scaled=_scaled(policy["maximum_load"]) if policy["enforce_maximum"] else None,
            ))
        except ValueError as error:
            diagnostics.append(_issue("INVALID_PRECISION", str(error), faculty_id=member.pk))

    if diagnostics:
        return PreparedBalancingInput(None, current_assignments, mutable_offering_ids, fixed_offering_ids, faculty, offerings, policies, tuple(diagnostics))

    qualifications = set(FacultyQualification.objects.filter(
        faculty_id__in=active_faculty_ids, subject_id__in=[item.subject_id for item in offerings],
    ).values_list("faculty_id", "subject_id"))
    demands = []
    for offering in offerings:
        current = tuple(sorted((item.faculty_id, _centis(item.share)) for item in by_offering[offering.pk]))
        current_map = dict(current)
        candidate_allocations = []
        if offering.pk in protected_ids:
            candidate_allocations.append(current_map)
        else:
            if sum(current_map.values()) == 100:
                candidate_allocations.append(current_map)
            for member in faculty:
                if not is_faculty_eligible_for_offering(member, offering):
                    continue
                candidate_allocations.append({member.pk: 100})
        seen = set()
        options = []
        for allocation in candidate_allocations:
            key = tuple(sorted(allocation.items()))
            if key in seen:
                continue
            seen.add(key)
            try:
                options.append(_option(offering, allocation, current, policies, qualifications))
            except ValueError as error:
                diagnostics.append(_issue("INVALID_CONTRIBUTION", str(error), offering_id=offering.pk))
        if not options:
            diagnostics.append(_issue("NO_ELIGIBLE_ALLOCATION", "No complete eligible allocation exists for this offering.", offering_id=offering.pk))
        demands.append(OfferingDemand(offering_id=offering.pk, options=tuple(options)))

    if diagnostics:
        return PreparedBalancingInput(None, current_assignments, mutable_offering_ids, fixed_offering_ids, faculty, offerings, policies, tuple(diagnostics))
    return PreparedBalancingInput(
        BalancingInput(faculty=tuple(loads), offerings=tuple(demands)),
        current_assignments, mutable_offering_ids, fixed_offering_ids,
        faculty, offerings, policies, (),
    )

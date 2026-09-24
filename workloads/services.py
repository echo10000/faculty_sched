from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import FacultyTermCapacity, WorkloadPolicy

NOT_PROVIDED = object()


def resolve_capacity(faculty, academic_term, *, override=NOT_PROVIDED, policies=None):
    """Resolve configuration only; no assigned loads or schedules are calculated.

    Missing values inherit independently. Explicit zero is a configured value.
    Faculty baseline and term overrides apply only to limits, not unit weights.
    """
    if override is NOT_PROVIDED:
        override = FacultyTermCapacity.objects.filter(faculty=faculty, academic_term=academic_term).first()
    if policies is None:
        policies = list(WorkloadPolicy.objects.filter(academic_term=academic_term).filter(
            Q(department=faculty.home_department) | Q(college=faculty.home_department.college) |
            Q(department__isnull=True, college__isnull=True)))
    else:
        policies = [policy for policy in policies if (
            policy.department_id == faculty.home_department_id
            or policy.college_id == faculty.home_department.college_id
            or (policy.department_id is None and policy.college_id is None)
        )]
    policies.sort(key=lambda item: 0 if item.department_id else 1 if item.college_id else 2)
    result = {}
    for field in ("recommended_load", "maximum_load", "lecture_weight", "laboratory_weight"):
        sources = ([override, faculty] if field.endswith("load") else []) + policies
        result[field] = next((getattr(source, field) for source in sources if source is not None and getattr(source, field) is not None), None)
    if result["recommended_load"] is not None and result["maximum_load"] is not None and result["recommended_load"] > result["maximum_load"]:
        raise ValidationError("The inherited recommended load exceeds the maximum. Configure compatible limits at the faculty, term, or policy level.")
    return result

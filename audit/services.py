from .models import AuditLog


def record_event(action, *, actor=None, obj=None, details=None):
    """Call inside the domain transaction; callers supply only non-secret metadata."""
    from accounts.permissions import profile_for
    profile = profile_for(actor) if actor else None
    department = (getattr(obj, "home_department", None) or getattr(obj, "owning_department", None) or getattr(obj, "owner_department", None) or getattr(obj, "department", None)) if obj else None
    if obj and obj._meta.label_lower == "workloads.facultytermcapacity":
        department = obj.faculty.home_department
    college = department.college if department else (getattr(obj, "owner_college", None) or getattr(obj, "college", None)) if obj else None
    metadata = dict(details or {})
    if obj and obj._meta.app_label == "workloads" and getattr(obj, "academic_term", None):
        metadata.setdefault("academic_term_id", obj.academic_term.pk)
    return AuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        action=action,
        object_type=obj._meta.label if obj else "",
        object_id=str(obj.pk) if obj else "",
        college_id=college.pk if college else profile.college_id if profile else None,
        department_id=department.pk if department else profile.department_id if profile else None,
        details=metadata,
    )

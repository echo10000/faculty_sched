from django.core.exceptions import PermissionDenied

from .models import AdminProfile


READ_PERMISSIONS = {
    "core.view_dashboard", "core.view_college", "core.view_department",
    "academics.view_academicyear", "academics.view_academicterm", "academics.view_semester",
}

RESOURCE_PERMISSIONS = {
    f"{app}.{action}_{model}"
    for app, model in [("faculty", "faculty"), ("academics", "subject"), ("scheduling", "room")]
    for action in ("view", "add", "change", "activate")
}

TEACHING_PERMISSIONS = {
    f"workloads.{action}_{model}"
    for model in ("facultyavailability", "subjectoffering", "facultysubjectassignment")
    for action in ("view", "add", "change", "delete")
    if model != "subjectoffering" or action != "delete"
} | {"workloads.view_workload"}

BALANCING_PERMISSIONS = {
    "workloads.generate_workloadrecommendation",
    "workloads.view_workloadrecommendationrun",
}

TIMETABLE_PERMISSIONS = {
    f"timetabling.{action}_{model}"
    for model in ("schedule", "scheduleentry", "classsection", "offeringrequirement", "roomunavailability")
    for action in ("view", "add", "change", "delete")
    if action != "delete" or model in ("scheduleentry", "roomunavailability")
} | {"timetabling.validate_schedule"}

TIMETABLE_PERMISSIONS |= {
    "timetabling.generate_schedule",
    "timetabling.view_assignmentmeetingrequirement",
    "timetabling.add_assignmentmeetingrequirement",
    "timetabling.change_assignmentmeetingrequirement",
    "timetabling.view_schedulingconfiguration",
    "timetabling.add_schedulingconfiguration",
    "timetabling.change_schedulingconfiguration",
    "timetabling.view_schedulegenerationrun",
}

REPORT_PERMISSIONS = {"core.export_report"}

# Retained grants support historical service callers. New role defaults use
# finalization and do not require the retired separate-reviewer workflow.
SCHEDULE_EDITOR_PERMISSIONS = {
    "timetabling.submit_schedule",
    "timetabling.revise_schedule",
}
SCHEDULE_REVIEWER_PERMISSIONS = {"timetabling.review_schedule"}
SCHEDULE_APPROVER_PERMISSIONS = {"timetabling.approve_schedule"}
SCHEDULE_FINALIZER_PERMISSIONS = {"timetabling.finalize_schedule"}
FACULTY_PORTAL_PERMISSIONS = {"faculty.view_own_teaching"}
STAFF_PERMISSIONS = (READ_PERMISSIONS | RESOURCE_PERMISSIONS | TEACHING_PERMISSIONS
                    | TIMETABLE_PERMISSIONS | BALANCING_PERMISSIONS | REPORT_PERMISSIONS
                    | {"timetabling.revise_schedule"} | SCHEDULE_FINALIZER_PERMISSIONS)


def faculty_for(user):
    """The existing Faculty.user link is independent of an administrative role."""
    if not user.is_authenticated or not user.is_active:
        return None
    if AdminProfile.objects.filter(user=user, is_enabled=False).exists():
        return None
    from faculty.models import Faculty
    return Faculty.objects.select_related("home_department__college").filter(
        user=user, is_active=True, home_department__is_active=True,
        home_department__college__is_active=True,
    ).first()


def faculty_only(user):
    profile = profile_for(user)
    return not is_system_admin(user) and bool(
        (profile and profile.role == AdminProfile.Role.FACULTY)
        or (not profile and faculty_for(user))
    )


def can_sign_in(user):
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    profile = profile_for(user)
    if profile:
        return profile.role != AdminProfile.Role.FACULTY or faculty_for(user) is not None
    # A disabled or inactive organizational profile never falls back to faculty.
    return not AdminProfile.objects.filter(user=user).exists() and faculty_for(user) is not None


def profile_for(user):
    if not user.is_authenticated or not user.is_active:
        return None
    # Query on each request/check so a revoked scope never survives relation caching.
    profile = AdminProfile.objects.select_related("college", "department__college").filter(user=user, is_enabled=True).first()
    if not profile:
        return None
    if profile.college_id and not profile.college.is_active:
        return None
    if profile.department_id and (not profile.department.is_active or not profile.department.college.is_active):
        return None
    return profile


def is_system_admin(user):
    if not user.is_authenticated or not user.is_active:
        return False
    profile = profile_for(user)
    return user.is_superuser or bool(profile and profile.role == AdminProfile.Role.SUPER_ADMIN)


def role_permissions(profile):
    if not profile:
        return set()
    if profile.role == AdminProfile.Role.SUPER_ADMIN:
        from django.contrib.auth.models import Permission
        return {f"{app}.{code}" for app, code in Permission.objects.values_list("content_type__app_label", "codename")}
    if profile.role == AdminProfile.Role.STAFF:
        return STAFF_PERMISSIONS
    if profile.role == AdminProfile.Role.FACULTY:
        return FACULTY_PORTAL_PERMISSIONS
    return set()


def require_access(user, permission):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Sign in with an active account.")
    if not can_sign_in(user):
        raise PermissionDenied("An enabled organizational profile is required. Contact your system administrator.")
    if not user.has_perm(permission):
        raise PermissionDenied("Your account has not been granted this permission.")


def scoped_colleges(user, queryset):
    if is_system_admin(user):
        return queryset
    profile = profile_for(user)
    if not profile or profile.role == AdminProfile.Role.FACULTY:
        raise PermissionDenied("An enabled administrator profile is required.")
    college_id = profile.college_id or profile.department.college_id
    return queryset.filter(pk=college_id)


def department_scoped_queryset(user, queryset, department_field="department"):
    if is_system_admin(user):
        return queryset
    profile = profile_for(user)
    if not profile or profile.role == AdminProfile.Role.FACULTY:
        raise PermissionDenied("An enabled administrator profile is required.")
    if profile.department_id:
        return queryset.filter(**{department_field: profile.department_id})
    college_lookup = "college_id" if department_field == "pk" else f"{department_field}__college_id"
    return queryset.filter(**{college_lookup: profile.college_id})

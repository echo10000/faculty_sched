from django.conf import settings

from accounts.permissions import is_system_admin, profile_for
from .models import SystemSetting


def navigation(request):
    user = request.user
    profile = profile_for(user)
    authorized = user.is_authenticated and (user.is_superuser or profile is not None)
    links = []
    for permission, label, route in [
        ("core.view_dashboard", "Overview", "home"),
        ("faculty.view_faculty", "Faculty", "faculty-management:list"),
        ("workloads.view_facultyavailability", "Faculty availability", "workloads:availability"),
        ("workloads.view_workload", "Workload monitoring", "workloads:monitor"),
        ("academics.view_subject", "Subjects", "subjects:list"),
        ("workloads.view_subjectoffering", "Subject offerings", "workloads:offerings"),
        ("workloads.view_facultysubjectassignment", "Faculty assignments", "workloads:assignments"),
        ("scheduling.view_room", "Rooms", "rooms:list"),
        ("timetabling.view_schedule", "Manual schedules", "timetabling:schedules"),
        ("timetabling.view_classsection", "Class sections", "timetabling:sections"),
        ("timetabling.view_offeringrequirement", "Offering requirements", "timetabling:requirements"),
        ("timetabling.view_roomunavailability", "Room unavailability", "timetabling:closures"),
        ("core.view_college", "Colleges", "college-list"),
        ("core.view_department", "Departments", "department-list"),
        ("academics.view_academicterm", "Academic calendar", "academic-calendar"),
    ]:
        if authorized and user.has_perm(permission):
            if permission.startswith(("workloads.", "timetabling.")) and not user.has_perm("academics.view_academicterm"):
                continue
            match = request.resolver_match
            active = bool(match and (match.view_name == route or (":" in route and match.namespace == route.split(":")[0])))
            if match and match.namespace == "workloads" and route.startswith("workloads:"):
                section = route.split(":")[1]
                active = match.url_name.startswith(section) or (section == "monitor" and match.url_name == "faculty")
            if match and match.namespace == "timetabling" and route.startswith("timetabling:"):
                section = route.split(":")[1]
                active = match.url_name.startswith(section) or (section == "schedules" and match.url_name in ("timetable", "conflicts", "entries-add", "entries-edit", "entries-delete", "validate"))
            links.append({"label": label, "route": route, "active": active})
    name = SystemSetting.objects.filter(key="institution_name").values_list("value", flat=True).first() or settings.INSTITUTION_NAME
    scope = "Institution-wide" if user.is_authenticated and is_system_admin(user) else str(profile.department or profile.college) if profile else "No assigned scope"
    return {
        "institution_name": name, "navigation_links": links, "access_scope": scope,
        "role_label": "System Admin" if user.is_authenticated and user.is_superuser else profile.get_role_display() if profile else "Account",
        "can_administer": authorized and user.is_staff and is_system_admin(user),
    }

from django.conf import settings

from accounts.permissions import is_system_admin, profile_for
from timetabling.generation_inputs import GENERATION_PERMISSIONS
from .models import SystemSetting


def navigation(request):
    user = request.user
    profile = profile_for(user)
    authorized = user.is_authenticated and (user.is_superuser or profile is not None)
    granted = user.get_all_permissions() if authorized else set()
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
        ("timetabling.view_assignmentmeetingrequirement", "Meeting requirements", "timetabling:meeting-requirements"),
        ("timetabling.view_schedulingconfiguration", "Scheduling configurations", "timetabling:configurations"),
        ("timetabling.view_roomunavailability", "Room unavailability", "timetabling:closures"),
        ("core.view_college", "Colleges", "college-list"),
        ("core.view_department", "Departments", "department-list"),
        ("academics.view_academicterm", "Academic calendar", "academic-calendar"),
    ]:
        if permission in granted:
            if permission.startswith(("workloads.", "timetabling.")) and "academics.view_academicterm" not in granted:
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
    if authorized:
        match = request.resolver_match
        special = []
        from reporting.services import available_catalog
        if available_catalog(user, granted=granted):
            special.append(("Reports", "reporting:index", {"index", "detail", "print", "export"}))
        if "workloads.generate_workloadrecommendation" in granted and "academics.view_academicterm" in granted:
            special.append(("Workload balancing", "workloads:balancing", {"balancing"}))
        if "workloads.view_workloadrecommendationrun" in granted and "academics.view_academicterm" in granted:
            special.append(("Recommendation history", "workloads:balancing-runs", {
                "balancing-runs", "balancing-run-detail", "balancing-run-accept", "balancing-run-discard"}))
        if all(permission in granted for permission in GENERATION_PERMISSIONS):
            special.append(("Automated generator", "timetabling:generator", {"generator"}))
        if "timetabling.view_schedulegenerationrun" in granted and "academics.view_academicterm" in granted:
            special.append(("Generation history", "timetabling:generation-runs", {
                "generation-runs", "generation-run-detail", "generation-run-accept", "generation-run-discard"}))
        if all(permission in granted for permission in ("timetabling.submit_schedule", "timetabling.view_schedule", "academics.view_academicterm")):
            special.append(("My schedules", "timetabling:my-schedules", {"my-schedules"}))
        if all(permission in granted for permission in ("timetabling.review_schedule", "timetabling.view_schedule", "academics.view_academicterm")):
            special.append(("Pending review", "timetabling:review-queue", {"review-queue"}))
        if "timetabling.view_schedule" in granted and "academics.view_academicterm" in granted:
            special.append(("Official schedules", "timetabling:official-schedules", {"official-schedules"}))
        for label, route, names in special:
            links.append({"label": label, "route": route,
                          "active": bool(match and match.namespace == route.split(":")[0] and match.url_name in names)})
    name = SystemSetting.objects.filter(key="institution_name").values_list("value", flat=True).first() or settings.INSTITUTION_NAME
    scope = "Institution-wide" if user.is_authenticated and is_system_admin(user) else str(profile.department or profile.college) if profile else "No assigned scope"
    return {
        "institution_name": name, "navigation_links": links, "access_scope": scope,
        "role_label": "System Admin" if user.is_authenticated and user.is_superuser else profile.get_role_display() if profile else "Account",
        "can_administer": authorized and user.is_staff and is_system_admin(user),
    }

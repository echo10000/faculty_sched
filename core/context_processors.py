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
        ("workloads.view_workload", "Faculty workload", "workloads:monitor"),
        ("academics.view_subject", "Subjects", "subjects:list"),
        ("workloads.view_subjectoffering", "Classes offered", "workloads:offerings"),
        ("workloads.view_facultysubjectassignment", "Teaching assignments", "workloads:assignments"),
        ("scheduling.view_room", "Rooms", "rooms:list"),
        ("timetabling.view_schedule", "Manage schedules", "timetabling:schedules"),
        ("timetabling.view_classsection", "Class sections", "timetabling:sections"),
        ("timetabling.view_offeringrequirement", "Class scheduling requirements", "timetabling:requirements"),
        ("timetabling.view_assignmentmeetingrequirement", "Class meeting requirements", "timetabling:meeting-requirements"),
        ("timetabling.view_schedulingconfiguration", "Schedule generation settings", "timetabling:configurations"),
        ("timetabling.view_roomunavailability", "Blocked room times", "timetabling:closures"),
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
                active = match.url_name.startswith(section) or (section == "schedules" and match.url_name in ("timetable", "conflicts", "entries-add", "entries-edit", "entries-delete", "validate", "schedule-review", "schedule-history"))
            if match and route in ("faculty-management:list", "subjects:list", "rooms:list"):
                active = active or match.namespace == route.split(":")[0]
            if match and route in ("college-list", "department-list"):
                active = active or match.url_name == route.replace("-list", "-detail")
            links.append({"label": label, "route": route, "active": active})
    if authorized:
        match = request.resolver_match
        special = []
        from reporting.services import available_catalog
        if available_catalog(user, granted=granted):
            special.append(("Reports", "reporting:index", {"index", "detail", "print", "export"}))
        if "workloads.generate_workloadrecommendation" in granted and "academics.view_academicterm" in granted:
            special.append(("Balance faculty workload", "workloads:balancing", {"balancing"}))
        if "workloads.view_workloadrecommendationrun" in granted and "academics.view_academicterm" in granted:
            special.append(("Workload recommendation history", "workloads:balancing-runs", {
                "balancing-runs", "balancing-run-detail", "balancing-run-accept", "balancing-run-discard"}))
        if all(permission in granted for permission in GENERATION_PERMISSIONS):
            special.append(("Generate schedule", "timetabling:generator", {"generator"}))
        if "timetabling.view_schedulegenerationrun" in granted and "academics.view_academicterm" in granted:
            special.append(("Generated schedule history", "timetabling:generation-runs", {
                "generation-runs", "generation-run-detail", "generation-run-accept", "generation-run-discard"}))
        if all(permission in granted for permission in ("timetabling.submit_schedule", "timetabling.view_schedule", "academics.view_academicterm")):
            special.append(("My schedules", "timetabling:my-schedules", {"my-schedules"}))
        if all(permission in granted for permission in ("timetabling.review_schedule", "timetabling.view_schedule", "academics.view_academicterm")):
            special.append(("Schedules for review", "timetabling:review-queue", {"review-queue"}))
        if "timetabling.view_schedule" in granted and "academics.view_academicterm" in granted:
            special.append(("Prepare schedule", "timetabling:prepare", {"prepare"}))
            special.append(("Official schedules", "timetabling:official-schedules", {"official-schedules"}))
        for label, route, names in special:
            links.append({"label": label, "route": route,
                          "active": bool(match and match.namespace == route.split(":")[0] and match.url_name in names)})
    # Icons are presentation metadata; permissions and destinations stay above.
    icons = {
        "home": "grid", "faculty-management:list": "person",
        "workloads:availability": "clock", "workloads:monitor": "chart",
        "workloads:assignments": "swap", "workloads:balancing": "chart",
        "workloads:balancing-runs": "history", "subjects:list": "book",
        "workloads:offerings": "layers", "timetabling:sections": "people",
        "timetabling:requirements": "checklist", "timetabling:meeting-requirements": "clock",
        "timetabling:schedules": "calendar", "timetabling:prepare": "checklist", "timetabling:generator": "spark",
        "timetabling:configurations": "sliders", "rooms:list": "building",
        "timetabling:closures": "block", "timetabling:generation-runs": "history",
        "timetabling:my-schedules": "calendar", "timetabling:review-queue": "review",
        "timetabling:official-schedules": "check", "reporting:index": "file",
        "college-list": "building", "department-list": "people",
        "academic-calendar": "calendar",
    }
    for link in links:
        link["icon"] = icons.get(link["route"], "file")
    # Group only links that passed the existing permission checks above.
    overview = ("Overview", "grid", ("home",))
    faculty = ("Faculty & workload", "people", ("faculty-management:list", "workloads:availability", "workloads:assignments", "workloads:monitor", "workloads:balancing"))
    academic = ("Academic setup", "book", ("workloads:offerings", "timetabling:sections", "subjects:list", "rooms:list"))
    scheduling = ("Scheduling", "calendar", ("timetabling:prepare", "timetabling:schedules", "timetabling:generator", "timetabling:my-schedules"))
    review = ("Review & approval", "review", ("timetabling:review-queue", "timetabling:official-schedules"))
    reports = ("Reports", "file", ("reporting:index",))
    more = ("More & settings", "settings", ("timetabling:meeting-requirements", "timetabling:requirements", "timetabling:configurations", "timetabling:closures", "timetabling:generation-runs", "workloads:balancing-runs"))
    administration = ("Administration", "settings", ("academic-calendar", "college-list", "department-list"))
    role = profile.role if profile else ""
    if user.is_authenticated and is_system_admin(user):
        sections = (overview, administration, reports, faculty, academic, scheduling, review, more)
    elif role == "dean":
        sections = (overview, review, faculty, reports, scheduling, academic, more, administration)
    else:
        sections = (overview, scheduling, faculty, academic, review, reports, more, administration)
    navigation_groups = []
    for label, icon, routes in sections:
        items = sorted((link for link in links if link["route"] in routes),
                       key=lambda link: routes.index(link["route"]))
        if items:
            navigation_groups.append({"label": label, "icon": icon, "links": items,
                                      "active": any(link["active"] for link in items)})
    name = SystemSetting.objects.filter(key="institution_name").values_list("value", flat=True).first() or settings.INSTITUTION_NAME
    assigned_unit = profile.department or profile.college if profile else None
    scope = "Institution-wide" if user.is_authenticated and is_system_admin(user) else assigned_unit.name if assigned_unit else "No assigned scope"
    return {
        "institution_name": name, "navigation_links": links, "navigation_groups": navigation_groups, "access_scope": scope,
        "role_label": "System Admin" if user.is_authenticated and user.is_superuser else profile.get_role_display() if profile else "Account",
        "can_administer": authorized and user.is_staff and is_system_admin(user),
    }

from django.conf import settings
from django.urls import reverse

from accounts.permissions import can_sign_in, faculty_for, faculty_only, is_system_admin, profile_for
from timetabling.generation_inputs import GENERATION_PERMISSIONS
from .models import SystemSetting


def navigation(request):
    user = request.user
    profile = profile_for(user)
    authorized = user.is_authenticated and can_sign_in(user)
    granted = user.get_all_permissions() if authorized else set()
    links = []
    for permission, label, route in [
        ("core.view_dashboard", "Dashboard", "home"),
        ("faculty.view_faculty", "Faculty", "faculty-management:list"),
        ("workloads.view_facultyavailability", "Faculty availability", "workloads:availability"),
        ("workloads.view_workload", "Faculty workload", "workloads:monitor"),
        ("academics.view_subject", "Subjects", "subjects:list"),
        ("workloads.view_subjectoffering", "Classes offered", "workloads:offerings"),
        ("workloads.view_facultysubjectassignment", "Assignment records", "workloads:assignments"),
        ("workloads.view_facultysubjectassignment", "Teaching assignments", "workloads:section-assignments"),
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
            if route == "workloads:section-assignments" and not all(item in granted for item in (
                    "timetabling.view_classsection", "timetabling.view_offeringrequirement",
                    "workloads.view_subjectoffering", "academics.view_subject", "workloads.view_workload")):
                continue
            if permission.startswith(("workloads.", "timetabling.")) and "academics.view_academicterm" not in granted:
                continue
            match = request.resolver_match
            active = bool(match and (match.view_name == route or (":" in route and match.namespace == route.split(":")[0])))
            if match and match.namespace == "workloads" and route.startswith("workloads:"):
                section = route.split(":")[1]
                active = match.url_name.startswith(section) or (section == "monitor" and match.url_name == "faculty") or (section == "availability" and match.url_name == "faculty-availability")
            if match and match.namespace == "timetabling" and route.startswith("timetabling:"):
                section = route.split(":")[1]
                active = match.url_name.startswith(section) or (section == "schedules" and match.url_name in ("timetable", "conflicts", "entries-add", "entries-edit", "entries-delete", "validate", "schedule-review", "schedule-history", "unscheduled"))
                if section == "requirements" and match.url_name == "room-eligibility":
                    active = True
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
            if all(permission in granted for permission in ("timetabling.view_classsection",
                    "workloads.view_subjectoffering", "academics.view_subject")):
                special.append(("Generate by section", "timetabling:section-generator", {"section-generator"}))
        if "timetabling.view_schedulegenerationrun" in granted and "academics.view_academicterm" in granted:
            special.append(("Generated schedule history", "timetabling:generation-runs", {
                "generation-runs", "generation-run-detail", "generation-run-accept", "generation-run-discard"}))
        if all(permission in granted for permission in ("timetabling.finalize_schedule", "timetabling.view_schedule", "academics.view_academicterm")):
            special.append(("My Scheduling Work", "timetabling:my-schedules", {"my-schedules"}))
        if all(permission in granted for permission in ("timetabling.finalize_schedule", "timetabling.view_schedule", "academics.view_academicterm")):
            special.append(("Schedules to finalize", "timetabling:review-queue", {"review-queue"}))
        if "timetabling.view_schedule" in granted and "academics.view_academicterm" in granted:
            special.append(("Prepare Academic Schedule", "timetabling:prepare", {"prepare"}))
            special.append(("Official schedules", "timetabling:official-schedules", {"official-schedules"}))
        for label, route, names in special:
            links.append({"label": label, "route": route,
                          "active": bool(match and match.namespace == route.split(":")[0] and match.url_name in names)})
    person = faculty_for(user) if authorized else None
    if person:
        for label, route in [('My Teaching Schedule', 'faculty-portal:schedule'), ('My Teaching Load', 'faculty-portal:workload')]:
            links.append({'label': label, 'route': route, 'active': bool(request.resolver_match and request.resolver_match.view_name == route)})
    can_administer = authorized and user.is_staff and is_system_admin(user)
    if can_administer:
        links.append({"label": "System administration", "route": "admin:index", "active": False})
    # Icons are presentation metadata; permissions and destinations stay above.
    icons = {
        "home": "grid", "faculty-management:list": "person",
        "workloads:availability": "clock", "workloads:monitor": "chart",
        "workloads:assignments": "swap", "workloads:balancing": "chart",
        "workloads:section-assignments": "swap",
        "workloads:balancing-runs": "history", "subjects:list": "book",
        "workloads:offerings": "layers", "timetabling:sections": "people",
        "timetabling:requirements": "checklist", "timetabling:meeting-requirements": "clock",
        "timetabling:schedules": "calendar", "timetabling:prepare": "checklist", "timetabling:generator": "spark",
        "timetabling:section-generator": "spark",
        "timetabling:configurations": "sliders", "rooms:list": "building",
        "timetabling:closures": "block", "timetabling:generation-runs": "history",
        "timetabling:my-schedules": "calendar", "timetabling:review-queue": "review",
        "timetabling:official-schedules": "check", "reporting:index": "file",
        "college-list": "building", "department-list": "people",
        "academic-calendar": "calendar",
        "admin:index": "settings", "faculty-portal:schedule": "calendar",
        "faculty-portal:workload": "chart",
    }
    for link in links:
        link["icon"] = icons.get(link["route"], "file")
    # Group only links that passed the existing permission checks above.
    sections = (
        ("Dashboard", "grid", ("home",), True),
        ("Prepare Academic Schedule", "checklist", ("timetabling:prepare",), True),
        ("Faculty & Teaching", "people", (
            "faculty-management:list", "workloads:section-assignments", "workloads:monitor",
            "workloads:availability", "workloads:assignments", "workloads:balancing",
            "workloads:balancing-runs"), False),
        ("Schedules & Publication", "calendar", (
            "timetabling:schedules", "timetabling:section-generator", "timetabling:generator",
            "timetabling:generation-runs", "timetabling:my-schedules", "timetabling:review-queue",
            "timetabling:official-schedules"), False),
        ("Reports", "file", ("reporting:index",), True),
        ("Management & Settings", "settings", (
            "academic-calendar", "college-list", "department-list", "subjects:list",
            "workloads:offerings", "timetabling:sections", "rooms:list", "timetabling:requirements",
            "timetabling:meeting-requirements", "timetabling:closures", "timetabling:configurations",
            "admin:index"), False),
        ("My Teaching Schedule", "calendar", ("faculty-portal:schedule",), True),
        ("My Teaching Load", "chart", ("faculty-portal:workload",), True),
    )
    navigation_groups = []
    for label, icon, routes, direct in sections:
        items = sorted((link for link in links if link["route"] in routes),
                       key=lambda link: routes.index(link["route"]))
        if items:
            navigation_groups.append({"label": label, "icon": icon, "links": items, "direct": direct,
                                      "active": any(link["active"] for link in items)})
    breadcrumbs = []
    match = request.resolver_match
    current_link = next((link for link in links if link["active"]), None)
    if current_link and match and match.view_name != current_link["route"]:
        if "core.view_dashboard" in granted:
            breadcrumbs.append({"label": "Dashboard", "url": reverse("home")})
        breadcrumbs.append({"label": current_link["label"], "url": reverse(current_link["route"])})
        breadcrumbs.append({"label": "Current page", "url": None})
    name = SystemSetting.objects.filter(key="institution_name").values_list("value", flat=True).first() or settings.INSTITUTION_NAME
    assigned_unit = profile.department or profile.college if profile else None
    from academics.calendar import staff_uses_current_term, current_terms
    current_term_only = authorized and not faculty_only(user) and staff_uses_current_term(user)
    scope = "Institution-wide" if user.is_authenticated and is_system_admin(user) else assigned_unit.name if assigned_unit else str(person) if person else "No assigned scope"
    return {
        "breadcrumbs": breadcrumbs, "institution_name": name,
        "primary_navigation": [group["links"][0] for group in navigation_groups if group["direct"]],
        "navigation_links": [link for group in navigation_groups for link in group["links"]],
        "navigation_groups": navigation_groups, "access_scope": scope,
        "role_label": "Admin" if user.is_authenticated and user.is_superuser else profile.get_role_display() if profile else "Faculty" if person else "Account",
        "can_administer": can_administer,
        "current_term_only": current_term_only,
        "staff_current_term": current_terms().select_related("academic_year", "semester").first() if current_term_only else None,
    }

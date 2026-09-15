from django.contrib.admin import AdminSite

from accounts.permissions import is_system_admin


class FoundationAdminSite(AdminSite):
    site_header = "CampusLoad administration"
    site_title = "CampusLoad"
    index_title = "System administration"

    def register(self, model_or_iterable, admin_class=None, **options):
        # Keep legacy models/migrations intact without exposing unfinished modules.
        allowed = {"auth.user", "auth.group", "accounts.adminprofile", "core.college", "core.department",
                   "core.systemsetting", "academics.academicyear", "academics.academicterm", "academics.semester", "audit.auditlog",
                   "faculty.employmentcategory", "faculty.academicrank", "resources.roomtype", "resources.building",
                   "workloads.workloadpolicy", "workloads.facultytermcapacity"}
        models = [model_or_iterable] if hasattr(model_or_iterable, "_meta") else model_or_iterable
        for model in models:
            if model._meta.label_lower in allowed:
                super().register(model, admin_class, **options)

    def has_permission(self, request):
        return request.user.is_staff and is_system_admin(request.user)

from django.contrib import admin
from core.admin_mixins import AuditedAdmin
from faculty.models import AcademicRank, EmploymentCategory
from .models import Building, RoomType


class ReferenceAdmin(AuditedAdmin):
    list_display = ("name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "created_by")

    def has_delete_permission(self, request, obj=None):
        return False


for model in (Building, RoomType, EmploymentCategory, AcademicRank):
    admin.site.register(model, ReferenceAdmin)

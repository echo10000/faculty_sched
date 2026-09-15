from django.contrib import admin
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.models import User, Group
from core.admin_mixins import AuditedAdmin

from .models import AdminProfile


@admin.register(AdminProfile)
class AdminProfileAdmin(AuditedAdmin):
    readonly_fields = ("created_at", "updated_at")
    list_display = ("user", "role", "college", "department", "is_enabled")
    list_filter = ("role", "college", "department", "is_enabled")
    search_fields = ("user__username", "user__first_name", "user__last_name")


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class AuditedUserAdmin(AuditedAdmin, UserAdmin):
    readonly_fields = ("last_login", "date_joined")


@admin.register(Group)
class AuditedGroupAdmin(AuditedAdmin, GroupAdmin):
    readonly_fields = ()

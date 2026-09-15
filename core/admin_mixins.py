from django.contrib import admin

from audit.services import record_event


class AuditedAdmin(admin.ModelAdmin):
    readonly_fields = ("created_at", "updated_at", "created_by")

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by_id"):
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def log_addition(self, request, obj, message):
        record_event("record.create", actor=request.user, obj=obj)
        return super().log_addition(request, obj, message)

    def log_change(self, request, obj, message):
        # Django's admin change messages contain field names, not secret field values.
        record_event("record.update", actor=request.user, obj=obj, details={"changes": message})
        return super().log_change(request, obj, message)

    def log_deletions(self, request, queryset):
        for obj in queryset:
            record_event("record.delete", actor=request.user, obj=obj)
        return super().log_deletions(request, queryset)

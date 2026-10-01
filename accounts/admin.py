from django.contrib import admin
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.models import User, Group
from django.contrib.auth.forms import UserChangeForm
from django import forms
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from core.admin_mixins import AuditedAdmin
from faculty.models import Faculty
from audit.services import record_event

from .models import AdminProfile


@admin.register(AdminProfile)
class AdminProfileAdmin(AuditedAdmin):
    readonly_fields = ("created_at", "updated_at")
    list_display = ("user", "role", "college", "department", "is_enabled")
    list_filter = ("role", "college", "department", "is_enabled")
    search_fields = ("user__username", "user__first_name", "user__last_name")


admin.site.unregister(User)
admin.site.unregister(Group)


class LinkedUserChangeForm(UserChangeForm):
    faculty_record = forms.ModelChoiceField(queryset=Faculty.objects.none(), required=False,
        help_text="Optional teaching identity. Linking a Faculty record preserves this account's Admin or Staff role.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['faculty_record'].queryset = Faculty.objects.filter(Q(user__isnull=True) | Q(user=self.instance))
        self.fields['faculty_record'].initial = Faculty.objects.filter(user=self.instance).first()

    def clean_faculty_record(self):
        selected = self.cleaned_data['faculty_record']
        previous = Faculty.objects.filter(user=self.instance).first()
        if previous and (not selected or selected.pk != previous.pk) and AdminProfile.objects.filter(
            user=self.instance, role='faculty', is_enabled=True,
        ).exists():
            raise forms.ValidationError('Disable the Faculty profile before changing its teaching identity.')
        return selected


@admin.register(User)
class AuditedUserAdmin(AuditedAdmin, UserAdmin):
    readonly_fields = ("last_login", "date_joined")
    form = LinkedUserChangeForm
    fieldsets = UserAdmin.fieldsets + (("Teaching identity", {"fields": ("faculty_record",)}),)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if 'faculty_record' in form.cleaned_data:
            selected = form.cleaned_data['faculty_record']
            for previous in Faculty.objects.select_for_update().filter(user=obj):
                if selected and previous.pk == selected.pk:
                    continue
                # A Faculty-only enabled account must retain a teaching identity.
                if AdminProfile.objects.filter(user=obj, role='faculty', is_enabled=True).exists():
                    raise PermissionDenied('Disable the Faculty profile before unlinking its teaching identity.')
                previous.user = None
                previous.save()
                record_event('account.faculty_unlinked', actor=request.user, obj=obj, details={'faculty_id': previous.pk})
            if selected and selected.user_id != obj.pk:
                selected = Faculty.objects.select_for_update().get(pk=selected.pk)
                if selected.user_id is not None:
                    raise PermissionDenied('This Faculty record was linked to another account. Reload and select again.')
                selected.user = obj
                selected.save()
                record_event('account.faculty_linked', actor=request.user, obj=obj, details={'faculty_id': selected.pk})


@admin.register(Group)
class AuditedGroupAdmin(AuditedAdmin, GroupAdmin):
    readonly_fields = ()

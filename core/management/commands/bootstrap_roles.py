from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.permissions import READ_PERMISSIONS, RESOURCE_PERMISSIONS, TEACHING_PERMISSIONS, TIMETABLE_PERMISSIONS


class Command(BaseCommand):
    help = "Create initial permission bundles without overwriting existing group grants."

    @transaction.atomic
    def handle(self, *args, **options):
        for name, permissions in {
            "College Dean": READ_PERMISSIONS | RESOURCE_PERMISSIONS | TEACHING_PERMISSIONS | TIMETABLE_PERMISSIONS,
            "Department Chair": READ_PERMISSIONS | RESOURCE_PERMISSIONS | TEACHING_PERMISSIONS | TIMETABLE_PERMISSIONS,
            "Authorized Staff": {"core.view_dashboard"},
            "System Admin": READ_PERMISSIONS | {"core.view_systemsetting", "audit.view_auditlog"},
        }.items():
            group, created = Group.objects.get_or_create(name=name)
            if created:
                for name in permissions:
                    app, codename = name.split(".")
                    group.permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
        self.stdout.write(self.style.SUCCESS("Initial role bundles ready; existing custom grants preserved."))

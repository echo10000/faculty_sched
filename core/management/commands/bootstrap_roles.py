from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.permissions import (
    READ_PERMISSIONS, STAFF_PERMISSIONS, FACULTY_PORTAL_PERMISSIONS,
)


class Command(BaseCommand):
    help = "Create initial permission bundles without overwriting existing group grants."

    @transaction.atomic
    def handle(self, *args, **options):
        for name, permissions in {
            "Authorized Staff": STAFF_PERMISSIONS,
            "Faculty": FACULTY_PORTAL_PERMISSIONS,
            "Admin": READ_PERMISSIONS | {"core.view_systemsetting", "audit.view_auditlog"},
        }.items():
            group, _ = Group.objects.get_or_create(name=name)
            for permission_name in permissions:
                app, codename = permission_name.split(".")
                group.permissions.add(
                    Permission.objects.get(content_type__app_label=app, codename=codename)
                )
        self.stdout.write(self.style.SUCCESS("Initial role bundles ready; existing custom grants preserved."))

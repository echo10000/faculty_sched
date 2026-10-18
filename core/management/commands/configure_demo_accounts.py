"""Apply the simplified account setup without deleting account history."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.demo import create_faculty_demo_accounts, save_demo_credentials
from accounts.models import AdminProfile
from audit.services import record_event
from core.models import College


class Command(BaseCommand):
    help = "Give dev.staff college scope, disable duplicate Chair/Dean samples, and link individual sample Faculty logins (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--college", default="DEMO-A", help="College code assigned to dev.staff.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development account configuration requires DEBUG=True.")
        try:
            college = College.objects.get(code=options["college"], is_active=True)
            staff = get_user_model().objects.select_for_update().get(username="dev.staff")
            profile = AdminProfile.objects.select_for_update().get(user=staff, role=AdminProfile.Role.STAFF)
        except (College.DoesNotExist, get_user_model().DoesNotExist, AdminProfile.DoesNotExist) as error:
            raise CommandError("Create the sample college and dev.staff account before configuring them.") from error
        actor = get_user_model().objects.filter(username="dev.admin", is_active=True,
            admin_profile__role=AdminProfile.Role.SUPER_ADMIN, admin_profile__is_enabled=True).first()
        if profile.college_id != college.pk or profile.department_id:
            previous = {"college_id": profile.college_id, "department_id": profile.department_id}
            profile.college, profile.department = college, None
            profile.save()
            record_event("account.scope_updated", actor=actor, obj=staff,
                details={"previous": previous, "college_id": college.pk, "source": "development_setup"})
        for user in get_user_model().objects.select_for_update().filter(username__in=("dev.chair", "dev.dean")):
            changed = user.is_active
            if user.is_active:
                user.is_active = False
                user.save(update_fields=["is_active"])
            legacy = AdminProfile.objects.select_for_update().filter(user=user).first()
            if legacy and legacy.is_enabled:
                legacy.is_enabled = False
                legacy.save(update_fields=["is_enabled", "updated_at"])
                changed = True
            if changed:
                record_event("account.disabled", actor=actor, obj=user,
                    details={"reason": "Duplicate development login", "source": "development_setup"})
        try:
            credentials = create_faculty_demo_accounts(actor=actor)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        save_demo_credentials(credentials)
        self.stdout.write(self.style.SUCCESS(
            f"dev.staff is assigned to {college.code}; Chair/Dean samples are disabled; {len(credentials)} new Faculty accounts created."
        ))
        if credentials:
            self.stdout.write("New passwords are saved in .local/development-credentials.txt (Git-ignored).")

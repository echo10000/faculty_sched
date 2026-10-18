"""Provision one fictional Authorized Staff login per active demo college."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.demo import create_college_staff_demo_accounts, save_demo_credentials
from accounts.permissions import is_system_admin


class Command(BaseCommand):
    help = "Create CAS, CCJE, CIT, CAF and CBA sample staff using existing college scope (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--actor", default="dev.admin", help="Existing active system administrator to record in the audit.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development account creation requires DEBUG=True.")
        actor = get_user_model().objects.filter(username=options["actor"]).first()
        if not actor or not is_system_admin(actor):
            raise CommandError("The actor must be an existing active system administrator.")
        try:
            credentials = create_college_staff_demo_accounts(actor=actor)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        save_demo_credentials(credentials)
        self.stdout.write(self.style.SUCCESS(
            f"{len(credentials)} new college staff accounts created. Existing accounts, passwords and scopes preserved."))
        if credentials:
            self.stdout.write("New passwords are saved in .local/development-credentials.txt (Git-ignored).")

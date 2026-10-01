"""Optional fictional human-review example for local development only."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import AdminProfile
from timetabling.conflicts import get_schedule_conflicts
from timetabling.models import Schedule
from timetabling.mutations import validate_schedule


class Command(BaseCommand):
    help = "Optionally validate the fictional manual timetable; publication remains explicit (DEBUG only)."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True.")
        chair = get_user_model().objects.filter(username="dev.chair", is_active=True).first()
        profile = AdminProfile.objects.filter(
            user=chair, role=AdminProfile.Role.STAFF, is_enabled=True,
        ).first() if chair else None
        if not profile:
            raise CommandError("Create the optional dev.chair account with seed_foundation --create-users first.")
        schedule = Schedule.objects.filter(
            name="Example manual timetable", department_id=profile.department_id,
            academic_term__code="DEMO-TERM",
        ).order_by("pk").first()
        if schedule is None:
            call_command("seed_timetables", stdout=self.stdout)
            schedule = Schedule.objects.filter(
                name="Example manual timetable", department_id=profile.department_id,
                academic_term__code="DEMO-TERM",
            ).order_by("pk").first()
        if schedule is None:
            raise CommandError("The fictional manual timetable was not available.")
        if schedule.status in (Schedule.Status.VALIDATED, Schedule.Status.UNDER_REVIEW, Schedule.Status.APPROVED):
            self.stdout.write("Review example already submitted; preserving its history.")
            return
        if schedule.status == Schedule.Status.NEEDS_REVISION:
            self.stdout.write("Review example was returned; preserving its reviewer decision.")
            return
        warnings = {item.code for item in get_schedule_conflicts(schedule, user=chair)
                    if item.severity == "WARNING"}
        try:
            validate_schedule(user=chair, schedule_id=schedule.pk)
        except ValidationError as error:
            raise CommandError("The fictional schedule cannot be submitted: " + "; ".join(error.messages)) from error
        self.stdout.write(self.style.SUCCESS(
            "Fictional schedule validated for staff review. No publication or official booking was created."
        ))

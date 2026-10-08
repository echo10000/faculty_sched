"""Install an editable example calendar without replacing existing calendar settings."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.permissions import is_system_admin
from academics.calendar import ensure_calendar_periods
from academics.models import AcademicCalendar, AcademicTerm, CalendarTermPattern, Semester
from audit.services import record_event


class Command(BaseCommand):
    help = "Configure a sample June–May academic year with two semesters (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--actor", default="dev.admin")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Sample calendar setup requires DEBUG=True.")
        actor = get_user_model().objects.filter(username=options["actor"]).first()
        if not actor or not is_system_admin(actor):
            raise CommandError("The actor must be an existing active system administrator.")
        if AcademicCalendar.objects.exists():
            self.stdout.write("Existing calendar settings preserved. Edit them in system administration.")
            return
        rule = AcademicCalendar.objects.create(name="Sample calendar — editable by admin",
            year_start_month=6, year_start_day=1, created_by=actor)
        demo = AcademicTerm.objects.select_related("semester").filter(
            code__in=("FIRST", "CAS-DEMO"), is_active=True).first()
        first = demo.semester if demo else Semester.objects.get_or_create(code="FIRST", defaults={"name": "First semester", "created_by": actor})[0]
        second = Semester.objects.get_or_create(code="SECOND", defaults={"name": "Second semester", "created_by": actor})[0]
        for semester, code, dates in ((first, "FIRST", (8, 1, 12, 20)), (second, "SECOND", (1, 5, 5, 20))):
            CalendarTermPattern.objects.create(calendar=rule, semester=semester, code=code,
                start_month=dates[0], start_day=dates[1], end_month=dates[2], end_day=dates[3], created_by=actor)
        record_event("calendar.sample_configured", actor=actor, obj=rule)
        ensure_calendar_periods()
        self.stdout.write(self.style.SUCCESS("Sample calendar configured: year June 1–May 31; first semester August 1–December 20; second semester January 5–May 20."))
        self.stdout.write("Edit Automatic calendar settings under Academic calendar. Existing terms and assignments are preserved.")

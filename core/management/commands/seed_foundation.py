from datetime import date
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicYear, AcademicTerm, Semester
from accounts.models import AdminProfile
from audit.services import record_event
from core.models import College, Department, SystemSetting


class Command(BaseCommand):
    help = "Seed fictional Phase 1 development records only; no faculty or timetable data."

    def add_arguments(self, parser):
        parser.add_argument("--with-balancing", action="store_true", help="Also seed a separate fictional Phase 6 workload balancing example (includes timetables).")
        parser.add_argument("--with-timetables", action="store_true", help="Also seed fictional manual Phase 4 timetables (includes teaching/resources).")
        parser.add_argument("--with-teaching", action="store_true", help="Also seed fictional Phase 3 availability, offerings and teaching assignments (includes resources).")
        parser.add_argument("--with-resources", action="store_true", help="Also seed fictional Phase 2 master data and example workload policies.")
        parser.add_argument("--create-users", action="store_true", help="Create example role accounts with generated passwords saved in .local/.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True; do not run against production.")
        call_command("bootstrap_roles", stdout=self.stdout)
        college, _ = College.objects.get_or_create(code="DEMO-A", defaults={"name": "Example College A"})
        second, _ = College.objects.get_or_create(code="DEMO-B", defaults={"name": "Example College B"})
        department, _ = Department.objects.get_or_create(college=college, code="DEMO-D1", defaults={"name": "Example Department One"})
        Department.objects.get_or_create(college=second, code="DEMO-D2", defaults={"name": "Example Department Two"})
        year_number = date.today().year
        year, _ = AcademicYear.objects.get_or_create(label=f"Example {year_number}-{year_number + 1}", defaults={"start_date": date(year_number, 1, 1), "end_date": date(year_number + 1, 12, 31)})
        semester, _ = Semester.objects.get_or_create(code="DEMO-PERIOD", defaults={"name": "Example teaching period"})
        AcademicTerm.objects.get_or_create(academic_year=year, code="DEMO-TERM", defaults={"semester": semester, "start_date": date(year_number, 8, 1), "end_date": date(year_number, 12, 20)})
        SystemSetting.objects.get_or_create(key="institution_name", defaults={"value": settings.INSTITUTION_NAME, "description": "Institution display name"})
        credentials = []
        if options["create_users"]:
            for username, role, scope in [
                ("dev.admin", AdminProfile.Role.SUPER_ADMIN, {}),
                ("dev.dean", AdminProfile.Role.DEAN, {"college": college}),
                ("dev.chair", AdminProfile.Role.DEPT_CHAIR, {"department": department}),
                ("dev.staff", AdminProfile.Role.STAFF, {"department": department}),
            ]:
                if get_user_model().objects.filter(username=username).exists():
                    continue
                password = secrets.token_urlsafe(20)
                user = get_user_model().objects.create_user(username=username, password=password, is_staff=role == AdminProfile.Role.SUPER_ADMIN)
                AdminProfile.objects.create(user=user, role=role, **scope)
                credentials.append(f"{username}: {password}")
                record_event("account.seed", obj=user)
        record_event("foundation.seed", details={"new_accounts": len(credentials)})
        if credentials:
            credential_file = settings.BASE_DIR / ".local" / "development-credentials.txt"

            def save_credentials():
                credential_file.parent.mkdir(exist_ok=True)
                with credential_file.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(credentials) + "\n")

            transaction.on_commit(save_credentials)
            self.stdout.write("New account passwords will be written to .local/development-credentials.txt (Git-ignored).")
        self.stdout.write(self.style.SUCCESS("Foundation seed complete. Example dates/organizations are not institutional policy. Existing records and passwords were preserved."))
        if options.get("with_balancing"):
            call_command("seed_balancing", stdout=self.stdout)
        elif options.get("with_timetables"):
            call_command("seed_timetables", stdout=self.stdout)
        elif options.get("with_teaching"):
            call_command("seed_teaching", stdout=self.stdout)
        elif options.get("with_resources"):
            call_command("seed_resources", stdout=self.stdout)

from datetime import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicTerm
from scheduling.models import Room
from workloads.models import FacultySubjectAssignment
from timetabling.models import Schedule, ScheduleEntry, ClassSection, OfferingRequirement
from timetabling.conflicts import detect_entry_conflicts
from timetabling.mutations import mutation_lock, event


class Command(BaseCommand):
    help = "Seed fictional manual draft meetings, preserving accounts and existing records."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True.")
        mutation_lock()
        call_command("seed_teaching", stdout=self.stdout)
        term = AcademicTerm.objects.filter(code="DEMO-TERM").order_by("-start_date").first()
        for index in (1, 2):
            assignment = FacultySubjectAssignment.objects.get(faculty__employee_id=f"DEMO-F{index}", subject_offering__academic_term=term, subject_offering__code="DEMO")
            department = assignment.subject_offering.department
            section, created = ClassSection.objects.get_or_create(academic_term=term, department=department, code=f"DEMO-CLASS-{index}", defaults={"expected_size": 25})
            if created:
                event("classsection.created", None, section, source="development_seed")
            requirement, created = OfferingRequirement.objects.get_or_create(subject_offering=assignment.subject_offering, defaults={"section": section})
            if created:
                event("offeringrequirement.created", None, requirement, source="development_seed")
            schedule, created = Schedule.objects.get_or_create(academic_term=term, department=department, name="Example manual timetable")
            if created:
                event("schedule.created", None, schedule, source="development_seed")
            room = Room.objects.get(code=f"DEMO-R{index}")
            for day, kind, start, end in [(1, "lecture", time(9), time(11)), (3, "laboratory", time(9), time(12))]:
                if ScheduleEntry.objects.filter(schedule=schedule, assignment=assignment, day_of_week=day, meeting_type=kind).exists():
                    continue
                entry = ScheduleEntry(schedule=schedule, assignment=assignment, room=room, day_of_week=day, meeting_type=kind, start_time=start, end_time=end, notes="Fictional manual seed example.")
                errors = [c.message for c in detect_entry_conflicts(entry) if c.severity == "ERROR"]
                if errors:
                    raise CommandError("Seed would conflict with edited data: " + "; ".join(errors))
                entry.save()
                event("scheduleentry.created", None, entry, source="development_seed")
        self.stdout.write(self.style.SUCCESS("Manual draft examples ready. To test conflicts, preview another Monday 10:00–11:00 meeting using the seeded faculty or room. Nothing was automatically scheduled."))

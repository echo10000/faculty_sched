from datetime import time
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from academics.models import AcademicTerm, Subject
from faculty.models import Faculty
from audit.services import record_event
from workloads.models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering


class Command(BaseCommand):
    help = "Seed fictional Phase 3 teaching data; never create timetables or reset accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True.")
        call_command("seed_resources", stdout=self.stdout)
        term = AcademicTerm.objects.filter(code="DEMO-TERM").order_by("-start_date").first()
        for index in (1, 2):
            faculty = Faculty.objects.get(employee_id=f"DEMO-F{index}")
            subject = Subject.objects.get(code=f"DEMO-S{index}")
            offering, new = SubjectOffering.objects.get_or_create(subject=subject, academic_term=term, code="DEMO", defaults={"department": subject.owning_department, "lecture_units": subject.lecture_units, "laboratory_units": subject.laboratory_units, "lecture_hours": subject.lecture_hours, "laboratory_hours": subject.laboratory_hours, "notes": "Fictional Phase 3 example."})
            if new:
                record_event("subjectoffering.created", obj=offering, details={"academic_term_id": term.pk, "source": "development_seed"})
            assignment, new = FacultySubjectAssignment.objects.get_or_create(faculty=faculty, subject_offering=offering)
            if new:
                from workloads.calculation import validate_workload
                from workloads.operations import check_share
                check_share(assignment)
                validate_workload(faculty, term, assignment, exclude_pk=assignment.pk)
                record_event("facultysubjectassignment.created", obj=assignment, details={"academic_term_id": term.pk, "source": "development_seed"})
            for day, kind, start, end in [(1, "available", time(8), time(12)), (1, "preferred", time(9), time(11)), (5, "unavailable", time(13), time(17))]:
                entry, new = FacultyAvailability.objects.get_or_create(faculty=faculty, academic_term=term, day_of_week=day, start_time=start, end_time=end, availability_type=kind, defaults={"notes": "Fictional availability; not institutional policy."})
                if new:
                    record_event("facultyavailability.created", obj=entry, details={"academic_term_id": term.pk, "source": "development_seed"})
        self.stdout.write(self.style.SUCCESS("Phase 3 teaching examples ready. Existing records and credentials preserved; no schedules generated."))

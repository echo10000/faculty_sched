"""Optional fictional, unscheduled teaching demand for reviewing Phase 6."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicTerm, Subject
from audit.services import record_event
from faculty.models import Faculty
from workloads.models import FacultySubjectAssignment, FacultyTermCapacity, SubjectOffering, WorkloadPolicy


class Command(BaseCommand):
    help = "Seed a separate fictional term with a reviewable workload balancing example; never run the optimizer."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True.")
        call_command("seed_timetables", stdout=self.stdout)
        base_term = AcademicTerm.objects.filter(code="DEMO-TERM").order_by("-start_date").first()
        next_year = base_term.academic_year.start_date.year + 1
        term, created = AcademicTerm.objects.get_or_create(
            academic_year=base_term.academic_year, code="DEMO-BALANCING",
            defaults={
                "semester": base_term.semester,
                "start_date": date(next_year, 1, 1),
                "end_date": date(next_year, 5, 31),
                "is_active": True,
            },
        )
        if created:
            record_event("academicterm.created", obj=term,
                         details={"academic_term_id": term.pk, "source": "development_seed"})
        subject = Subject.objects.get(code="DEMO-S1")
        department = subject.owning_department
        first = Faculty.objects.get(employee_id="DEMO-F1")
        second, created = Faculty.objects.get_or_create(
            employee_id="DEMO-F1B",
            defaults={
                "first_name": "Morgan", "last_name": "Example",
                "home_department": department,
                "employment_category": first.employment_category,
                "is_active": True,
            },
        )
        if created:
            record_event("faculty.created", obj=second,
                         details={"source": "development_seed"})
        policy, created = WorkloadPolicy.objects.get_or_create(
            academic_term=term, department=department,
            defaults={
                "lecture_weight": Decimal("1"),
                "laboratory_weight": Decimal("1"),
                "enforce_maximum": True,
            },
        )
        if created:
            record_event("workloadpolicy.created", obj=policy,
                         details={"academic_term_id": term.pk, "source": "development_seed"})
        for faculty in (first, second):
            capacity, created = FacultyTermCapacity.objects.get_or_create(
                faculty=faculty, academic_term=term,
                defaults={
                    "recommended_load": Decimal("3"),
                    "maximum_load": Decimal("6"),
                    "enforce_maximum": True,
                },
            )
            if created:
                record_event("facultytermcapacity.created", obj=capacity,
                             details={"academic_term_id": term.pk, "source": "development_seed"})
        for code in ("BALANCE-A", "BALANCE-B"):
            offering, created = SubjectOffering.objects.get_or_create(
                subject=subject, academic_term=term, code=code,
                defaults={
                    "department": department,
                    "lecture_units": Decimal("3"),
                    "laboratory_units": Decimal("0"),
                    "lecture_hours": Decimal("3"),
                    "laboratory_hours": Decimal("0"),
                    "is_active": True,
                },
            )
            if created:
                record_event("subjectoffering.created", obj=offering,
                             details={"academic_term_id": term.pk, "source": "development_seed"})
            if created:
                assignment = FacultySubjectAssignment.objects.create(
                    faculty=first, subject_offering=offering, share=Decimal("1"),
                )
                record_event("facultysubjectassignment.created", obj=assignment,
                             details={"academic_term_id": term.pk, "source": "development_seed"})
        self.stdout.write(self.style.SUCCESS(
            "Phase 6 fictional balancing example ready. No recommendations were generated or accepted."
        ))

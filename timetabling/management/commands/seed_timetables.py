from datetime import time, timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicTerm, Subject
from audit.services import record_event
from faculty.models import Faculty
from scheduling.models import Room
from workloads.models import FacultySubjectAssignment, SubjectOffering
from timetabling.models import (
    AssignmentMeetingRequirement,
    ClassSection,
    OfferingRequirement,
    Schedule,
    ScheduleEntry,
    SchedulingConfiguration,
)
from timetabling.conflicts import detect_entry_conflicts
from timetabling.mutations import mutation_lock, event


class Command(BaseCommand):
    help = "Seed fictional manual and generator timetable examples without generating meetings."

    configuration_defaults = {
        "allowed_weekdays": [1, 2, 3, 4, 5],
        "earliest_start": time(8),
        "latest_end": time(17),
        "slot_increment_minutes": 30,
        "solver_time_limit_seconds": 10,
        "random_seed": 17,
        "worker_count": 1,
        "faculty_preference_weight": 1,
        "faculty_gap_weight": 2,
        "section_gap_weight": 3,
        "meeting_distribution_weight": 4,
        "room_fit_weight": 5,
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-infeasible-generator-example",
            action="store_true",
            help="Also seed a separate fictional zero-candidate generator workspace.",
        )

    def seed_configuration(self, term, department, *, latest_end):
        configuration, created = SchedulingConfiguration.objects.get_or_create(
            academic_term=term,
            department=department,
            defaults={**self.configuration_defaults, "latest_end": latest_end},
        )
        if created:
            event("schedulingconfiguration.created", None, configuration, source="development_seed")

    def seed_meeting_requirement(self, assignment, meeting_type, duration):
        requirement, created = AssignmentMeetingRequirement.objects.get_or_create(
            assignment=assignment,
            meeting_type=meeting_type,
            defaults={"meetings_per_week": 1, "duration_minutes": duration},
        )
        if created:
            event("assignmentmeetingrequirement.created", None, requirement, source="development_seed")

    def seed_infeasible_example(self, default_term):
        start_date = default_term.end_date + timedelta(days=1)
        end_date = start_date + timedelta(days=90)
        if end_date > default_term.academic_year.end_date:
            raise CommandError("The fictional infeasible term would extend beyond its academic year.")

        term, created = AcademicTerm.objects.get_or_create(
            academic_year=default_term.academic_year,
            code="DEMO-INFEASIBLE",
            defaults={
                "semester": default_term.semester,
                "start_date": start_date,
                "end_date": end_date,
                "is_active": True,
            },
        )
        if created:
            record_event(
                "academicterm.created", obj=term,
                details={"academic_term_id": term.pk, "source": "development_seed"},
            )

        subject = Subject.objects.get(code="DEMO-S1")
        department = subject.owning_department
        offering, created = SubjectOffering.objects.get_or_create(
            subject=subject,
            academic_term=term,
            code="INFEASIBLE",
            defaults={
                "department": department,
                "lecture_units": 2,
                "laboratory_units": 0,
                "lecture_hours": 2,
                "laboratory_hours": 0,
                "is_active": True,
                "notes": "Fictional zero-candidate generator example; not institutional policy.",
            },
        )
        if created:
            record_event(
                "subjectoffering.created", obj=offering,
                details={"academic_term_id": term.pk, "source": "development_seed"},
            )

        faculty = Faculty.objects.get(employee_id="DEMO-F1")
        assignment, created = FacultySubjectAssignment.objects.get_or_create(
            faculty=faculty,
            subject_offering=offering,
            defaults={"share": 1},
        )
        if created:
            record_event(
                "facultysubjectassignment.created", obj=assignment,
                details={"academic_term_id": term.pk, "source": "development_seed"},
            )

        section, created = ClassSection.objects.get_or_create(
            academic_term=term,
            department=department,
            code="DEMO-INFEASIBLE-CLASS",
            defaults={"expected_size": 25},
        )
        if created:
            event("classsection.created", None, section, source="development_seed")
        requirement, created = OfferingRequirement.objects.get_or_create(
            subject_offering=offering,
            defaults={"section": section},
        )
        if created:
            event("offeringrequirement.created", None, requirement, source="development_seed")
        self.seed_configuration(term, department, latest_end=time(9))
        self.seed_meeting_requirement(assignment, "lecture", 120)
        workspace, created = Schedule.objects.get_or_create(
            academic_term=term,
            department=department,
            name="Example infeasible generator workspace",
        )
        if created:
            event("schedule.created", None, workspace, source="development_seed")

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

        for index in (1, 2):
            assignment = FacultySubjectAssignment.objects.get(
                faculty__employee_id=f"DEMO-F{index}",
                subject_offering__academic_term=term,
                subject_offering__code="DEMO",
            )
            self.seed_configuration(term, assignment.subject_offering.department, latest_end=time(17))
            self.seed_meeting_requirement(assignment, "lecture", 120)
            self.seed_meeting_requirement(assignment, "laboratory", 180)

        first_department = FacultySubjectAssignment.objects.get(
            faculty__employee_id="DEMO-F1",
            subject_offering__academic_term=term,
            subject_offering__code="DEMO",
        ).subject_offering.department
        workspace, created = Schedule.objects.get_or_create(
            academic_term=term,
            department=first_department,
            name="Example generator workspace",
        )
        if created:
            event("schedule.created", None, workspace, source="development_seed")

        if options["with_infeasible_generator_example"]:
            self.seed_infeasible_example(term)
        self.stdout.write(self.style.SUCCESS(
            "Manual draft and feasible generator examples ready. Nothing was automatically scheduled."
        ))

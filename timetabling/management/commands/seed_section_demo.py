"""Add repeatable section-generation examples without generating meetings."""
from datetime import date, time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicYear, AcademicTerm, Semester, Subject
from audit.services import record_event
from core.models import College, Department, Program
from faculty.models import EmploymentCategory, Faculty
from resources.models import Building, RoomType
from scheduling.models import Room
from workloads.calculation import validate_workload
from workloads.models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering, WorkloadPolicy
from workloads.operations import check_share
from timetabling.locking import scheduling_lock
from timetabling.models import AssignmentMeetingRequirement, ClassSection, OfferingRequirement, Schedule, SchedulingConfiguration


class Command(BaseCommand):
    help = "Seed fictional section-generation data, optionally expanding all colleges and archiving legacy examples."

    # Fictional teaching examples, not official curricula or institutional policies.
    colleges = {
        "CAS": ("College of Arts and Sciences", {
            "BSIT": ("Bachelor of Science in Information Technology", "computer_lab", "Computer laboratory",
                     ("Programming 1", "Data Structures", "Web Application Development", "Capstone Project")),
            "BSCS": ("Bachelor of Science in Computer Science", "computer_lab", "Computer laboratory",
                     ("Programming 1", "Data Structures", "Algorithm Design", "Computer Science Project")),
        }),
        "CCJE": ("College of Criminal Justice Education", {
            "CRIM": ("Criminology", "forensic_lab", "Forensic laboratory",
                     ("Introduction to Criminalistics", "Forensic Photography", "Crime Scene Investigation", "Forensic Practice")),
        }),
        "CIT": ("College of Industrial Technology", {
            "IT": ("Industrial Technology", "industrial_workshop", "Industrial workshop",
                   ("Workshop Fundamentals", "Industrial Processes", "Production Technology", "Industrial Project")),
            "COMPTECH": ("Computer Technology", "computer_lab", "Computer laboratory",
                         ("Computer Hardware Fundamentals", "Computer Servicing", "Network Installation", "Computer Systems Project")),
            "AUTO": ("Automotive Technology", "automotive_workshop", "Automotive workshop",
                     ("Automotive Fundamentals", "Engine Servicing", "Vehicle Diagnostics", "Automotive Systems Project")),
        }),
        "CAF": ("College of Agriculture and Forestry", {
            "ANSCI": ("Animal Science", "animal_science_lab", "Animal science laboratory",
                      ("Introduction to Animal Science", "Animal Nutrition", "Livestock Production", "Animal Science Project")),
            "AGRON": ("Agronomy", "agronomy_lab", "Agronomy laboratory",
                      ("Crop Science Fundamentals", "Soil Science", "Crop Production", "Agronomy Project")),
        }),
        "CBA": ("College of Business Administration", {
            "HM": ("Hospitality Management", "hospitality_lab", "Hospitality laboratory",
                   ("Hospitality Fundamentals", "Food and Beverage Service", "Rooms Division Operations", "Hospitality Project")),
            "OA": ("Office Administration", "computer_lab", "Computer laboratory",
                   ("Office Technology Fundamentals", "Business Document Processing", "Office Systems", "Office Administration Project")),
            "BA": ("Business Administration", "business_lab", "Business simulation laboratory",
                   ("Business Applications", "Marketing Applications", "Operations Management", "Business Enterprise Project")),
        }),
    }

    def add_arguments(self, parser):
        parser.add_argument("--actor", help="Existing administrator username to record as draft creator.")
        parser.add_argument("--all-colleges", action="store_true",
                            help="Add CAS, CCJE, CIT, CAF and CBA; A/B sections in years 1–4.")
        parser.add_argument("--archive-legacy", action="store_true",
                            help="Deactivate known older DEMO examples, preserving accounts and scheduling history.")

    def archive_legacy(self):
        colleges = College.objects.filter(code__in=("DEMO-A", "DEMO-B"), name__in=(
            "Example College A", "Example College B"))
        departments = Department.objects.filter(college__in=colleges, code__in=("DEMO-D1", "DEMO-D2"))
        terms = AcademicTerm.objects.filter(code__in=("DEMO-TERM", "DEMO-INFEASIBLE", "DEMO-BALANCING"),
                                            academic_year__label__startswith="Example ")
        groups = (
            SubjectOffering.objects.filter(academic_term__in=terms, department__in=departments),
            ClassSection.objects.filter(academic_term__in=terms, department__in=departments),
            Subject.objects.filter(owning_department__in=departments, code__startswith="DEMO-"),
            Faculty.objects.filter(home_department__in=departments, employee_id__startswith="DEMO-"),
            Room.objects.filter(owner_department__in=departments, code__startswith="DEMO-"),
            departments, colleges, terms,
        )
        count = 0
        for records in groups:
            for obj in records.filter(is_active=True):
                obj.is_active = False
                obj.save(update_fields=["is_active", "updated_at"])
                record_event(f"{obj._meta.model_name}.archived", actor=self.actor, obj=obj,
                             details={"source": "section_demo_seed", "legacy_example_only": True})
                count += 1
        self.stdout.write(f"Archived {count} older example records. Accounts, audits and schedule history retained.")

    def create(self, model, *, defaults=None, **lookup):
        obj, created = model.objects.get_or_create(**lookup, defaults=defaults or {})
        if created:
            record_event(f"{model._meta.model_name}.created", actor=self.actor, obj=obj,
                         details={"source": "section_demo_seed", "example_only": True})
        return obj

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True.")
        self.actor = None
        if options.get("actor"):
            from accounts.permissions import is_system_admin
            self.actor = get_user_model().objects.filter(username=options["actor"]).first()
            if not self.actor or not is_system_admin(self.actor):
                raise CommandError("The actor must be an existing active system administrator.")
        scheduling_lock()
        # Reuse the original demo identities after an administrator renames them.
        year = (AcademicYear.objects.filter(label="2026-2027").first()
                or AcademicYear.objects.filter(label="CAS Demo 2026-2027").first())
        if year is None:
            year = self.create(AcademicYear, label="2026-2027", defaults={
                "start_date": date(2026, 6, 1), "end_date": date(2027, 5, 31)})
        term = year.terms.filter(code__in=("FIRST", "CAS-DEMO")).first()
        if term is None:
            semester = (Semester.objects.filter(code="FIRST").first()
                        or Semester.objects.filter(code="CAS-DEMO-1").first())
            if semester is None:
                semester = self.create(Semester, code="FIRST", defaults={"name": "First semester"})
            term = self.create(AcademicTerm, academic_year=year, code="FIRST", defaults={
                "semester": semester, "start_date": date(2026, 8, 1), "end_date": date(2026, 12, 20)})
        category = self.create(EmploymentCategory, code="full_time", defaults={"name": "Full Time"})
        lecture = self.create(RoomType, code="lecture", defaults={"name": "Lecture room"})
        for college_code, (college_name, programs) in self.colleges.items():
            if not options["all_colleges"] and college_code != "CAS":
                continue
            college = self.create(College, code=college_code, defaults={"name": college_name})
            building = self.create(Building, code=f"{college_code}-DEMO",
                                   defaults={"name": f"{college_code} Demo Building"})
            for suffix in ("R1", "R2"):
                room_code = f"{college_code}-DEMO-{suffix}"
                self.create(Room, code=room_code, defaults={"name": f"{college_code} demo {room_code}",
                    "capacity": 40, "category": lecture, "building": building, "owner_college": college})
            for code, (name, lab_code, lab_name, practical_titles) in programs.items():
                lab = self.create(RoomType, code=lab_code, defaults={"name": lab_name})
                room_code = "CAS-DEMO-LAB" if college_code == "CAS" else f"{college_code}-DEMO-{code}-LAB"
                self.create(Room, code=room_code, defaults={"name": f"{college_code} demo {room_code}",
                    "capacity": 40, "category": lab, "building": building, "owner_college": college})
                if college_code == "CAS" and options["all_colleges"]:
                    # Both eight-section computing programs share college laboratories.
                    self.create(Room, code="CAS-DEMO-LAB2", defaults={"name": "CAS demo CAS-DEMO-LAB2",
                        "capacity": 40, "category": lab, "building": building, "owner_college": college})
                self.seed_department(college, code, name, practical_titles, term, category, lecture, lab,
                                     all_years=options["all_colleges"])
        if options["archive_legacy"]:
            self.archive_legacy()
        description = ("CAS, CCJE, CIT, CAF and CBA; 11 programs with A/B sections in years 1–4"
                       if options["all_colleges"] else "BSIT and BSCS, each with 1A, 1B and 2A")
        self.stdout.write(self.style.SUCCESS(
            f"{term} ready: {description}; four subjects per section. "
            "No meetings were generated by this command. Existing records, accounts and decisions were preserved."))

    def seed_department(self, college, code, name, practical_titles, term, category, lecture, lab, *, all_years):
        department = self.create(Department, college=college, code=code, defaults={
            "name": f"{code} Department" if college.code == "CAS" else f"{name} ({code})"})
        program = self.create(Program, department=department, code=code, defaults={"name": name})
        self.create(WorkloadPolicy, academic_term=term, department=department, defaults={
            "recommended_load": Decimal("18"), "maximum_load": Decimal("24"),
            "lecture_weight": Decimal("1"), "laboratory_weight": Decimal("1.5"),
            "enforce_maximum": True, "notes": "DEMO ONLY: example values, not approved institutional policy."})
        self.create(SchedulingConfiguration, academic_term=term, department=department, defaults={
            "allowed_weekdays": [1, 2, 3, 4, 5], "earliest_start": time(8), "latest_end": time(17),
            "slot_increment_minutes": 60, "solver_time_limit_seconds": 10, "random_seed": 17,
            "worker_count": 1, "faculty_preference_weight": 0, "faculty_gap_weight": 0,
            "section_gap_weight": 0, "meeting_distribution_weight": 1, "room_fit_weight": 1})
        levels = ([(level, letter) for level in range(1, 5) for letter in ("A", "B")]
                  if all_years else [(1, "A"), (1, "B"), (2, "A")])
        people_by_year = {}
        for level, letter in levels:
            faculty_year = level if all_years else 1
            if faculty_year not in people_by_year:
                people_by_year[faculty_year] = self.seed_faculty(college, department, term, category, faculty_year)
            people = people_by_year[faculty_year]
            section = self.create(ClassSection, academic_term=term, department=department,
                code=f"{code}-{level}{letter}", defaults={"program": program, "year_level": level,
                                                        "expected_size": 30})
            if college.code == "CAS" and level <= 2:
                titles = (("Programming 1", "Introduction to Computing", "Mathematics in the Modern World", "Purposive Communication")
                          if level == 1 else
                          ("Data Structures", "Database Fundamentals", "Discrete Mathematics", "Technical Communication"))
            else:
                titles = (practical_titles[level - 1], f"{name} Theory {level}",
                          ("Mathematics in the Modern World", "Research Fundamentals", "Applied Statistics", "Professional Ethics")[level - 1],
                          ("Purposive Communication", "Technical Communication", "Project Planning", "Entrepreneurship")[level - 1])
            for index, title in enumerate(titles, start=1):
                practical = index == 1
                subject = self.create(Subject, code=f"D-{code}-{level}{index}", defaults={
                    "title": title, "owning_department": department, "lecture_units": 2 if practical else 3,
                    "laboratory_units": 1 if practical else 0, "lecture_hours": 2 if practical else 3,
                    "laboratory_hours": 3 if practical else 0,
                    "description": "Fictional demo subject selection; not an official curriculum."})
                offering = self.create(SubjectOffering, subject=subject, academic_term=term,
                    code=section.code, defaults={"department": department,
                        "lecture_units": subject.lecture_units, "laboratory_units": subject.laboratory_units,
                        "lecture_hours": subject.lecture_hours, "laboratory_hours": subject.laboratory_hours,
                        "notes": "Section generation demo; sample teaching values."})
                # Preserve previously assigned instructors and accepted CAS meetings.
                assignment = self.create(FacultySubjectAssignment, subject_offering=offering,
                                         defaults={"faculty": people[index - 1]})
                check_share(assignment)
                validate_workload(assignment.faculty, term, assignment, exclude_pk=assignment.pk)
                self.create(OfferingRequirement, subject_offering=offering, defaults={"section": section,
                    "room_type": lab if practical else lecture, "room_type_mandatory": True,
                    "capacity_is_hard": True})
                self.create(AssignmentMeetingRequirement, assignment=assignment, meeting_type="lecture",
                    defaults={"meetings_per_week": 1, "duration_minutes": 120 if practical else 180})
                if practical:
                    self.create(AssignmentMeetingRequirement, assignment=assignment, meeting_type="laboratory",
                                defaults={"meetings_per_week": 1, "duration_minutes": 180})
        self.create(Schedule, academic_term=term, department=department,
            name=f"{college.code} demo · {code} department draft", defaults={"created_by": self.actor})

    def seed_faculty(self, college, department, term, category, level):
        people = []
        suffix = "" if level == 1 else f"-Y{level}"
        for index, last in enumerate(("Santos", "Reyes", "Cruz", "Garcia"), start=1):
            identity = f"{college.code}-{department.code}{suffix}-F{index}"
            person = self.create(Faculty, employee_id=identity, defaults={
                "first_name": f"Demo {department.code} Y{level} {index}", "last_name": last,
                "email": f"{identity.lower()}@example.invalid", "home_department": department,
                "employment_category": category, "notes": "Fictional section-demo instructor."})
            people.append(person)
            for day in range(1, 6):
                self.create(FacultyAvailability, faculty=person, academic_term=term,
                    day_of_week=day, start_time=time(8), end_time=time(17),
                    availability_type="available", defaults={"notes": "Example availability only."})
        return people

from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicTerm, Subject
from audit.services import record_event
from core.models import Department
from faculty.models import AcademicRank, EmploymentCategory, Faculty
from resources.models import Building, RoomType
from scheduling.models import Room
from workloads.models import FacultyTermCapacity, WorkloadPolicy


class Command(BaseCommand):
    help = "Seed fictional Phase 2 master data and example policies, preserving existing records/accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Development seeding requires DEBUG=True; do not run against production.")
        call_command("seed_foundation", stdout=self.stdout)
        category, _ = EmploymentCategory.objects.get_or_create(code="full_time", defaults={"name": "Full Time"})
        EmploymentCategory.objects.get_or_create(code="part_time", defaults={"name": "Part Time"})
        rank, _ = AcademicRank.objects.get_or_create(name="Example Assistant Professor")
        for code, name in [("lecture", "Lecture room"), ("computer_lab", "Computer laboratory"), ("laboratory", "Laboratory"), ("other", "Other")]:
            RoomType.objects.get_or_create(code=code, defaults={"name": name})
        building, _ = Building.objects.get_or_create(code="DEMO-HALL", defaults={"name": "Example Academic Hall"})
        term = AcademicTerm.objects.filter(code="DEMO-TERM").order_by("-start_date").first()
        created = 0
        for index, dept_code, first, last in [(1, "DEMO-D1", "Alex", "Santos"), (2, "DEMO-D2", "Jamie", "Reyes")]:
            department = Department.objects.get(code=dept_code)
            faculty, new = Faculty.objects.get_or_create(employee_id=f"DEMO-F{index}", defaults={"first_name": first, "last_name": last, "email": f"faculty{index}@example.invalid", "home_department": department, "employment_category": category, "academic_rank": rank, "notes": "Fictional development record."})
            if new:
                record_event("faculty.created", obj=faculty, details={"source": "development_seed"})
                created += 1
            subject, new = Subject.objects.get_or_create(code=f"DEMO-S{index}", defaults={"title": "Introduction to Computing" if index == 1 else "Applied Sciences", "owning_department": department, "lecture_units": Decimal("2"), "laboratory_units": Decimal("1"), "lecture_hours": Decimal("2"), "laboratory_hours": Decimal("3"), "description": "Fictional example subject; values are not institutional policy."})
            if new:
                record_event("subject.created", obj=subject, details={"source": "development_seed"})
                created += 1
            room, new = Room.objects.get_or_create(code=f"DEMO-R{index}", defaults={"name": f"Example classroom {index}", "building": building, "category": RoomType.objects.get(code="lecture"), "capacity": 40, "owner_department": department})
            if new:
                record_event("room.created", obj=room, details={"source": "development_seed"})
                created += 1
            # Illustrative configuration only, scoped to the fictional departments/term.
            policy, new = WorkloadPolicy.objects.get_or_create(academic_term=term, department=department, defaults={"recommended_load": Decimal("18"), "maximum_load": Decimal("24"), "lecture_weight": Decimal("1"), "laboratory_weight": Decimal("1.5"), "notes": "EXAMPLE ONLY: replace with approved institutional policy."})
            if new:
                record_event("workloadpolicy.created", obj=policy, details={"source": "development_seed"})
            capacity, new = FacultyTermCapacity.objects.get_or_create(faculty=faculty, academic_term=term, defaults={"maximum_load": Decimal("21"), "notes": "EXAMPLE ONLY: individual term override."})
            if new:
                record_event("facultytermcapacity.created", obj=capacity, details={"source": "development_seed"})
        record_event("resources.seed", details={"new_master_records": created})
        self.stdout.write(self.style.SUCCESS("Phase 2 seed complete. Existing records and passwords preserved; no assignments or schedules created."))

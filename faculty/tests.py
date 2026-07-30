from datetime import time
from decimal import Decimal

from django.test import TestCase

from academics.models import Curriculum, Subject
from core.models import College, Department, Program
from faculty.models import Designation, Faculty, FacultyQualification
from faculty.services import compute_department_load_summary, compute_faculty_load
from scheduling.models import Assignment, Block, Room, Term, TimeSlot


class FacultyLoadServiceTests(TestCase):
    def setUp(self):
        college = College.objects.create(name="College", code="COL")
        self.department = Department.objects.create(college=college, name="Computing", code="COMP")
        program = Program.objects.create(department=self.department, name="Computing", code="BSCS")
        curriculum = Curriculum.objects.create(program=program, version_year=2026)
        self.term = Term.objects.create(academic_year="2026-2027", term_name="1st")
        self.block = Block.objects.create(curriculum=curriculum, term=self.term, section_code="A", year_level=1)
        self.room = Room.objects.create(name="Room 101", room_type="lecture", capacity=40)
        self.slot = TimeSlot.objects.create(day_of_week="MON", start_time=time(9), end_time=time(10))
        self.subject = Subject.objects.create(code="CS101", title="Subject", units=Decimal("3.0"))
        self.underload = self.make_faculty("FAC-001", Decimal("6.0"))
        self.on_target = self.make_faculty("FAC-002", Decimal("3.0"))
        designation = Designation.objects.create(name="Chair", units_released=Decimal("3.0"))
        self.overload = self.make_faculty("FAC-003", Decimal("6.0"), designation)
        for faculty in (self.underload, self.on_target, self.overload):
            FacultyQualification.objects.create(faculty=faculty, subject=self.subject)

    def make_faculty(self, employee_id, base_load, designation=None):
        return Faculty.objects.create(
            employee_id=employee_id,
            first_name=employee_id,
            last_name="Faculty",
            home_department=self.department,
            employment_type="full_time",
            base_load_units=base_load,
            designation=designation,
        )

    def assign(self, faculty, units):
        return Assignment.objects.create(
            faculty=faculty,
            subject=self.subject,
            block=self.block,
            room=self.room,
            term=self.term,
            time_slot=self.slot,
            units_credited=units,
        )

    def test_underload_without_designation(self):
        self.assign(self.underload, Decimal("3.0"))
        load = compute_faculty_load(self.underload, self.term)
        self.assertEqual(load["status"], "underload")
        self.assertEqual(load["difference"], Decimal("-3.0"))

    def test_on_target_without_designation(self):
        self.assign(self.on_target, Decimal("3.0"))
        load = compute_faculty_load(self.on_target, self.term)
        self.assertEqual(load["status"], "on_target")
        self.assertEqual(load["units_released"], Decimal("0.0"))

    def test_overload_with_designation(self):
        self.assign(self.overload, Decimal("4.0"))
        load = compute_faculty_load(self.overload, self.term)
        self.assertEqual(load["required_load"], Decimal("3.0"))
        self.assertEqual(load["status"], "overload")

    def test_department_summary_includes_every_faculty(self):
        summary = compute_department_load_summary(self.department, self.term)
        self.assertEqual({entry["faculty"] for entry in summary}, {self.underload, self.on_target, self.overload})

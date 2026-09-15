from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase

from academics.models import AcademicYear, AcademicTerm, Semester, Subject
from accounts.models import AdminProfile
from core.models import College, Department
from faculty.models import Faculty
from scheduling.models import Room
from workloads.models import SubjectOffering, FacultySubjectAssignment
from .models import ClassSection, OfferingRequirement, Schedule, ScheduleEntry
from .mutations import save_entry


class ConcurrentTimetableTests(TransactionTestCase):
    def setUp(self):
        college = College.objects.create(code="R", name="Race college")
        dept = Department.objects.create(college=college, code="R", name="Race department")
        self.user = get_user_model().objects.create_user(username="race-chair")
        AdminProfile.objects.create(user=self.user, role="dept_chair", department=dept)
        year = AcademicYear.objects.create(label="Race year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        semester = Semester.objects.create(code="R", name="Race")
        term = AcademicTerm.objects.create(academic_year=year, semester=semester, code="R", start_date=year.start_date, end_date=year.end_date)
        self.schedule = Schedule.objects.create(name="Concurrent workspace", academic_term=term, department=dept)
        self.assignments, self.rooms = [], []
        for n in (1, 2):
            faculty = Faculty.objects.create(employee_id=f"R{n}", first_name=f"Race{n}", last_name="Teacher", home_department=dept)
            subject = Subject.objects.create(code=f"R{n}", title="Concurrent subject", owning_department=dept, lecture_units=1)
            offering = SubjectOffering.objects.create(subject=subject, academic_term=term, department=dept, code="R", lecture_units=1, laboratory_units=0, lecture_hours=1, laboratory_hours=0)
            section = ClassSection.objects.create(academic_term=term, department=dept, code=f"R{n}")
            OfferingRequirement.objects.create(subject_offering=offering, section=section)
            self.assignments.append(FacultySubjectAssignment.objects.create(faculty=faculty, subject_offering=offering))
            self.rooms.append(Room.objects.create(name=f"Race room{n}", code=f"R{n}", capacity=40, owner_department=dept))

    def race(self, pairs, expected_code):
        barrier = Barrier(2)
        def submit(pair):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                barrier.wait(timeout=15)
                obj, form, conflicts = save_entry(user=user, schedule_id=self.schedule.pk,
                    data={"assignment": pair[0], "room": pair[1], "day_of_week": 1, "start_time": "09:00", "end_time": "10:00", "meeting_type": "lecture"})
                return "saved" if obj else expected_code if expected_code in {c.code for c in conflicts} else str(form.errors)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, pairs))
        self.assertCountEqual(outcomes, ["saved", expected_code])
        self.assertEqual(ScheduleEntry.objects.count(), 1)

    def test_concurrent_room_overlap_is_prevented(self):
        self.race([(self.assignments[0].pk, self.rooms[0].pk), (self.assignments[1].pk, self.rooms[0].pk)], "ROOM_OVERLAP")

    def test_concurrent_faculty_overlap_is_prevented(self):
        self.race([(self.assignments[0].pk, self.rooms[0].pk), (self.assignments[0].pk, self.rooms[1].pk)], "FACULTY_OVERLAP")

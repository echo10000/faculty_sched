"""Separate PostgreSQL connections exercise official-booking exclusion races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, close_old_connections, connections, transaction
from django.test import TransactionTestCase

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from core.models import College, Department
from faculty.models import Faculty
from scheduling.models import Room
from workloads.models import FacultySubjectAssignment, SubjectOffering
from .models import (
    ActiveSchedule, ClassSection, OfferingRequirement, OfficialResourceBooking,
    Schedule, ScheduleEntry,
)
from .workflow import approve_schedule, revise_approved_schedule, submit_schedule


class OfficialBookingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        college = College.objects.create(code="P7R", name="Phase 7 race college")
        self.department = Department.objects.create(college=college, code="P7R", name="Race department")
        self.users = [get_user_model().objects.create_superuser(
            username=f"p7-race-admin-{index}", password="test-only-password",
            email=f"race-{index}@example.invalid",
        ) for index in range(3)]
        year = AcademicYear.objects.create(label="Phase 7 race year", start_date=date(2026, 1, 1),
                                           end_date=date(2026, 12, 31))
        semester = Semester.objects.create(code="P7R", name="Phase 7 race term")
        self.term = AcademicTerm.objects.create(academic_year=year, semester=semester,
                                                code="P7R", start_date=date(2026, 1, 1),
                                                end_date=date(2026, 5, 31))
        self.faculty = []
        self.rooms = []
        self.sections = []
        self.assignments = []
        self.requirements = []
        self.schedules = []
        self.entries = []
        for index in (1, 2):
            faculty = Faculty.objects.create(employee_id=f"P7R{index}", first_name=f"Race{index}",
                                             last_name="Faculty", home_department=self.department)
            room = Room.objects.create(code=f"P7R{index}", name=f"Race room {index}",
                                       capacity=100, owner_department=self.department)
            subject = Subject.objects.create(code=f"P7R{index}", title=f"Race subject {index}",
                                             owning_department=self.department, lecture_units=1,
                                             laboratory_units=0)
            offering = SubjectOffering.objects.create(subject=subject, academic_term=self.term,
                                                      department=self.department, lecture_units=1,
                                                      laboratory_units=0, lecture_hours=1,
                                                      laboratory_hours=0)
            section = ClassSection.objects.create(academic_term=self.term, department=self.department,
                                                  code=f"P7R{index}", expected_size=20)
            requirement = OfferingRequirement.objects.create(subject_offering=offering, section=section)
            assignment = FacultySubjectAssignment.objects.create(faculty=faculty, subject_offering=offering)
            schedule = Schedule.objects.create(academic_term=self.term, department=self.department,
                                               name=f"Race candidate {index}")
            entry = ScheduleEntry.objects.create(schedule=schedule, assignment=assignment, room=room,
                                                 day_of_week=1, start_time=time(9), end_time=time(10),
                                                 meeting_type="lecture")
            self.faculty.append(faculty)
            self.rooms.append(room)
            self.sections.append(section)
            self.requirements.append(requirement)
            self.assignments.append(assignment)
            self.schedules.append(schedule)
            self.entries.append(entry)

    def booking(self, index, *, booking_date=date(2026, 1, 5)):
        entry = self.entries[index]
        entry.refresh_from_db()
        return OfficialResourceBooking(
            schedule_entry=entry, booking_date=booking_date,
            start_time=entry.start_time, end_time=entry.end_time,
            faculty_id=entry.assignment.faculty_id, room_id=entry.room_id,
            section_id=entry.assignment.subject_offering.scheduling_requirement.section_id,
        )

    def race_bookings(self):
        Schedule.objects.filter(pk__in=[schedule.pk for schedule in self.schedules]).update(status="approved")
        barrier = Barrier(2)

        def insert(index):
            close_old_connections()
            try:
                try:
                    with transaction.atomic():
                        row = self.booking(index)
                        barrier.wait(timeout=15)
                        row.save(force_insert=True)
                    return "committed"
                except IntegrityError:
                    return "conflict"
                except OperationalError as error:
                    # PostgreSQL can deadlock while the three exclusion constraints
                    # inspect concurrent inserts. It aborts one transaction, which
                    # is also a valid losing outcome for this booking race.
                    if getattr(error.__cause__, "pgcode", None) == "40P01":
                        return "conflict"
                    raise
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(insert, (0, 1)))
        self.assertCountEqual(outcomes, ["committed", "conflict"])
        self.assertEqual(OfficialResourceBooking.objects.count(), 1)

    def test_concurrent_room_bookings_only_one_commits(self):
        ScheduleEntry.objects.filter(pk=self.entries[1].pk).update(room=self.rooms[0])
        self.race_bookings()

    def test_concurrent_faculty_bookings_only_one_commits(self):
        FacultySubjectAssignment.objects.filter(pk=self.assignments[1].pk).update(faculty=self.faculty[0])
        self.race_bookings()

    def test_concurrent_section_bookings_only_one_commits(self):
        OfferingRequirement.objects.filter(pk=self.requirements[1].pk).update(section=self.sections[0])
        self.race_bookings()

    def test_adjacent_time_and_different_dates_can_both_commit(self):
        ScheduleEntry.objects.filter(pk=self.entries[1].pk).update(room=self.rooms[0],
                                                                    start_time=time(10), end_time=time(11))
        later_entry = ScheduleEntry.objects.create(
            schedule=self.schedules[1], assignment=self.assignments[1], room=self.rooms[0],
            day_of_week=1, start_time=time(9), end_time=time(10), meeting_type="lecture",
        )
        self.entries.append(later_entry)
        Schedule.objects.filter(pk__in=[schedule.pk for schedule in self.schedules]).update(status="approved")
        self.booking(0).save(force_insert=True)
        self.booking(1).save(force_insert=True)
        self.assertEqual(OfficialResourceBooking.objects.count(), 2)
        self.booking(2, booking_date=date(2026, 1, 12)).save(force_insert=True)
        self.assertEqual(OfficialResourceBooking.objects.count(), 3)

    def test_two_submitted_revisions_race_for_same_active_selection(self):
        # The second independent draft is empty, so it does not occupy a peer slot.
        self.entries[1].delete()
        initial = self.schedules[0]
        warnings = ["MEETING_HOURS_WARNING"]  # The second teaching assignment has no meeting.
        initial = submit_schedule(user=self.users[0], schedule_id=initial.pk,
                                  revision_token=initial.revision_token,
                                  acknowledged_warnings=warnings)
        initial = approve_schedule(user=self.users[1], schedule_id=initial.pk,
                                   revision_token=initial.revision_token,
                                   acknowledged_warnings=warnings)
        candidates = [
            revise_approved_schedule(user=self.users[0], schedule_id=initial.pk,
                                      revision_token=initial.revision_token)
            for _ in range(2)
        ]
        submitted = []
        for clone in candidates:
            clone = submit_schedule(user=self.users[0], schedule_id=clone.pk,
                                    revision_token=clone.revision_token,
                                    acknowledged_warnings=warnings)
            submitted.append(clone)
        candidates = submitted
        barrier = Barrier(2)

        def approve(index):
            close_old_connections()
            try:
                reviewer = get_user_model().objects.get(pk=self.users[index + 1].pk)
                barrier.wait(timeout=15)
                try:
                    approve_schedule(user=reviewer, schedule_id=candidates[index].pk,
                                     revision_token=candidates[index].revision_token,
                                     acknowledged_warnings=warnings)
                    return "committed"
                except ValidationError as error:
                    if "official" in str(error).lower() or "stale" in str(error).lower() or "changed" in str(error).lower():
                        return "stale"
                    raise
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, (0, 1)))
        self.assertCountEqual(outcomes, ["committed", "stale"])
        self.assertIn(ActiveSchedule.objects.get(academic_term=self.term,
                                                 department=self.department).schedule_id,
                      [candidate.pk for candidate in candidates])
        self.assertEqual(Schedule.objects.filter(pk__in=[candidate.pk for candidate in candidates],
                                                 status="approved").count(), 1)

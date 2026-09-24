"""Direct database writes cannot corrupt selected official timetable evidence."""

from datetime import date, time

from django.db import IntegrityError, transaction

from academics.models import AcademicTerm
from faculty.models import Faculty
from scheduling.models import Room
from workloads.models import FacultySubjectAssignment

from .conflicts import get_schedule_conflicts
from .models import (
    ActiveSchedule, ClassSection, OfferingRequirement, OfficialResourceBooking,
    Schedule, ScheduleApprovalSnapshot, ScheduleEntry, ScheduleWorkflowEvent,
)
from .tests import TimetableFixture
from .workflow import approve_schedule, submit_schedule


class OfficialDatabaseIntegrityTests(TimetableFixture):
    def setUp(self):
        super().setUp()
        self.entry = self.candidate()
        self.entry.save()
        warning_codes = sorted({item.code for item in get_schedule_conflicts(self.schedule, user=self.chair)
                                if item.severity == "WARNING"})
        submit_schedule(user=self.chair, schedule_id=self.schedule.pk,
                        revision_token=self.schedule.revision_token,
                        acknowledged_warnings=warning_codes)
        approve_schedule(user=self.dean, schedule_id=self.schedule.pk,
                         revision_token=self.schedule.revision_token,
                         acknowledged_warnings=warning_codes)
        self.schedule.refresh_from_db()

    def test_approved_version_and_entries_reject_direct_mutation(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Schedule.objects.filter(pk=self.schedule.pk).update(name="Illicit rename")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.filter(pk=self.entry.pk).update(start_time=time(8))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.bulk_create([ScheduleEntry(
                schedule=self.schedule, assignment=self.assignment, room=self.room,
                day_of_week=2, start_time=time(9), end_time=time(10),
                meeting_type="lecture",
            )])
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.filter(pk=self.entry.pk).delete()
        self.assertEqual(Schedule.objects.get(pk=self.schedule.pk).name, "Manual draft")
        self.assertEqual(ScheduleEntry.objects.filter(schedule=self.schedule).count(), 1)

    def test_history_snapshot_and_active_selection_are_database_protected(self):
        event = ScheduleWorkflowEvent.objects.get(action="approved")
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=self.schedule)
        active = ActiveSchedule.objects.get(academic_term=self.term, department=self.department)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleWorkflowEvent.objects.filter(pk=event.pk).update(remarks="Changed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleApprovalSnapshot.objects.filter(pk=snapshot.pk).update(payload={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            ActiveSchedule.objects.filter(pk=active.pk).delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ActiveSchedule.objects.create(academic_term=self.later, department=self.department,
                                          schedule=self.schedule, selected_by=self.dean)
        draft = Schedule.objects.create(academic_term=self.later, department=self.department,
                                        name="Unapproved selection")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ActiveSchedule.objects.create(academic_term=self.later, department=self.department,
                                          schedule=draft, selected_by=self.dean)
        self.assertEqual(ActiveSchedule.objects.get(pk=active.pk).schedule_id, self.schedule.pk)

    def test_booking_source_keys_and_dates_cannot_drift(self):
        booking = OfficialResourceBooking.objects.filter(schedule_entry=self.entry).order_by("booking_date").first()
        OfficialResourceBooking.objects.filter(pk=booking.pk).delete()
        wrong_room = Room.objects.create(code="OFFICIAL-FORGE", name="Forged room", capacity=40, owner_department=self.department)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OfficialResourceBooking.objects.create(
                schedule_entry=self.entry, booking_date=booking.booking_date,
                start_time=booking.start_time, end_time=booking.end_time,
                faculty=self.faculty, room=wrong_room, section=self.section,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OfficialResourceBooking.objects.create(
                schedule_entry=self.entry, booking_date=date(2026, 1, 6),
                start_time=booking.start_time, end_time=booking.end_time,
                faculty=self.faculty, room=self.room, section=self.section,
            )
        other_section = ClassSection.objects.create(academic_term=self.term,
                                                    department=self.department, code="OFFICIAL-FORGE")
        with self.assertRaises(IntegrityError), transaction.atomic():
            OfferingRequirement.objects.filter(pk=self.requirement.pk).update(section=other_section)
        other_faculty = Faculty.objects.create(employee_id="OFFICIAL-FORGE", first_name="Other",
                                               last_name="Faculty", home_department=self.department)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacultySubjectAssignment.objects.filter(pk=self.assignment.pk).update(faculty=other_faculty)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicTerm.objects.filter(pk=self.term.pk).update(start_date=date(2026, 1, 2))

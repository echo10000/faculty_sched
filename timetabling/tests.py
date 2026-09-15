from datetime import time

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from workloads.tests import TeachingFixture


class IntervalTests(SimpleTestCase):
    def test_overlap_boundaries_and_duration(self):
        from timetabling.intervals import overlaps, duration_minutes
        for start, end, expected in [(9, 10, True), (8, 10, True), (9, 11, True), (8, 12, True), (10, 11, False), (7, 9, False)]:
            self.assertEqual(overlaps(time(9), time(10), time(start), time(end)), expected)
        self.assertEqual(duration_minutes(time(9), time(10, 30)), 90)


class TimetableFixture(TeachingFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from timetabling.models import ClassSection, OfferingRequirement, Schedule
        from workloads.models import FacultySubjectAssignment
        cls.section = ClassSection.objects.create(academic_term=cls.term, department=cls.department, code="A", expected_size=45)
        cls.requirement = OfferingRequirement.objects.create(subject_offering=cls.offering, section=cls.section)
        cls.assignment = FacultySubjectAssignment.objects.create(faculty=cls.faculty, subject_offering=cls.offering)
        cls.schedule = Schedule.objects.create(academic_term=cls.term, department=cls.department, name="Manual draft")
        cls.room = cls.records[cls.department.pk]["rooms"]

    def candidate(self, **changes):
        from timetabling.models import ScheduleEntry
        return ScheduleEntry(schedule=self.schedule, assignment=self.assignment, room=self.room,
                             day_of_week=1, start_time=time(9), end_time=time(10), meeting_type="lecture", **changes)


class ConflictTests(TimetableFixture):
    def codes(self, entry):
        from timetabling.conflicts import detect_entry_conflicts
        return {c.code: c.severity for c in detect_entry_conflicts(entry)}

    def test_capacity_warning_allows_otherwise_valid_entry(self):
        self.assertEqual(self.codes(self.candidate()), {"ROOM_CAPACITY_WARNING": "WARNING"})

    def test_same_faculty_room_section_overlap_and_adjacency(self):
        self.candidate().save()
        codes = self.codes(self.candidate())
        for code in ("FACULTY_OVERLAP", "ROOM_OVERLAP", "SECTION_OVERLAP"):
            self.assertEqual(codes[code], "ERROR")
        entry = self.candidate()
        entry.start_time, entry.end_time = time(10), time(11)
        self.assertNotIn("ROOM_OVERLAP", self.codes(entry))
        entry.day_of_week = 2
        self.assertNotIn("FACULTY_OVERLAP", self.codes(entry))

    def test_invalid_range_and_wrong_term(self):
        entry = self.candidate()
        entry.end_time = time(8)
        self.assertIn("INVALID_TIME_RANGE", self.codes(entry))
        with self.assertRaises(ValidationError):
            entry.full_clean()
        entry = self.candidate()
        self.schedule.academic_term = self.later
        self.assertIn("TERM_MISMATCH", self.codes(entry))

    def test_unavailable_blocks_preferred_does_not(self):
        from workloads.models import FacultyAvailability
        record = FacultyAvailability.objects.create(faculty=self.faculty, academic_term=self.term,
            day_of_week=1, start_time=time(9), end_time=time(10), availability_type="preferred")
        self.assertNotIn("FACULTY_UNAVAILABLE", self.codes(self.candidate()))
        record.availability_type = "unavailable"
        record.save()
        self.assertEqual(self.codes(self.candidate())["FACULTY_UNAVAILABLE"], "ERROR")

    def test_inactive_resources_detected(self):
        for obj in (self.faculty, self.room, self.offering, self.section):
            with self.subTest(model=type(obj).__name__):
                type(obj).objects.filter(pk=obj.pk).update(is_active=False)
                self.assertEqual(self.codes(self.candidate())["INACTIVE_RESOURCE"], "ERROR")
                type(obj).objects.filter(pk=obj.pk).update(is_active=True)

    def test_room_closure_and_mandatory_type(self):
        from timetabling.models import RoomUnavailability
        from resources.models import RoomType
        RoomUnavailability.objects.create(room=self.room, academic_term=self.term, day_of_week=1, start_time=time(8), end_time=time(11))
        self.requirement.room_type = RoomType.objects.create(code="laboratory", name="Laboratory")
        self.requirement.room_type_mandatory = True
        self.requirement.save()
        codes = self.codes(self.candidate())
        self.assertEqual(codes["ROOM_UNAVAILABLE"], "ERROR")
        self.assertEqual(codes["ROOM_TYPE_MISMATCH"], "ERROR")


class MutationTests(TimetableFixture):
    def data(self, **changes):
        return {"assignment": self.assignment.pk, "room": self.room.pk, "day_of_week": "1", "start_time": "09:00", "end_time": "10:00", "meeting_type": "lecture", **changes}

    def save_entry(self, **kwargs):
        from timetabling.mutations import save_entry
        return save_entry(user=self.chair, schedule_id=self.schedule.pk, data=self.data(), **kwargs)

    def test_preview_save_recheck_and_audit(self):
        from audit.models import AuditLog
        from timetabling.models import ScheduleEntry
        obj, form, conflicts = self.save_entry(preview=True)
        self.assertIsNotNone(obj)
        self.assertFalse(ScheduleEntry.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="scheduleentry.created").exists())
        obj, form, conflicts = self.save_entry()
        self.assertIsNotNone(obj)
        obj, form, conflicts = self.save_entry()
        self.assertIsNone(obj)
        self.assertIn("ROOM_OVERLAP", {c.code for c in conflicts})
        self.assertEqual(ScheduleEntry.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="scheduleentry.created").count(), 1)

    def test_validate_reset_and_dependency_invalidation(self):
        from timetabling.mutations import validate_schedule, effective_status, save_entry
        self.save_entry()
        report = validate_schedule(user=self.chair, schedule_id=self.schedule.pk)
        self.assertEqual(report["errors"], 0)
        self.schedule.refresh_from_db()
        self.assertEqual(effective_status(self.schedule), "validated")
        self.room.capacity = 30
        self.room.save()
        self.assertEqual(effective_status(self.schedule), "draft")
        validate_schedule(user=self.chair, schedule_id=self.schedule.pk)
        save_entry(user=self.chair, schedule_id=self.schedule.pk, data=self.data(start_time="10:00", end_time="11:00"))
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "draft")

    def test_atomic_audit_failure_and_delete(self):
        from unittest.mock import patch
        from timetabling.models import ScheduleEntry
        from timetabling.mutations import remove_entry
        with patch("timetabling.mutations.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.save_entry()
        self.assertFalse(ScheduleEntry.objects.exists())
        obj, _, _ = self.save_entry()
        remove_entry(user=self.chair, schedule_id=self.schedule.pk, pk=obj.pk)
        self.assertFalse(ScheduleEntry.objects.exists())

    def test_workload_not_increased_by_meetings(self):
        from workloads.calculation import calculate_workload
        before = calculate_workload(self.faculty, self.term)
        self.save_entry()
        after = calculate_workload(self.faculty, self.term)
        self.assertEqual(before["teaching_units"], after["teaching_units"])
        self.assertEqual(before["teaching_hours"], after["teaching_hours"])


class PageTests(TimetableFixture):
    def url(self, name, *args):
        from django.urls import reverse
        return reverse(f"timetabling:{name}", args=args)

    def data(self, **changes):
        return {"assignment": self.assignment.pk, "room": self.room.pk, "day_of_week": "1", "start_time": "09:00", "end_time": "10:00", "meeting_type": "lecture", **changes}

    def test_schedule_create_edit_and_scoped_pages(self):
        from timetabling.models import Schedule
        self.client.force_login(self.chair)
        response = self.client.post(self.url("schedules-add"), {"academic_term": self.term.pk, "department": self.department.pk, "name": "New manual timetable"})
        self.assertEqual(response.status_code, 302)
        obj = Schedule.objects.get(name="New manual timetable")
        self.assertEqual(obj.status, "draft")
        for name, args in [("schedules", []), ("schedules-detail", [obj.pk]), ("timetable", [obj.pk]), ("conflicts", [obj.pk]), ("entries-add", [obj.pk]), ("sections", []), ("requirements", []), ("closures", [])]:
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 200, name)
        self.assertEqual(self.client.post(self.url("schedules-edit", obj.pk), {"academic_term": self.term.pk, "department": self.department.pk, "name": "Revised"}).status_code, 302)
        obj.refresh_from_db()
        self.assertEqual(obj.name, "Revised")

    def test_entry_preview_save_edit_delete_workflow(self):
        from timetabling.models import ScheduleEntry
        self.client.force_login(self.chair)
        response = self.client.post(self.url("entries-add", self.schedule.pk), self.data(intent="preview"))
        self.assertContains(response, "Validation preview")
        self.assertFalse(ScheduleEntry.objects.exists())
        self.assertEqual(self.client.post(self.url("entries-add", self.schedule.pk), self.data(intent="save")).status_code, 302)
        entry = ScheduleEntry.objects.get()
        response = self.client.post(self.url("entries-edit", self.schedule.pk, entry.pk), self.data(start_time="10:00", end_time="11:00"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.url("entries-delete", self.schedule.pk, entry.pk)).status_code, 200)
        self.assertTrue(ScheduleEntry.objects.exists())
        self.assertEqual(self.client.post(self.url("entries-delete", self.schedule.pk, entry.pk)).status_code, 302)
        self.assertFalse(ScheduleEntry.objects.exists())

    def test_staff_and_outside_schedule_denied(self):
        from timetabling.models import Schedule
        other = Schedule.objects.create(name="Secret outside schedule", department=self.external, academic_term=self.term)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url("schedules")).status_code, 403)
        self.client.force_login(self.chair)
        for name in ("schedules-detail", "schedules-edit", "timetable", "conflicts", "entries-add", "validate"):
            self.assertEqual(self.client.get(self.url(name, other.pk)).status_code, 404, name)
        self.assertNotContains(self.client.get(self.url("schedules")), "Secret outside schedule")

    def test_forged_assignment_room_term_section_and_department(self):
        from timetabling.models import ScheduleEntry, ClassSection, OfferingRequirement
        from workloads.models import FacultySubjectAssignment
        outside = FacultySubjectAssignment.objects.create(faculty=self.records[self.external.pk]["faculty-management"], subject_offering=self.offerings[self.external.pk])
        self.client.force_login(self.chair)
        for changes in ({"assignment": outside.pk}, {"room": self.records[self.external.pk]["rooms"].pk}, {"assignment": "999999"}):
            response = self.client.post(self.url("entries-add", self.schedule.pk), self.data(**changes))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(ScheduleEntry.objects.exists())
        section = ClassSection.objects.create(academic_term=self.term, department=self.external, code="SECRET")
        response = self.client.post(self.url("requirements-edit", self.requirement.pk), {"subject_offering": self.offering.pk, "section": section.pk})
        self.assertTrue(response.context["form"].errors)
        response = self.client.post(self.url("schedules-edit", self.schedule.pk), {"academic_term": self.later.pk, "department": self.department.pk, "name": "Changed"})
        self.assertTrue(response.context["form"].errors)
        for query in ({"department": self.external.pk}, {"academic_term": "bad"}, {"room": self.records[self.external.pk]["rooms"].pk}, {"section": section.pk}):
            response = self.client.get(self.url("timetable", self.schedule.pk), query)
            self.assertTrue(response.context["filter_form"].errors)
            self.assertEqual(len(response.context["entries"]), 0)

    def test_validation_post_csrf_and_navigation(self):
        from django.test import Client
        self.client.force_login(self.chair)
        self.assertContains(self.client.get("/"), "Manual schedules")
        self.assertEqual(self.client.get(self.url("validate", self.schedule.pk)).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.chair)
        self.assertEqual(secure.post(self.url("entries-add", self.schedule.pk), self.data()).status_code, 403)
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get("/"), "Manual schedules")


class IntegrationTests(TimetableFixture):
    def test_scoped_future_dataset_has_requirements_without_solving(self):
        from timetabling.datasets import scheduling_input
        data = scheduling_input(self.chair, self.schedule.pk)
        self.assertEqual(data["schedule"]["term_id"], self.term.pk)
        self.assertEqual([a["assignment_id"] for a in data["assignments"]], [self.assignment.pk])
        self.assertEqual(data["assignments"][0]["section_id"], self.section.pk)
        self.assertEqual([r["id"] for r in data["rooms"]], [self.room.pk])
        self.assertEqual(data["existing_entries"], [])

    def test_seed_idempotent_and_production_denial(self):
        from io import StringIO
        from django.core.management import call_command, CommandError
        from django.test import override_settings
        from timetabling.models import Schedule, ScheduleEntry
        from timetabling.conflicts import detect_entry_conflicts
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_timetables", stdout=StringIO())
        with override_settings(DEBUG=True):
            call_command("seed_timetables", stdout=StringIO())
            counts = Schedule.objects.count(), ScheduleEntry.objects.count()
            call_command("seed_timetables", stdout=StringIO())
            self.assertEqual(counts, (Schedule.objects.count(), ScheduleEntry.objects.count()))
            self.assertEqual(counts, (3, 4))
            for entry in ScheduleEntry.objects.all():
                self.assertFalse([c for c in detect_entry_conflicts(entry) if c.severity == "ERROR"])

    def test_required_meeting_hours_warn_and_empty_does_not_validate(self):
        from timetabling.mutations import validate_schedule
        report = validate_schedule(user=self.chair, schedule_id=self.schedule.pk)
        self.assertGreater(report["errors"], 0)
        self.assertIn("MEETING_HOURS_WARNING", report["categories"])
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "draft")

    def test_different_faculty_room_section_allowed(self):
        from timetabling.models import ClassSection, OfferingRequirement, ScheduleEntry
        from workloads.models import SubjectOffering, FacultySubjectAssignment
        from faculty.models import Faculty
        from scheduling.models import Room
        from timetabling.conflicts import detect_entry_conflicts
        self.candidate().save()
        faculty = Faculty.objects.create(employee_id="SECOND-F", first_name="Second", last_name="Teacher", home_department=self.department)
        offering = SubjectOffering.objects.create(subject=self.offering.subject, academic_term=self.term, department=self.department, code="SECOND", lecture_units=2, laboratory_units=0, lecture_hours=2, laboratory_hours=0)
        section = ClassSection.objects.create(academic_term=self.term, department=self.department, code="SECOND")
        OfferingRequirement.objects.create(subject_offering=offering, section=section)
        assignment = FacultySubjectAssignment.objects.create(faculty=faculty, subject_offering=offering)
        room = Room.objects.create(name="Second room", code="SECOND", capacity=40, category=self.room_type, owner_department=self.department)
        entry = self.candidate()
        entry.assignment, entry.room = assignment, room
        self.assertFalse([c for c in detect_entry_conflicts(entry) if c.severity == "ERROR"])

    def test_capacity_hard_and_optional_room_type(self):
        from resources.models import RoomType
        from timetabling.conflicts import detect_entry_conflicts
        self.requirement.capacity_is_hard = True
        self.requirement.room_type = RoomType.objects.create(code="lab", name="Lab")
        self.requirement.save()
        codes = {c.code: c.severity for c in detect_entry_conflicts(self.candidate())}
        self.assertEqual(codes["ROOM_CAPACITY_WARNING"], "ERROR")
        self.assertEqual(codes["ROOM_TYPE_MISMATCH"], "WARNING")

    def test_cross_workspace_overlap_and_outside_detail_redaction(self):
        from timetabling.models import Schedule, ScheduleEntry
        from timetabling.conflicts import detect_entry_conflicts
        other = Schedule.objects.create(name="Secret college plan", department=self.external, academic_term=self.term)
        # Simulate an inconsistent imported record to ensure validation does not leak it.
        entry = self.candidate()
        entry.save()
        ScheduleEntry.objects.filter(pk=entry.pk).update(schedule=other)
        conflicts = detect_entry_conflicts(self.candidate(), user=self.chair)
        self.assertIn("ROOM_OVERLAP", {c.code for c in conflicts})
        self.assertNotIn("Secret college plan", str(conflicts))
        self.assertTrue(all(c.other_entry_id is None for c in conflicts))

    def test_overlapping_calendar_terms_checked_nonoverlapping_terms_allowed(self):
        from datetime import date
        from academics.models import AcademicTerm
        from timetabling.models import Schedule, ScheduleEntry
        from timetabling.conflicts import detect_entry_conflicts
        entry = self.candidate()
        entry.save()
        overlap = AcademicTerm.objects.create(academic_year=self.term.academic_year, semester=self.term.semester, code="OVER", start_date=date(2026, 3, 1), end_date=date(2026, 7, 1))
        other = Schedule.objects.create(name="Overlapping calendar", department=self.department, academic_term=overlap)
        ScheduleEntry.objects.filter(pk=entry.pk).update(schedule=other)
        self.assertIn("ROOM_OVERLAP", {c.code for c in detect_entry_conflicts(self.candidate())})
        Schedule.objects.filter(pk=other.pk).update(academic_term=self.later)
        self.assertNotIn("ROOM_OVERLAP", {c.code for c in detect_entry_conflicts(self.candidate())})

    def test_database_invalid_interval_and_delete_protection(self):
        from django.db import IntegrityError, transaction
        from django.db.models.deletion import ProtectedError
        from timetabling.models import ScheduleEntry
        entry = self.candidate()
        entry.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.filter(pk=entry.pk).update(end_time=time(8))
        with self.assertRaises(ProtectedError):
            self.assignment.delete()
        with self.assertRaises(ProtectedError):
            self.schedule.delete()

    def test_department_section_program_and_requirement_term_constraints(self):
        from core.models import Program
        from timetabling.models import ClassSection
        self.section.program = Program.objects.create(department=self.external, code="X", name="Outside")
        with self.assertRaises(ValidationError):
            self.section.save()
        later_section = ClassSection.objects.create(department=self.department, academic_term=self.later, code="LATER")
        self.requirement.section = later_section
        with self.assertRaises(ValidationError):
            self.requirement.save()

    def test_available_sparse_records_do_not_restrict_unlisted_times(self):
        from workloads.models import FacultyAvailability
        from timetabling.conflicts import detect_entry_conflicts
        FacultyAvailability.objects.create(faculty=self.faculty, academic_term=self.term, day_of_week=2, start_time=time(12), end_time=time(13), availability_type="available")
        self.assertNotIn("FACULTY_UNAVAILABLE", {c.code for c in detect_entry_conflicts(self.candidate())})

    def test_scheduled_teaching_assignment_removal_explains_dependency(self):
        from django.urls import reverse
        from audit.models import AuditLog
        self.candidate().save()
        self.client.force_login(self.chair)
        response = self.client.post(reverse("workloads:assignments-delete", args=[self.assignment.pk]) + f"?academic_term={self.term.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remove its timetable meetings first")
        self.assertFalse(AuditLog.objects.filter(action="facultysubjectassignment.deleted").exists())

    def test_full_validation_does_not_leak_imported_outside_entry(self):
        from timetabling.models import ScheduleEntry
        from timetabling.conflicts import get_schedule_conflicts
        from workloads.models import FacultySubjectAssignment
        outside = FacultySubjectAssignment.objects.create(faculty=self.records[self.external.pk]["faculty-management"], subject_offering=self.offerings[self.external.pk])
        entry = self.candidate()
        entry.save()
        ScheduleEntry.objects.filter(pk=entry.pk).update(assignment=outside)
        conflicts = get_schedule_conflicts(self.schedule, user=self.chair)
        self.assertIn("PROTECTED_ENTRY", {c.code for c in conflicts})
        self.assertNotIn("Person3", str(conflicts))

    def test_changed_dependency_during_validation_cannot_mark_validated(self):
        from unittest.mock import patch
        from timetabling.conflicts import get_schedule_conflicts
        from timetabling.mutations import validate_schedule
        from workloads.models import FacultyAvailability
        self.candidate().save()
        def change_after_read(schedule, **kwargs):
            result = get_schedule_conflicts(schedule, **kwargs)
            FacultyAvailability.objects.create(faculty=self.faculty, academic_term=self.term, day_of_week=1, start_time=time(9), end_time=time(10), availability_type="unavailable")
            return result
        with patch("timetabling.mutations.get_schedule_conflicts", side_effect=change_after_read):
            report = validate_schedule(user=self.chair, schedule_id=self.schedule.pk)
        self.assertIn("CONCURRENT_CHANGE", report["categories"])
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "draft")

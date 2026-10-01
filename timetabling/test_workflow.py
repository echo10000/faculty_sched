"""Human review, versioning, and official selection regressions."""

from datetime import time
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404

from audit.models import AuditLog
from .conflicts import get_schedule_conflicts
from .forms import ScheduleForm
from .generation import accept_generation, request_generation
from .generation_inputs import GenerationOverrides
from .models import (
    ActiveSchedule, AssignmentMeetingRequirement, ClassSection, OfferingRequirement,
    OfficialResourceBooking, Schedule, ScheduleApprovalSnapshot, ScheduleEntry,
    ScheduleWorkflowEvent, SchedulingConfiguration,
)
from .tests import TimetableFixture
from .workflow import approve_schedule, return_schedule, revise_approved_schedule, submit_schedule


class WorkflowTests(TimetableFixture):
    def setUp(self):
        super().setUp()
        self.entry = self.candidate()
        self.entry.save()
        self.schedule.refresh_from_db()

    def warning_codes(self, schedule=None):
        schedule = schedule or self.schedule
        return sorted({finding.code for finding in get_schedule_conflicts(schedule, user=self.chair)
                       if finding.severity == "WARNING"})

    def submit(self, schedule=None, user=None, *, token=None, acknowledgements=None):
        schedule = schedule or self.schedule
        return submit_schedule(
            user=user or self.chair, schedule_id=schedule.pk,
            revision_token=token if token is not None else schedule.revision_token,
            acknowledged_warnings=(self.warning_codes(schedule) if acknowledgements is None
                                   else acknowledgements),
        )

    def approve(self, schedule=None, user=None, *, token=None, acknowledgements=None):
        schedule = schedule or self.schedule
        return approve_schedule(
            user=user or self.dean, schedule_id=schedule.pk,
            revision_token=token if token is not None else schedule.revision_token,
            acknowledged_warnings=(self.warning_codes(schedule) if acknowledgements is None
                                   else acknowledgements),
        )

    def test_submission_requires_current_revision_and_warning_acknowledgement(self):
        warnings = self.warning_codes()
        self.assertTrue(warnings)
        with self.assertRaises(ValidationError):
            self.submit(token="old-revision")
        with self.assertRaises(ValidationError):
            self.submit(acknowledgements=[])
        self.assertFalse(ScheduleWorkflowEvent.objects.exists())
        self.submit()
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, Schedule.Status.UNDER_REVIEW)
        self.assertEqual(self.schedule.submitted_by, self.chair)
        self.assertEqual(sorted(self.schedule.submitted_warning_codes), warnings)
        self.assertEqual(self.schedule.submitted_revision_token, self.schedule.revision_token)
        self.assertEqual(list(ScheduleWorkflowEvent.objects.values_list("action", flat=True)), ["submitted"])
        self.assertTrue(AuditLog.objects.filter(action="schedule.submitted").exists())

    def test_blocking_validation_and_missing_meetings_prevent_submission(self):
        ScheduleEntry.objects.filter(pk=self.entry.pk).delete()
        with self.assertRaises(ValidationError):
            self.submit()
        self.assertFalse(ScheduleWorkflowEvent.objects.exists())

    def test_return_preserves_history_and_resubmission(self):
        self.submit()
        first_revision = self.schedule.revision_token
        with self.assertRaises(ValidationError):
            return_schedule(user=self.chair, schedule_id=self.schedule.pk,
                            revision_token=first_revision, remarks="Needs work")
        returned = return_schedule(user=self.dean, schedule_id=self.schedule.pk,
                                   revision_token=first_revision, remarks="  Recheck room  ")
        self.assertEqual(returned.status, Schedule.Status.NEEDS_REVISION)
        self.assertNotEqual(returned.revision_token, first_revision)
        self.assertEqual(ScheduleWorkflowEvent.objects.get(action="returned").remarks, "Recheck room")
        self.submit(schedule=returned)
        self.assertEqual(list(ScheduleWorkflowEvent.objects.values_list("action", flat=True)),
                         ["submitted", "returned", "resubmitted"])

    def test_reviewer_separation_scope_and_capability(self):
        self.submit()
        with self.assertRaises(PermissionDenied):
            self.approve(user=self.chair)
        with self.assertRaises(PermissionDenied):
            self.approve(user=self.staff)
        outsider = self.make_user("outside-reviewer", "staff", college=self.other_college)
        with self.assertRaises(PermissionDenied):
            self.approve(user=outsider)
        with self.assertRaises(PermissionDenied):
            self.submit(user=outsider)
        self.assertFalse(ScheduleApprovalSnapshot.objects.exists())

    def test_explicit_staff_grants_remain_scoped(self):
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="timetabling", codename__in=(
                "submit_schedule", "change_schedule", "view_schedule", "view_scheduleentry")))
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="academics", codename="view_academicterm"))
        self.staff = type(self.staff).objects.get(pk=self.staff.pk)
        self.submit(user=self.staff)
        self.assertEqual(self.schedule.pk, ScheduleWorkflowEvent.objects.get(action="submitted").schedule_id)
        outside = Schedule.objects.create(name="Other college schedule", academic_term=self.term,
                                          department=self.external)
        with self.assertRaises(Http404):
            submit_schedule(user=self.staff, schedule_id=outside.pk,
                            revision_token=outside.revision_token)

    def test_approval_rechecks_revision_dependencies_and_warnings(self):
        self.submit()
        with self.assertRaises(ValidationError):
            self.approve(token="old-revision")
        with self.assertRaises(ValidationError):
            self.approve(acknowledgements=[])
        self.room.capacity = 12
        self.room.save()
        with self.assertRaises(ValidationError):
            self.approve()
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, Schedule.Status.UNDER_REVIEW)
        self.assertFalse(ScheduleApprovalSnapshot.objects.exists())
        self.assertFalse(ActiveSchedule.objects.exists())

    def test_approval_creates_frozen_snapshot_bookings_selection_and_audit(self):
        self.submit()
        approved = self.approve()
        self.assertEqual(approved.status, Schedule.Status.APPROVED)
        self.assertEqual(ActiveSchedule.objects.get(academic_term=self.term,
                                                    department=self.department).schedule, approved)
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=approved)
        self.assertEqual(snapshot.revision_token, approved.revision_token)
        frozen = snapshot.payload["entries"][0]
        self.assertEqual(frozen["entry_id"], self.entry.pk)
        self.assertEqual(frozen["faculty_id"], self.faculty.pk)
        self.assertEqual(frozen["section_id"], self.section.pk)
        self.assertEqual(frozen["room_id"], self.room.pk)
        self.assertEqual(frozen["start_time"], "09:00:00")
        self.assertEqual(snapshot.payload["warning_acknowledgements"]["reviewer_codes"],
                         self.warning_codes())
        bookings = OfficialResourceBooking.objects.filter(schedule_entry=self.entry)
        self.assertGreater(bookings.count(), 1)
        self.assertTrue(all(row.booking_date.isoweekday() == 1 for row in bookings))
        self.assertTrue(AuditLog.objects.filter(action="schedule.approved").exists())
        self.assertTrue(AuditLog.objects.filter(action="schedule.official_selected").exists())

    def test_revision_clones_and_replacement_retains_history(self):
        self.submit()
        old = self.approve()
        old_snapshot = ScheduleApprovalSnapshot.objects.get(schedule=old).payload
        old_booking_count = OfficialResourceBooking.objects.count()
        clone = revise_approved_schedule(user=self.chair, schedule_id=old.pk,
                                         revision_token=old.revision_token)
        self.assertEqual(clone.family_id, old.family_id)
        self.assertEqual(clone.version_number, old.version_number + 1)
        self.assertEqual(clone.parent_version_id, old.pk)
        self.assertEqual(clone.status, Schedule.Status.DRAFT)
        self.assertEqual(clone.entries.count(), old.entries.count())
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, old.pk)
        self.assertEqual(OfficialResourceBooking.objects.count(), old_booking_count)
        self.submit(schedule=clone)
        self.approve(schedule=clone)
        old.refresh_from_db()
        self.assertEqual(old.status, Schedule.Status.APPROVED)
        self.assertEqual(ScheduleApprovalSnapshot.objects.get(schedule=old).payload, old_snapshot)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, clone.pk)
        self.assertFalse(OfficialResourceBooking.objects.filter(schedule_entry__schedule=old).exists())
        self.assertEqual(OfficialResourceBooking.objects.filter(schedule_entry__schedule=clone).count(),
                         old_booking_count)
        self.assertTrue(AuditLog.objects.filter(action="schedule.official_replaced").exists())

    def test_failed_replacement_rolls_back_every_consequence(self):
        self.submit()
        old = self.approve()
        clone = revise_approved_schedule(user=self.chair, schedule_id=old.pk,
                                         revision_token=old.revision_token)
        self.submit(schedule=clone)
        audit_before = AuditLog.objects.count()
        booking_ids = list(OfficialResourceBooking.objects.values_list("pk", flat=True))
        with patch("timetabling.workflow.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.approve(schedule=clone)
        clone.refresh_from_db()
        self.assertEqual(clone.status, Schedule.Status.UNDER_REVIEW)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, old.pk)
        self.assertEqual(list(OfficialResourceBooking.objects.values_list("pk", flat=True)), booking_ids)
        self.assertFalse(ScheduleApprovalSnapshot.objects.filter(schedule=clone).exists())
        self.assertFalse(ScheduleWorkflowEvent.objects.filter(schedule=clone, action="approved").exists())
        self.assertEqual(AuditLog.objects.count(), audit_before)

    def test_approved_version_rejects_regular_mutation_services(self):
        from .mutations import remove_entry, save_entry
        self.submit()
        approved = self.approve()
        with self.assertRaises(ValidationError):
            save_entry(user=self.chair, schedule_id=approved.pk, data={
                "assignment": self.assignment.pk, "room": self.room.pk,
                "day_of_week": 2, "start_time": "09:00", "end_time": "10:00",
                "meeting_type": "lecture",
            })
        with self.assertRaises(ValidationError):
            remove_entry(user=self.chair, schedule_id=approved.pk, pk=self.entry.pk)
        self.assertEqual(approved.entries.count(), 1)

    def test_database_preserves_approved_version_snapshot_and_history(self):
        self.submit()
        approved = self.approve()
        approved.name = "Changed through model save"
        with self.assertRaises(IntegrityError), transaction.atomic():
            approved.save(update_fields=["name"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Schedule.objects.filter(pk=approved.pk).update(name="Changed after approval")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.create(
                schedule=approved, assignment=self.assignment, room=self.room,
                day_of_week=2, start_time=time(9), end_time=time(10), meeting_type="lecture",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.filter(pk=self.entry.pk).update(start_time=time(10))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleEntry.objects.filter(pk=self.entry.pk).delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleApprovalSnapshot.objects.filter(schedule=approved).update(payload={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleWorkflowEvent.objects.filter(schedule=approved).delete()
        self.assertEqual(approved.entries.count(), 1)
        self.assertTrue(ScheduleApprovalSnapshot.objects.filter(schedule=approved).exists())

    def test_cross_scope_conflict_redacts_protected_identity(self):
        from workloads.models import FacultySubjectAssignment

        secret_schedule = Schedule.objects.create(
            name="Hidden schedule name", academic_term=self.term, department=self.external,
        )
        secret_section = ClassSection.objects.create(
            academic_term=self.term, department=self.external, code="HIDDEN-SECTION",
        )
        external_offering = self.offerings[self.external.pk]
        OfferingRequirement.objects.create(subject_offering=external_offering,
                                           section=secret_section)
        secret_faculty = self.records[self.external.pk]["faculty-management"]
        secret_assignment = FacultySubjectAssignment.objects.create(
            faculty=secret_faculty, subject_offering=external_offering,
        )
        ScheduleEntry.objects.create(
            schedule=secret_schedule, assignment=secret_assignment, room=self.room,
            day_of_week=1, start_time=time(9), end_time=time(10), meeting_type="lecture",
        )
        findings = get_schedule_conflicts(self.schedule, user=self.chair)
        room_conflicts = [finding for finding in findings if finding.code == "ROOM_OVERLAP"]
        self.assertTrue(room_conflicts)
        redacted = " ".join(finding.message for finding in room_conflicts)
        self.assertNotIn("Hidden schedule name", redacted)
        self.assertNotIn("HIDDEN-SECTION", redacted)
        self.assertNotIn(str(secret_faculty), redacted)
        self.assertTrue(all(finding.other_entry_id is None for finding in room_conflicts))
        with self.assertRaises(ValidationError):
            self.submit()

    def test_metadata_and_entry_edits_rotate_revision_token(self):
        from .mutations import save_entry, save_record

        original_token = self.schedule.revision_token
        saved, form = save_record(
            user=self.chair, form_class=ScheduleForm,
            data={"academic_term": self.term.pk, "department": self.department.pk,
                  "name": "Updated timetable"}, pk=self.schedule.pk,
        )
        self.assertFalse(form.errors)
        self.assertNotEqual(saved.revision_token, original_token)
        after_metadata = saved.revision_token
        edited, form, conflicts = save_entry(
            user=self.chair, schedule_id=self.schedule.pk, pk=self.entry.pk,
            data={"assignment": self.assignment.pk, "room": self.room.pk,
                  "day_of_week": 1, "start_time": "10:00", "end_time": "11:00",
                  "meeting_type": "lecture"},
        )
        self.assertIsNotNone(edited, (form.errors, conflicts))
        self.schedule.refresh_from_db()
        self.assertNotEqual(self.schedule.revision_token, after_metadata)

    def test_generation_request_and_accept_respect_review_lifecycle(self):
        # The Phase 5 fixture's exact meeting requirements produce a real
        # proposal, then the lifecycle changes without changing its inputs.
        SchedulingConfiguration.objects.create(
            academic_term=self.term, department=self.department,
            allowed_weekdays=[1, 2, 3, 4, 5], earliest_start=time(8),
            latest_end=time(17), slot_increment_minutes=30,
            solver_time_limit_seconds=10, random_seed=17, worker_count=1,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment, meeting_type="lecture",
            meetings_per_week=2, duration_minutes=60,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment, meeting_type="laboratory",
            meetings_per_week=1, duration_minutes=180,
        )
        # Remove the ad hoc meeting from this workflow fixture so the solver
        # sees the same empty editable schedule as the Phase 5 service tests.
        self.entry.delete()
        for state in (Schedule.Status.UNDER_REVIEW, Schedule.Status.APPROVED):
            with self.subTest(state=state):
                run = request_generation(
                    user=self.chair, schedule_id=self.schedule.pk,
                    strategy="FILL_GAPS", overrides=GenerationOverrides(),
                )
                self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
                Schedule.objects.filter(pk=self.schedule.pk).update(status=state)
                with self.assertRaises(ValidationError):
                    request_generation(
                        user=self.chair, schedule_id=self.schedule.pk,
                        strategy="FILL_GAPS", overrides=GenerationOverrides(),
                    )
                refused = accept_generation(user=self.chair, run_id=run.pk)
                self.assertEqual(refused.status, "STALE")
                self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())
                if state == Schedule.Status.UNDER_REVIEW:
                    Schedule.objects.filter(pk=self.schedule.pk).update(status=Schedule.Status.DRAFT)

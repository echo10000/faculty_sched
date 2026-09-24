"""Scoped browser routes and the optional development review seed."""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .conflicts import get_schedule_conflicts
from .models import ActiveSchedule, OfficialResourceBooking, Schedule, ScheduleApprovalSnapshot, ScheduleWorkflowEvent
from .tests import TimetableFixture
from .workflow import submit_schedule


class WorkflowPageTests(TimetableFixture):
    def setUp(self):
        self.entry = self.candidate()
        self.entry.save()
        self.warnings = sorted({item.code for item in get_schedule_conflicts(self.schedule, user=self.chair)
                                if item.severity == "WARNING"})

    def url(self, name, *args):
        return reverse(f"timetabling:{name}", args=args)

    def test_review_queue_history_official_and_cross_scope_are_scoped(self):
        self.client.force_login(self.chair)
        self.assertContains(self.client.get(self.url("schedule-review", self.schedule.pk)), "Submit for review")
        self.assertContains(self.client.get(self.url("schedule-history", self.schedule.pk)), "v1")
        submit_schedule(user=self.chair, schedule_id=self.schedule.pk,
                        revision_token=self.schedule.revision_token,
                        acknowledged_warnings=self.warnings)
        self.client.force_login(self.dean)
        self.assertContains(self.client.get(self.url("review-queue")), self.schedule.name)
        self.assertContains(self.client.get(self.url("schedule-review", self.schedule.pk)), "Approve as official")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url("review-queue")).status_code, 403)
        self.client.force_login(self.make_user("phase7-foreign-dean", "dean", college=self.other_college))
        self.assertNotContains(self.client.get(self.url("review-queue")), self.schedule.name)
        self.assertEqual(self.client.get(self.url("schedule-review", self.schedule.pk)).status_code, 404)

    def test_transition_is_post_only_csrf_protected_and_rejects_stale_revision(self):
        url = self.url("schedule-submit", self.schedule.pk)
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(url).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.chair)
        self.assertEqual(secure.post(url, {"revision_token": self.schedule.revision_token}).status_code, 403)
        response = self.client.post(url, {"revision_token": "stale"})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ScheduleWorkflowEvent.objects.exists())

    def test_approval_route_creates_official_page_and_frozen_review(self):
        submit_schedule(user=self.chair, schedule_id=self.schedule.pk,
                        revision_token=self.schedule.revision_token,
                        acknowledged_warnings=self.warnings)
        self.client.force_login(self.dean)
        response = self.client.post(self.url("schedule-approve", self.schedule.pk), {
            "revision_token": self.schedule.revision_token,
            "acknowledged_warnings": self.warnings,
            "remarks": "Reviewed in person.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(self.url("official-schedules")), self.schedule.name)
        self.assertContains(self.client.get(self.url("schedule-review", self.schedule.pk)), "Frozen approval record")
        self.assertTrue(ActiveSchedule.objects.filter(schedule=self.schedule).exists())


class ReviewSeedTests(TestCase):
    @override_settings(DEBUG=True)
    def test_optional_seed_submits_once_without_official_history(self):
        call_command("seed_foundation", create_users=True, with_review=True, verbosity=0)
        first = Schedule.objects.get(name="Example manual timetable", department__code="DEMO-D1")
        self.assertEqual(first.status, Schedule.Status.UNDER_REVIEW)
        event_ids = list(ScheduleWorkflowEvent.objects.values_list("pk", flat=True))
        entry_ids = list(first.entries.values_list("pk", flat=True))
        call_command("seed_foundation", create_users=True, with_review=True, verbosity=0)
        first.refresh_from_db()
        self.assertEqual(first.status, Schedule.Status.UNDER_REVIEW)
        self.assertEqual(list(ScheduleWorkflowEvent.objects.values_list("pk", flat=True)), event_ids)
        self.assertEqual(list(first.entries.values_list("pk", flat=True)), entry_ids)
        self.assertFalse(ActiveSchedule.objects.exists())
        self.assertFalse(ScheduleApprovalSnapshot.objects.exists())
        self.assertFalse(OfficialResourceBooking.objects.exists())
        self.assertTrue(get_user_model().objects.filter(username="dev.chair").exists())

"""The redesigned desk presents authoritative checks without changing the workflow."""
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase
from django.urls import reverse

from audit.models import AuditLog
from .models import ActiveSchedule, Schedule, ScheduleGenerationRun, ScheduleWorkflowEvent
from .preparation import publication_findings, publication_groups
from .tests import TimetableFixture


class PreparePrototypeTests(TimetableFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def page(self, stage):
        return self.client.get(reverse("timetabling:prepare"), {
            "academic_term": self.term.pk, "department": self.department.pk,
            "schedule": self.schedule.pk, "stage": stage,
        })

    def test_publication_checks_match_review_without_writes(self):
        before = (self.schedule.revision_token, self.schedule.validated_signature,
                  ScheduleGenerationRun.objects.count(), ScheduleWorkflowEvent.objects.count(),
                  AuditLog.objects.count())
        page = self.page(7)
        review = self.client.get(reverse("timetabling:schedule-review", args=[self.schedule.pk]))
        expected = {(item["code"], item["severity"], item["message"]) for item in review.context["conflicts"]}
        actual = {(item["code"], item["severity"], item["message"]) for item in page.context["publication_findings"]}
        self.assertEqual(actual, expected)
        self.assertIn("EMPTY_SCHEDULE", {item[0] for item in actual})
        self.assertEqual(page.context["finding_summary"]["errors"], sum(item[1] == "ERROR" for item in actual))
        self.schedule.refresh_from_db()
        after = (self.schedule.revision_token, self.schedule.validated_signature,
                 ScheduleGenerationRun.objects.count(), ScheduleWorkflowEvent.objects.count(),
                 AuditLog.objects.count())
        self.assertEqual(before, after)
        self.assertEqual(self.schedule.status, "draft")
        self.assertFalse(ActiveSchedule.objects.exists())
        for finding in page.context["publication_findings"]:
            if finding.get("url"):
                self.assertEqual(self.client.get(finding["url"]).status_code, 200)

    def test_meeting_remedies_keep_the_selected_workflow_context(self):
        entry = self.candidate()
        entry.save()
        page = self.page(6)
        self.assertEqual(page.context["draft_meeting_count"], 1)
        meetings = [meeting for finding in page.context["publication_findings"] for meeting in finding["meetings"]]
        self.assertTrue(meetings)
        for meeting in meetings:
            query = parse_qs(urlsplit(meeting["url"]).query)
            self.assertEqual(query["workflow_schedule"], [str(self.schedule.pk)])
            self.assertEqual(self.client.get(meeting["url"]).status_code, 200)
        self.assertContains(page, "Accepted draft &amp; manual meetings")

    def test_no_permission_does_not_assume_a_clear_check(self):
        with patch.object(type(self.staff), "has_perm", return_value=False), patch(
                "timetabling.finalization_validation.publication_conflicts") as validator:
            self.assertIsNone(publication_findings(self.staff, self.schedule))
            validator.assert_not_called()

    def test_published_history_is_distinguished_from_official_selection(self):
        Schedule.objects.filter(pk=self.schedule.pk).update(status="approved")
        page = self.page(5)
        self.assertContains(page, "historical published version")
        self.assertNotContains(page, "Generate department timetable")
        published_review = self.page(7)
        self.assertIsNone(published_review.context["publication_findings"])
        self.assertContains(published_review, "Frozen publication record")
        self.assertNotContains(published_review, "Current publication checks")
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=self.schedule, selected_by=self.staff)
        page = self.page(5)
        self.assertContains(page, "current official schedule")
        self.assertNotContains(page, "historical published version")

    def test_large_publication_overview_preserves_every_finding_and_fix(self):
        findings = [
            {"code": "TEACHING_HOURS_INCOMPLETE", "severity": "ERROR",
             "message": f"Class {index}: weekly hours incomplete.",
             "remedy": "Adjust meetings to cover confirmed hours.", "meetings": [],
             "url": f"/fix/{index}/", "label": f"Fix class {index}"}
            for index in range(45)
        ]
        findings.append({"code": "MEETING_HOURS_WARNING", "severity": "WARNING",
                         "message": "Review additional hours.", "remedy": "Confirm hours.",
                         "meetings": [{"url": "/meeting/fix/", "label": "Affected meeting", "action": "Edit"}]})
        for stage in (6, 7):
            with patch("timetabling.views.publication_findings", return_value=findings):
                page = self.page(stage)
            self.assertEqual(page.context["finding_summary"], {"errors": 45, "warnings": 1})
            grouped = [item for group in page.context["publication_groups"] for item in group["findings"]]
            self.assertEqual(grouped, findings)
            self.assertContains(page, "Not ready for publication")
            self.assertContains(page, 'class="prepare-validation-group"', count=2)
            for finding in findings:
                self.assertContains(page, finding["message"], count=1)
                if finding.get("url"):
                    self.assertContains(page, f'href="{finding["url"]}"', count=1)
            self.assertContains(page, 'href="/meeting/fix/"', count=1)

    def test_stage_six_action_tracks_proposals_and_saved_meetings(self):
        page = self.page(6)
        heading = page.content.decode().split('<header class="prepare-task-heading">')[1].split('</header>')[0]
        self.assertIn("Go to Generate timetable", heading)
        self.assertNotIn("Review generated proposal", heading)
        run = ScheduleGenerationRun.objects.create(
            schedule=self.schedule, academic_term=self.term, department=self.department,
            requested_by=self.staff, strategy="FILL_GAPS", status="INPUT_INVALID")
        self.assertIsNone(self.page(6).context["review_proposal"])
        ScheduleGenerationRun.objects.filter(pk=run.pk).update(status="PROPOSAL_READY")
        page = self.page(6)
        self.assertEqual(page.context["review_proposal"].pk, run.pk)
        self.assertContains(page, "Review generated proposal")
        ScheduleGenerationRun.objects.filter(pk=run.pk).update(status="ACCEPTED")
        self.candidate().save()
        page = self.page(6)
        self.assertContains(page, "Inspect publication blockers")
        self.assertContains(page, "Review timetable")
        self.assertIsNone(page.context["review_proposal"])
        with patch("timetabling.views.publication_findings", return_value=[]):
            page = self.page(6)
        heading = page.content.decode().split('<header class="prepare-task-heading">')[1].split('</header>')[0]
        self.assertIn("Review timetable", heading)

    def test_complete_context_is_collapsed_but_incomplete_context_stays_editable(self):
        self.assertContains(self.page(1), '<details class="prepare-context" >')
        self.client.force_login(self.admin)
        page = self.client.get(reverse("timetabling:prepare"), {
            "academic_term": self.term.pk, "department": self.external.pk, "stage": 1})
        self.assertContains(page, '<details class="prepare-context" open>')


class PublicationGroupingTests(SimpleTestCase):
    def test_only_equivalent_known_metadata_groups_without_deduplication(self):
        base = {"code": "TEACHING_HOURS_INCOMPLETE", "severity": "ERROR", "remedy": "Restore hours.", "message": "Repeated message"}
        rows = [base.copy(), base.copy(), {**base, "severity": "WARNING"},
                {**base, "remedy": "Different remedy."},
                {**base, "code": "UNRECOGNIZED"}, {**base, "code": "UNRECOGNIZED"}]
        groups = publication_groups(rows)
        self.assertEqual([len(group["findings"]) for group in groups], [2, 1, 1, 1, 1])
        self.assertEqual([row for group in groups for row in group["findings"]], rows)
        self.assertIs(groups[0]["findings"][0], rows[0])

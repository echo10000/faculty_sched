"""Navigation preserves validated context and published/scoped read boundaries."""
from datetime import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import AdminProfile
from .conflicts import Conflict
from .models import Schedule, ScheduleApprovalSnapshot, ScheduleEntry
from .presentation import present_conflicts
from .tests import TimetableFixture


class Links(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.urls = []
        self.feed(response.content.decode())

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.extend(value for name, value in attrs if name == "href")


class ConnectedWorkspaceTests(TimetableFixture):
    def test_faculty_picker_and_tabs_preserve_filters(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse("timetabling:timetable", args=[self.schedule.pk]), {
            "faculty": self.faculty.pk, "room": self.room.pk,
        })
        # Assignments without meetings remain selectable in the faculty panel.
        self.assertIn(self.faculty, page.context["faculty_choices"])
        self.assertEqual(page.context["selected_faculty"], self.faculty)
        links = Links(page).urls
        conflict_url = next(url for url in links if urlsplit(url).path == reverse("timetabling:conflicts", args=[self.schedule.pk]))
        self.assertEqual(parse_qs(urlsplit(conflict_url).query), {
            "faculty": [str(self.faculty.pk)], "room": [str(self.room.pk)],
        })
        assignment_url = next(url for url in links if urlsplit(url).path == reverse("workloads:assignments") and "faculty=" in url)
        assignment_page = self.client.get(assignment_url)
        self.assertEqual(assignment_page.status_code, 200)
        self.assertEqual(assignment_page.context["faculty_context"], self.faculty)
        self.assertEqual(assignment_page.context["schedule"], self.schedule)
        self.assertEqual(assignment_page.context["term"], self.term)
        detail_url = next(url for url in Links(assignment_page).urls if urlsplit(url).path == reverse("workloads:assignments-detail", args=[self.assignment.pk]))
        detail = self.client.get(detail_url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, f'aria-current="page">{self.assignment}</li>', html=False)

    def test_invalid_faculty_is_not_reflected_into_navigation(self):
        self.client.force_login(self.chair)
        outside = self.records[self.external.pk]["faculty-management"]
        page = self.client.get(reverse("timetabling:timetable", args=[self.schedule.pk]), {"faculty": outside.pk})
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["filter_form"].errors)
        self.assertIsNone(page.context["selected_faculty"])
        self.assertEqual(page.context["meeting_filters"], {})
        self.assertNotIn(outside, page.context["faculty_choices"])

    def test_schedule_context_rejects_other_scope_and_term(self):
        self.client.force_login(self.chair)
        outside = Schedule.objects.create(name="Outside", academic_term=self.term, department=self.external)
        url = reverse("workloads:assignments")
        self.assertEqual(self.client.get(url, {"academic_term": self.term.pk, "schedule": outside.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {"academic_term": self.later.pk, "schedule": self.schedule.pk}).status_code, 404)

    def test_conflict_links_resolve_only_visible_entries_and_respect_state(self):
        entry = self.candidate()
        entry.save()
        outside_schedule = Schedule.objects.create(name="Outside", academic_term=self.term, department=self.external)
        outside = self.candidate()
        outside.schedule = outside_schedule
        from workloads.models import FacultySubjectAssignment
        outside.assignment = FacultySubjectAssignment.objects.create(
            faculty=self.records[self.external.pk]["faculty-management"],
            subject_offering=self.offerings[self.external.pk])
        outside.room = self.records[self.external.pk]["rooms"]
        outside.save()
        finding = Conflict("ROOM_OVERLAP", "ERROR", "Overlap", "Choose another time", entry.pk, outside.pk)
        rows = present_conflicts(self.chair, [finding])
        self.assertEqual(len(rows[0]["meetings"]), 1)
        self.assertEqual(rows[0]["meetings"][0]["url"], reverse("timetabling:entries-edit", args=[self.schedule.pk, entry.pk]))
        filtered = present_conflicts(self.chair, [finding], {"faculty": self.faculty.pk})
        self.assertEqual(parse_qs(urlsplit(filtered[0]["meetings"][0]["url"]).query), {"faculty": [str(self.faculty.pk)]})
        Schedule.objects.filter(pk=self.schedule.pk).update(status="approved")
        published = present_conflicts(self.chair, [finding])[0]["meetings"][0]
        self.assertEqual(published["action"], "View meeting")
        self.assertIn(f"#meeting-{entry.pk}", published["url"])
        self.assertEqual(finding.entry_id, entry.pk)

    def test_unscheduled_action_preselects_assignment_without_writing(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse("timetabling:unscheduled", args=[self.schedule.pk]))
        url = next(url for url in Links(page).urls if "assignment=" in url)
        editor = self.client.get(url)
        self.assertEqual(editor.status_code, 200)
        self.assertEqual(editor.context["form"].initial["assignment"], self.assignment.pk)
        self.assertFalse(ScheduleEntry.objects.exists())

    def test_published_version_is_not_preselected_in_generator_navigation(self):
        self.client.force_login(self.chair)
        Schedule.objects.filter(pk=self.schedule.pk).update(status="approved")
        page = self.client.get(reverse("timetabling:timetable", args=[self.schedule.pk]))
        generator_url = next(url for url in Links(page).urls if urlsplit(url).path == reverse("timetabling:generator"))
        self.assertNotIn("schedule", parse_qs(urlsplit(generator_url).query))
        self.assertEqual(self.client.get(generator_url).status_code, 200)

    def test_portal_calendar_and_table_use_the_same_frozen_publication(self):
        from .mutations import validate_schedule
        from .workflow import finalize_schedule

        entry = self.candidate()
        entry.end_time = time(11)
        entry.save()
        ScheduleEntry.objects.create(schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=2, start_time=time(9), end_time=time(12), meeting_type="laboratory")
        report = validate_schedule(user=self.staff, schedule_id=self.schedule.pk)
        warnings = {item.code for item in report["conflicts"] if item.severity == "WARNING"}
        self.schedule.refresh_from_db()
        finalize_schedule(user=self.staff, schedule_id=self.schedule.pk,
            revision_token=self.schedule.revision_token, acknowledged_warnings=warnings)
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=self.schedule)
        saved_payload = snapshot.payload
        user = get_user_model().objects.create_user(username="calendar-reader")
        self.faculty.user = user
        self.faculty.save()
        AdminProfile.objects.create(user=user, role="faculty")
        self.client.force_login(user)
        url = reverse("faculty-portal:schedule")
        calendar = self.client.get(url, {"term": self.term.pk})
        table = self.client.get(url, {"term": self.term.pk, "view": "table"})
        self.assertContains(calendar, 'aria-label="My official weekly timetable"')
        self.assertContains(table, '<th scope="col">Subject</th>')
        self.assertEqual(calendar.context["entries"], table.context["entries"])
        self.assertEqual(len(calendar.context["entries"]), 2)
        self.offering.subject.title = "Changed live title"
        self.offering.subject.save()
        self.assertNotContains(self.client.get(url), "Changed live title")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload, saved_payload)

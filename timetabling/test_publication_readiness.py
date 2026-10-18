"""Published presentation stays frozen while editable versions use current data."""
from copy import deepcopy
from datetime import time
import json

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import AdminProfile
from faculty.portal import teaching_records
from reporting.services import build_report
from .conflicts import get_schedule_conflicts
from .forms import TimetableFilter
from .models import ActiveSchedule, ScheduleApprovalSnapshot, SchedulingConfiguration
from .tests import TimetableFixture
from .timetable import NOT_RECORDED, filtered_entries, schedule_labels
from .workflow import _snapshot_payload, approve_schedule, revise_approved_schedule, submit_schedule


class PublishedPresentationTests(TimetableFixture):
    def publish(self, schedule=None):
        schedule = schedule or self.schedule
        codes = {finding.code for finding in get_schedule_conflicts(schedule, user=self.chair)
                 if finding.severity == "WARNING"}
        schedule = submit_schedule(user=self.chair, schedule_id=schedule.pk,
                                   revision_token=schedule.revision_token, acknowledged_warnings=codes)
        return approve_schedule(user=self.dean, schedule_id=schedule.pk,
                                revision_token=schedule.revision_token, acknowledged_warnings=codes)

    def rename_resources(self):
        for obj, values in (
            (self.offering.subject, {"code": "RENAMED-SUBJECT", "title": "Renamed subject title"}),
            (self.room, {"code": "RENAMED-ROOM", "name": "Renamed room name"}),
            (self.faculty, {"first_name": "Renamed", "last_name": "Instructor"}),
            (self.department, {"name": "Renamed department"}),
            (self.section, {"code": "RENAMED-SECTION"}),
        ):
            type(obj).objects.filter(pk=obj.pk).update(**values)

    def test_renamed_live_records_do_not_change_staff_faculty_or_official_rows(self):
        entry = self.candidate()
        entry.save()
        published = self.publish()
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=published)
        saved = deepcopy(snapshot.payload)
        row = saved["entries"][0]
        reader = get_user_model().objects.create_user(username="snapshot-faculty-reader")
        self.faculty.user = reader
        self.faculty.save()
        AdminProfile.objects.create(user=reader, role="faculty")
        self.rename_resources()
        self.client.force_login(self.staff)
        for name in ("timetable", "schedules-detail", "schedule-review", "schedule-history"):
            with self.subTest(page=name):
                page = self.client.get(reverse("timetabling:" + name, args=[published.pk]))
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, saved["schedule"]["department_name"])
                self.assertEqual(page.context["schedule_labels"]["department_name"], saved["schedule"]["department_name"])
                if name != "schedule-history":
                    for key in ("subject_code", "subject_title", "faculty_name", "room_code", "section_code"):
                        self.assertContains(page, row[key])
                    for changed in ("RENAMED-SUBJECT", "Renamed subject title", "RENAMED-ROOM", "RENAMED-SECTION"):
                        self.assertNotContains(page, changed)
        self.client.force_login(reader)
        faculty_page = self.client.get(reverse("faculty-portal:schedule"), {"term": self.term.pk})
        self.assertEqual(faculty_page.status_code, 200)
        self.assertContains(faculty_page, row["subject_title"])
        self.assertNotContains(faculty_page, "Renamed subject title")
        self.assertEqual(teaching_records(reader, self.term.pk)["entries"][0]["faculty_name"], row["faculty_name"])
        report = build_report(self.staff, "master", {"term": str(self.term.pk)},
                              institution="Fictional University", scope="Test scope")[0]
        self.assertIn(row["subject_code"], str(report["rows"]))
        self.assertIn(row["faculty_name"], str(report["rows"]))
        self.assertNotIn("RENAMED-SUBJECT", str(report["rows"]))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload, saved)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, published.pk)

    def test_published_filters_search_frozen_values_and_preserve_identity(self):
        entry = self.candidate()
        entry.save()
        published = self.publish()
        old_code = self.offering.subject.code
        self.rename_resources()
        self.client.force_login(self.staff)
        url = reverse("timetabling:timetable", args=[published.pk])
        page = self.client.get(url, {"faculty": self.faculty.pk, "room": self.room.pk,
                                     "section": self.section.pk, "day_of_week": 1, "q": old_code})
        self.assertEqual([meeting.pk for meeting in page.context["entries"]], [entry.pk])
        self.assertContains(page, f'id="meeting-{entry.pk}"')
        self.assertEqual(len(self.client.get(url, {"q": "RENAMED-SUBJECT"}).context["entries"]), 0)
        outsider = self.records[self.external.pk]["faculty-management"]
        outside_page = self.client.get(url, {"faculty": outsider.pk})
        self.assertEqual(outside_page.status_code, 200)
        self.assertTrue(outside_page.context["filter_form"].errors)
        self.assertEqual(outside_page.context["entries"], [])

    def test_published_schedule_indexes_use_their_own_frozen_metadata(self):
        self.candidate().save()
        published = self.publish()
        metadata = ScheduleApprovalSnapshot.objects.get(schedule=published).payload["schedule"]
        self.rename_resources()
        type(published.family).objects.filter(pk=published.family_id).update(name="Renamed family")
        type(self.term).objects.filter(pk=self.term.pk).update(code="CURRENT-TERM")
        self.client.force_login(self.staff)
        for route in ("schedules", "official-schedules"):
            with self.subTest(route=route):
                page = self.client.get(reverse("timetabling:" + route))
                self.assertEqual(page.status_code, 200)
                item = next(iter(page.context["page_obj"]))
                version = item.schedule if route == "official-schedules" else item
                self.assertEqual(version.publication_labels["department_name"], metadata["department_name"])
                self.assertEqual(version.publication_labels["academic_term_code"], metadata["academic_term_code"])
                for key in ("name", "family_name", "academic_term_code", "department_name"):
                    self.assertContains(page, metadata[key])
                self.assertNotContains(page, "Renamed family")

    def test_revision_is_live_and_each_published_version_keeps_its_own_snapshot(self):
        self.candidate().save()
        first = self.publish()
        old = deepcopy(ScheduleApprovalSnapshot.objects.get(schedule=first).payload)
        self.rename_resources()
        revision = revise_approved_schedule(user=self.chair, schedule_id=first.pk,
                                            revision_token=first.revision_token)
        self.client.force_login(self.staff)
        draft = self.client.get(reverse("timetabling:timetable", args=[revision.pk]))
        self.assertContains(draft, "RENAMED-SUBJECT")
        second = self.publish(revision)
        latest = self.client.get(reverse("timetabling:timetable", args=[second.pk]))
        self.assertContains(latest, "RENAMED-SUBJECT")
        history = self.client.get(reverse("timetabling:timetable", args=[first.pk]))
        self.assertContains(history, old["entries"][0]["subject_code"])
        self.assertNotContains(history, "RENAMED-SUBJECT")
        self.assertEqual(ScheduleApprovalSnapshot.objects.get(schedule=first).payload, old)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, second.pk)

    def test_legacy_missing_labels_are_unknown_and_never_filled_from_live_records(self):
        entry = self.candidate()
        entry.save()
        payload = _snapshot_payload(self.schedule, [], self.dean)
        for key in ("faculty_name", "room_code", "subject_title", "section_code"):
            payload["entries"][0].pop(key)
        payload["schedule"].pop("department_name")
        type(self.schedule).objects.filter(pk=self.schedule.pk).update(status="approved")
        ScheduleApprovalSnapshot.objects.create(schedule=self.schedule, approved_by=self.dean,
                                                revision_token=self.schedule.revision_token, payload=payload)
        self.schedule.refresh_from_db()
        form = TimetableFilter({}, user=self.staff)
        displayed = filtered_entries(self.staff, self.schedule, form)[0]
        self.assertEqual(displayed.pk, entry.pk)
        for field in ("faculty_label", "room_label", "subject_title_label", "section_label"):
            self.assertEqual(getattr(displayed, field), NOT_RECORDED)
        self.assertEqual(schedule_labels(self.schedule)["department_name"], NOT_RECORDED)
        self.assertEqual(ScheduleApprovalSnapshot.objects.get(schedule=self.schedule).payload, payload)

    def test_approved_version_with_no_snapshot_does_not_claim_live_labels_are_historical(self):
        self.candidate().save()
        type(self.schedule).objects.filter(pk=self.schedule.pk).update(status="approved")
        self.schedule.refresh_from_db()
        self.assertEqual(schedule_labels(self.schedule)["name"], NOT_RECORDED)
        displayed = filtered_entries(self.staff, self.schedule, TimetableFilter({}, user=self.staff))[0]
        self.assertEqual(displayed.subject_code_label, NOT_RECORDED)


class PublicationConfigurationTests(TimetableFixture):
    def configuration(self, **values):
        return SchedulingConfiguration.objects.create(
            academic_term=self.term, department=self.department, allowed_weekdays=[1, 2, 3, 4, 5],
            earliest_start=time(8), latest_end=time(18), slot_increment_minutes=30,
            solver_time_limit_seconds=5, random_seed=1, worker_count=1, **values)

    def test_configured_lunch_and_rest_are_json_serializable(self):
        self.configuration(lunch_break_enabled=True, lunch_start=time(12), lunch_end=time(13), faculty_rest_minutes=30)
        payload = _snapshot_payload(self.schedule, [], self.dean)
        self.assertEqual({key: payload["configuration"][key] for key in
                          ("lunch_break_enabled", "lunch_start", "lunch_end", "faculty_rest_minutes")},
                         {"lunch_break_enabled": True, "lunch_start": "12:00:00", "lunch_end": "13:00:00", "faculty_rest_minutes": 30})
        json.dumps(payload)

    def test_disabled_and_missing_configuration_are_distinct(self):
        self.assertEqual(_snapshot_payload(self.schedule, [], self.dean)["configuration"], {})
        self.configuration()
        configuration = _snapshot_payload(self.schedule, [], self.dean)["configuration"]
        self.assertIs(configuration["lunch_break_enabled"], False)
        self.assertIsNone(configuration["lunch_start"])
        self.assertIsNone(configuration["lunch_end"])
        self.assertEqual(configuration["faculty_rest_minutes"], 0)

    def test_historical_snapshot_is_not_rewritten_when_current_settings_change(self):
        self.candidate().save()
        payload = _snapshot_payload(self.schedule, [], self.dean)
        payload["configuration"] = {"allowed_weekdays": [1, 2, 3, 4, 5]}
        type(self.schedule).objects.filter(pk=self.schedule.pk).update(status="approved")
        snapshot = ScheduleApprovalSnapshot.objects.create(schedule=self.schedule, approved_by=self.dean,
                                                            revision_token=self.schedule.revision_token, payload=payload)
        self.configuration(lunch_break_enabled=True, lunch_start=time(12), lunch_end=time(13), faculty_rest_minutes=60)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("timetabling:schedule-review", args=[self.schedule.pk])).status_code, 200)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload, payload)
        self.assertEqual(snapshot.payload["configuration"], {"allowed_weekdays": [1, 2, 3, 4, 5]})
        self.assertNotIn("lunch_break_enabled", snapshot.payload["configuration"])

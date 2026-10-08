"""Scoped browser routes and the optional development review seed."""

from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .conflicts import get_schedule_conflicts
from .models import ActiveSchedule, OfficialResourceBooking, Schedule, ScheduleApprovalSnapshot, ScheduleWorkflowEvent
from .tests import TimetableFixture
from .workflow import submit_schedule


class OfficialScheduleCalendarTests(TimetableFixture):
    today = date(2026, 10, 10)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from academics.test_rollover import sample_calendar
        from workloads.models import FacultySubjectAssignment, SubjectOffering
        from .models import ClassSection, OfferingRequirement, ScheduleEntry
        from .mutations import validate_schedule
        from .workflow import finalize_schedule

        cls.official = {}
        # Publish real, complete schedules before enabling the term restriction.
        for term in (cls.term, cls.later):
            for department in (cls.department, cls.sibling, cls.external):
                offering, _ = SubjectOffering.objects.get_or_create(
                    academic_term=term, department=department,
                    subject=cls.records[department.pk]["subjects"],
                    defaults={"lecture_units": 2, "laboratory_units": 1,
                              "lecture_hours": 2, "laboratory_hours": 3},
                )
                section, _ = ClassSection.objects.get_or_create(
                    academic_term=term, department=department, code="A",
                    defaults={"expected_size": 30},
                )
                OfferingRequirement.objects.get_or_create(subject_offering=offering, defaults={"section": section})
                assignment, _ = FacultySubjectAssignment.objects.get_or_create(
                    subject_offering=offering, faculty=cls.records[department.pk]["faculty-management"],
                )
                schedule = Schedule.objects.create(
                    academic_term=term, department=department,
                    name=f"Official {department.code} {term.code}",
                )
                for day, end, kind in ((1, time(11), "lecture"), (2, time(12), "laboratory")):
                    ScheduleEntry.objects.create(
                        schedule=schedule, assignment=assignment,
                        room=cls.records[department.pk]["rooms"], day_of_week=day,
                        start_time=time(9), end_time=end, meeting_type=kind,
                    )
                report = validate_schedule(user=cls.admin, schedule_id=schedule.pk)
                if report["errors"]:
                    raise AssertionError(report["conflicts"])
                schedule.refresh_from_db()
                finalize_schedule(
                    user=cls.admin, schedule_id=schedule.pk, revision_token=schedule.revision_token,
                    acknowledged_warnings={item.code for item in report["conflicts"] if item.severity == "WARNING"},
                )
                cls.official[term.pk, department.pk] = schedule
        cls.calendar = sample_calendar()
        cls.calendar.academic_years.add(cls.term.academic_year)
        cls.calendar.last_rollover_date = cls.today
        cls.calendar.save()

    def official_ids(self, user, query, *, today=None):
        today = today or self.today
        # Rollover is covered separately; keep these tests focused on visibility.
        type(self.calendar).objects.filter(pk=self.calendar.pk).update(last_rollover_date=today)
        self.client.force_login(user)
        with patch("academics.calendar.timezone.localdate", return_value=today):
            response = self.client.get(reverse("timetabling:official-schedules"), query)
        self.assertEqual(response.status_code, 200)
        return {item.schedule_id for item in response.context["page_obj"]}

    def test_staff_current_term_with_valid_blank_and_omitted_parameters(self):
        expected = {self.official[self.later.pk, self.department.pk].pk}
        for query in ({}, {"academic_term": ""}, {"academic_term": self.later.pk}):
            with self.subTest(query=query):
                self.assertEqual(self.official_ids(self.staff, query), expected)

    def test_staff_cannot_select_historical_or_invalid_terms(self):
        for value in (self.term.pk, "invalid", 999999):
            with self.subTest(value=value):
                self.assertEqual(self.official_ids(self.staff, {"academic_term": value}), set())

    def test_staff_calendar_gap_has_no_official_selections(self):
        for query in ({}, {"academic_term": ""}, {"academic_term": self.later.pk}):
            with self.subTest(query=query):
                self.assertEqual(self.official_ids(self.staff, query, today=date(2027, 6, 10)), set())

    def test_department_and_college_scope_remain_enforced(self):
        current_department = self.official[self.later.pk, self.department.pk].pk
        current_sibling = self.official[self.later.pk, self.sibling.pk].pk
        self.assertEqual(self.official_ids(self.college_staff, {"academic_term": ""}),
                         {current_department, current_sibling})
        for department in (self.sibling, self.external):
            with self.subTest(department=department.code):
                self.assertEqual(self.official_ids(self.staff, {
                    "academic_term": self.later.pk, "department": department.pk,
                }), set())
        self.assertEqual(self.official_ids(self.college_staff, {
            "academic_term": "", "department": self.external.pk,
        }), set())

    def test_direct_access_matches_term_and_organization_policy(self):
        for today in (self.today, date(2027, 6, 10)):
            type(self.calendar).objects.filter(pk=self.calendar.pk).update(last_rollover_date=today)
            for user in (self.staff, self.college_staff, self.admin):
                self.client.force_login(user)
                for (term_id, department_id), schedule in self.official.items():
                    allowed = user == self.admin or (
                        today == self.today and term_id == self.later.pk
                        and department_id in ({self.department.pk, self.sibling.pk}
                                              if user == self.college_staff else {self.department.pk})
                    )
                    for route in ("schedules-detail", "schedule-review", "schedule-history"):
                        with self.subTest(today=today, user=user.username, schedule=schedule.name, route=route):
                            with patch("academics.calendar.timezone.localdate", return_value=today):
                                response = self.client.get(reverse(f"timetabling:{route}", args=[schedule.pk]))
                            self.assertEqual(response.status_code, 200 if allowed else 404)

    def test_admin_retains_all_terms_and_historical_filter_during_gap(self):
        all_ids = {schedule.pk for schedule in self.official.values()}
        historical = {schedule.pk for (term_id, _), schedule in self.official.items() if term_id == self.term.pk}
        for today in (self.today, date(2027, 6, 10)):
            for query, expected in (({}, all_ids), ({"academic_term": ""}, all_ids),
                                    ({"academic_term": self.term.pk}, historical)):
                with self.subTest(today=today, query=query):
                    self.assertEqual(self.official_ids(self.admin, query, today=today), expected)

    def test_disabled_calendar_retains_staff_access_to_own_other_terms(self):
        type(self.calendar).objects.filter(pk=self.calendar.pk).update(is_enabled=False)
        expected = {self.official[term.pk, self.department.pk].pk for term in (self.term, self.later)}
        self.assertEqual(self.official_ids(self.staff, {"academic_term": ""}), expected)
        self.assertEqual(self.official_ids(self.staff, {"academic_term": self.term.pk}),
                         {self.official[self.term.pk, self.department.pk].pk})


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
        self.assertContains(self.client.get(self.url("schedule-review", self.schedule.pk)), "Finalize and Publish")
        self.assertContains(self.client.get(self.url("schedule-history", self.schedule.pk)), "v1")
        submit_schedule(user=self.chair, schedule_id=self.schedule.pk,
                        revision_token=self.schedule.revision_token,
                        acknowledged_warnings=self.warnings)
        self.client.force_login(self.dean)
        self.assertContains(self.client.get(self.url("review-queue")), self.schedule.name)
        self.assertContains(self.client.get(self.url("schedule-review", self.schedule.pk)), "Finalize and Publish")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url("review-queue")).status_code, 200)
        self.client.force_login(self.make_user("phase7-foreign-dean", "staff", college=self.other_college))
        self.assertNotContains(self.client.get(self.url("review-queue")), self.schedule.name)
        self.assertEqual(self.client.get(self.url("schedule-review", self.schedule.pk)).status_code, 404)

    def test_transition_is_post_only_csrf_protected_and_rejects_stale_revision(self):
        url = self.url("schedule-finalize", self.schedule.pk)
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(url).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.chair)
        self.assertEqual(secure.post(url, {"revision_token": self.schedule.revision_token}).status_code, 403)
        response = self.client.post(url, {"revision_token": "stale"})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ScheduleWorkflowEvent.objects.exists())

    def test_staff_finalization_route_creates_official_page_and_frozen_record(self):
        from .models import ScheduleEntry
        from .mutations import validate_schedule
        from datetime import time
        ScheduleEntry.objects.filter(pk=self.entry.pk).update(end_time=time(11))
        ScheduleEntry.objects.create(schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=2, start_time=time(9), end_time=time(12), meeting_type='laboratory')
        validate_schedule(user=self.staff, schedule_id=self.schedule.pk)
        self.schedule.refresh_from_db()
        self.client.force_login(self.staff)
        response = self.client.post(self.url('schedule-finalize', self.schedule.pk), {
            'revision_token': self.schedule.revision_token, 'acknowledged_warnings': ['ROOM_CAPACITY_WARNING'], 'remarks': 'Reviewed and finalized.'})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(self.url('official-schedules')), self.schedule.name)
        self.assertContains(self.client.get(self.url('schedule-review', self.schedule.pk)), 'Frozen publication record')
        self.assertTrue(ActiveSchedule.objects.filter(schedule=self.schedule).exists())


class ReviewSeedTests(TestCase):
    @override_settings(DEBUG=True)
    def test_optional_seed_validates_without_publication(self):
        call_command("seed_foundation", create_users=True, with_review=True, verbosity=0)
        first = Schedule.objects.get(name="Example manual timetable", department__code="DEMO-D1")
        self.assertEqual(first.status, Schedule.Status.VALIDATED)
        event_ids = list(ScheduleWorkflowEvent.objects.values_list("pk", flat=True))
        entry_ids = list(first.entries.values_list("pk", flat=True))
        call_command("seed_foundation", create_users=True, with_review=True, verbosity=0)
        first.refresh_from_db()
        self.assertEqual(first.status, Schedule.Status.VALIDATED)
        self.assertEqual(list(ScheduleWorkflowEvent.objects.values_list("pk", flat=True)), event_ids)
        self.assertEqual(list(first.entries.values_list("pk", flat=True)), entry_ids)
        self.assertFalse(ActiveSchedule.objects.exists())
        self.assertFalse(ScheduleApprovalSnapshot.objects.exists())
        self.assertFalse(OfficialResourceBooking.objects.exists())
        self.assertTrue(get_user_model().objects.filter(username="dev.staff").exists())
        self.assertFalse(get_user_model().objects.filter(username__in=("dev.chair", "dev.dean")).exists())

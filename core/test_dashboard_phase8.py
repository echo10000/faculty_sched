"""Phase 8 dashboard integration and scope regressions."""

from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from faculty.models import Faculty
from timetabling.models import (
    ActiveSchedule, ClassSection, OfferingRequirement, OfficialResourceBooking,
    Schedule, ScheduleApprovalSnapshot, ScheduleEntry,
    ScheduleGenerationRun,
)
from timetabling.tests import TimetableFixture
from workloads.calculation import calculate_workload
from workloads.models import FacultySubjectAssignment, WorkloadRecommendationRun


class DashboardPhase8Tests(TimetableFixture):
    def dashboard(self, user, term=None, **filters):
        self.client.force_login(user)
        params = {"academic_term": (term or self.term).pk, **filters}
        response = self.client.get("/", params)
        self.assertEqual(response.status_code, 200)
        return response.context

    @staticmethod
    def counts(section):
        return {row["key"]: row["count"] for row in section["counts"]}

    def test_role_scope_and_forged_organization_filters(self):
        Schedule.objects.create(academic_term=self.term, department=self.sibling, name="Sibling draft")
        Schedule.objects.create(academic_term=self.term, department=self.external, name="External draft")
        expected = [(self.admin, 3), (self.dean, 2), (self.chair, 1)]
        for user, total in expected:
            with self.subTest(role=user.username):
                context = self.dashboard(user, department=self.external.pk,
                                         college=self.other_college.pk)
                self.assertEqual(sum(self.counts(context["monitoring"]["schedules"]).values()), total)
                self.assertEqual(sum(self.counts(context["monitoring"]["workload"]).values()), total)
                self.assertEqual(context["monitoring"]["rooms"]["active_count"], total)
        self.client.force_login(self.staff)
        self.assertIsNone(self.client.get("/").context["monitoring"]["schedules"])
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label__in=["academics", "timetabling"],
            codename__in=["view_academicterm", "view_schedule"],
        ))
        self.staff = type(self.staff).objects.get(pk=self.staff.pk)
        staff = self.dashboard(self.staff, department=self.external.pk)
        self.assertEqual(sum(self.counts(staff["monitoring"]["schedules"]).values()), 1)
        self.assertIsNone(staff["monitoring"]["workload"])

    def test_workload_counts_match_authoritative_service_without_double_counting(self):
        self.policy(recommended_load=Decimal("3"), maximum_load=Decimal("6"))
        extra = Faculty.objects.create(employee_id="EXTRA", first_name="Extra",
                                       last_name="Person", home_department=self.department)
        FacultySubjectAssignment.objects.create(faculty=extra, subject_offering=self.offering,
                                                 share=Decimal("0.5"))
        expected = [calculate_workload(person, self.term)["status"]
                    for person in (self.faculty, extra)]
        distribution = self.counts(self.dashboard(self.chair)["monitoring"]["workload"])
        self.assertEqual(sum(distribution.values()), 2)
        for status in set(expected):
            self.assertEqual(distribution[status], expected.count(status))
        self.assertEqual(self.counts(self.dashboard(self.chair, self.later)["monitoring"]["workload"])["UNCONFIGURED"], 2)

    def test_term_switch_and_historical_term(self):
        older = self.term
        newer = self.later
        Schedule.objects.create(academic_term=newer, department=self.department, name="Later draft")
        Schedule.objects.create(academic_term=newer, department=self.department, name="Later revision")
        current = self.dashboard(self.chair, older)
        future = self.dashboard(self.chair, newer)
        self.assertEqual(current["selected_term"].pk, older.pk)
        self.assertEqual(future["selected_term"].pk, newer.pk)
        self.assertEqual(sum(self.counts(current["monitoring"]["schedules"]).values()), 1)
        self.assertEqual(sum(self.counts(future["monitoring"]["schedules"]).values()), 2)
        self.assertEqual({term.pk for term in current["term_options"]}, {older.pk, newer.pk})
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get("/", {"academic_term": "not-an-id"}).status_code, 400)
        self.assertEqual(self.client.get("/", {"academic_term": 999999}).status_code, 404)

    def test_schedule_lifecycle_review_and_official_version_are_distinct(self):
        self.candidate().save()
        historical = self.schedule
        Schedule.objects.filter(pk=historical.pk).update(status=Schedule.Status.APPROVED)
        historical.refresh_from_db()
        official = Schedule.objects.create(
            academic_term=self.term, department=self.department, family=historical.family,
            parent_version=historical, version_number=2, name="Official v2",
        )
        ScheduleEntry.objects.create(schedule=official, assignment=self.assignment, room=self.room,
                                     day_of_week=2, start_time=time(9), end_time=time(11),
                                     meeting_type="lecture")
        Schedule.objects.filter(pk=official.pk).update(status=Schedule.Status.APPROVED)
        official.refresh_from_db()
        snapshot = ScheduleApprovalSnapshot.objects.create(
            schedule=official, revision_token=official.revision_token,
            dependency_signature="signature", approved_by=self.dean, payload={"entries": []},
        )
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=official, selected_by=self.dean)
        for status in (Schedule.Status.VALIDATED, Schedule.Status.UNDER_REVIEW,
                       Schedule.Status.NEEDS_REVISION):
            draft = Schedule.objects.create(academic_term=self.term, department=self.department,
                                            name=f"{status} version")
            Schedule.objects.filter(pk=draft.pk).update(status=status)
        schedules = self.dashboard(self.chair)["monitoring"]["schedules"]
        lifecycle = self.counts(schedules)
        self.assertEqual(lifecycle[Schedule.Status.APPROVED], 2)
        self.assertEqual(lifecycle[Schedule.Status.UNDER_REVIEW], 1)
        self.assertEqual(lifecycle[Schedule.Status.NEEDS_REVISION], 1)
        self.assertEqual(lifecycle[Schedule.Status.VALIDATED], 1)
        self.assertEqual(schedules["official_count"], 1)
        self.assertEqual(schedules["pending_count"], 1)
        self.assertEqual(schedules["returned_count"], 1)
        self.assertEqual([row["selection"].schedule_id for row in schedules["recent_official"]],
                         [official.pk])
        self.assertEqual(schedules["recent_official"][0]["snapshot"].pk, snapshot.pk)
        self.assertEqual(schedules["recent_official"][0]["meeting_count"], 1)

    def test_room_hours_use_current_official_meetings_and_scope(self):
        own_entry = self.candidate()
        own_entry.save()
        Schedule.objects.filter(pk=self.schedule.pk).update(status=Schedule.Status.APPROVED)
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=self.schedule, selected_by=self.dean)
        other = Schedule.objects.create(academic_term=self.term, department=self.external,
                                        name="Other official")
        foreign_assignment = FacultySubjectAssignment.objects.create(
            faculty=self.records[self.external.pk]["faculty-management"],
            subject_offering=self.offerings[self.external.pk],
        )
        foreign_room = self.records[self.external.pk]["rooms"]
        foreign_section = ClassSection.objects.create(
            academic_term=self.term, department=self.external, code="FOREIGN",
        )
        OfferingRequirement.objects.create(
            subject_offering=foreign_assignment.subject_offering, section=foreign_section,
        )
        foreign_entry = ScheduleEntry.objects.create(
            schedule=other, assignment=foreign_assignment, room=foreign_room,
            day_of_week=1, start_time=time(9), end_time=time(12), meeting_type="lecture",
        )
        Schedule.objects.filter(pk=other.pk).update(status=Schedule.Status.APPROVED)
        ActiveSchedule.objects.create(academic_term=self.term, department=self.external,
                                      schedule=other, selected_by=self.admin)
        monday = self.term.start_date + timedelta(days=(7 - self.term.start_date.weekday()) % 7)
        OfficialResourceBooking.objects.create(
            schedule_entry=own_entry, booking_date=monday, start_time=time(9), end_time=time(10),
            faculty=self.faculty, room=self.room, section=self.section,
        )
        OfficialResourceBooking.objects.create(
            schedule_entry=own_entry, booking_date=monday + timedelta(days=7),
            start_time=time(9), end_time=time(10),
            faculty=self.faculty, room=self.room, section=self.section,
        )
        OfficialResourceBooking.objects.create(
            schedule_entry=foreign_entry, booking_date=monday, start_time=time(9), end_time=time(12),
            faculty=foreign_assignment.faculty, room=foreign_room, section=foreign_section,
        )
        chair_rooms = self.dashboard(self.chair)["monitoring"]["rooms"]
        self.assertEqual(chair_rooms["used_count"], 1)
        self.assertEqual(chair_rooms["scheduled_hours"], Decimal("2"))
        self.assertNotIn(foreign_room.pk, {row["room_id"] for row in chair_rooms["rows"]})
        admin_rooms = self.dashboard(self.admin)["monitoring"]["rooms"]
        self.assertEqual(admin_rooms["used_count"], 2)
        self.assertEqual(admin_rooms["scheduled_hours"], Decimal("5"))

    def test_recent_conflict_findings_use_existing_validator(self):
        self.candidate().save()
        section = self.dashboard(self.chair)["monitoring"]["schedules"]
        self.assertEqual(len(section["issues"]), 1)
        self.assertEqual(section["issues"][0]["schedule"].pk, self.schedule.pk)
        self.assertGreaterEqual(section["issues"][0]["warning_count"], 1)

    def test_staff_monitoring_requires_each_permission_and_remains_scoped(self):
        Schedule.objects.create(academic_term=self.term, department=self.external,
                                name="Protected external timetable")
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label__in=["academics", "timetabling", "scheduling"],
            codename__in=["view_academicterm", "view_schedule", "view_scheduleentry", "view_room"],
        ))
        staff = type(self.staff).objects.get(pk=self.staff.pk)
        context = self.dashboard(staff)
        self.assertEqual(sum(self.counts(context["monitoring"]["schedules"]).values()), 1)
        self.assertIsNotNone(context["monitoring"]["rooms"])
        self.assertIsNone(context["monitoring"]["workload"])
        self.assertIsNone(context["monitoring"]["generation"])
        self.assertIsNone(context["monitoring"]["balancing"])
        self.assertNotContains(self.client.get("/", {"academic_term": self.term.pk}),
                               "Protected external timetable")

    def test_recent_generation_and_balancing_runs_are_scoped_and_term_filtered(self):
        own_generation = ScheduleGenerationRun.objects.create(
            schedule=self.schedule, academic_term=self.term, department=self.department,
            requested_by=self.chair, strategy=ScheduleGenerationRun.Strategy.FILL_GAPS,
            status=ScheduleGenerationRun.Status.PROPOSAL_READY,
        )
        foreign_schedule = Schedule.objects.create(academic_term=self.term, department=self.external,
                                                   name="Foreign draft")
        foreign_generation = ScheduleGenerationRun.objects.create(
            schedule=foreign_schedule, academic_term=self.term, department=self.external,
            requested_by=self.admin, strategy=ScheduleGenerationRun.Strategy.FILL_GAPS,
            status=ScheduleGenerationRun.Status.FAILED,
        )
        own_balancing = WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.department, initiated_by=self.chair,
            status=WorkloadRecommendationRun.Status.PROPOSAL_READY,
        )
        foreign_balancing = WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.external, initiated_by=self.admin,
            status=WorkloadRecommendationRun.Status.FAILED,
        )
        for key, included, excluded in (
            ("generation", own_generation, foreign_generation),
            ("balancing", own_balancing, foreign_balancing),
        ):
            with self.subTest(kind=key):
                section = self.dashboard(self.chair)["monitoring"][key]
                self.assertEqual([run.pk for run in section["recent"]], [included.pk])
                self.assertEqual(sum(self.counts(section).values()), 1)
                self.assertEqual({run.pk for run in self.dashboard(self.admin)["monitoring"][key]["recent"]},
                                 {included.pk, excluded.pk})
                self.assertEqual(sum(self.counts(self.dashboard(self.chair, self.later)["monitoring"][key]).values()), 0)

    def test_dashboard_queries_do_not_grow_per_faculty(self):
        self.client.force_login(self.chair)
        with CaptureQueriesContext(connection) as before:
            self.client.get("/", {"academic_term": self.term.pk})
        Faculty.objects.bulk_create([
            Faculty(employee_id=f"PERF{index}", first_name="Test", last_name="Faculty",
                    home_department=self.department)
            for index in range(12)
        ])
        with CaptureQueriesContext(connection) as after:
            response = self.client.get("/", {"academic_term": self.term.pk})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(after), len(before) + 6)
        self.assertLess(len(after), 220)

    def test_ungranted_staff_cannot_open_monitoring_pages(self):
        self.client.force_login(self.staff)
        for url in (
            reverse("workloads:monitor") + f"?academic_term={self.term.pk}",
            reverse("timetabling:review-queue"),
            reverse("timetabling:official-schedules"),
            reverse("timetabling:generation-runs"),
            reverse("workloads:balancing-runs"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

"""Phase 9 data-source, export, and scope regressions."""

from datetime import time
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Permission
from django.db import connection
from django.http import Http404
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook

from audit.models import AuditLog
from timetabling.models import (
    ActiveSchedule, ClassSection, Schedule, ScheduleApprovalSnapshot, ScheduleGenerationRun,
    ScheduleWorkflowEvent,
)
from timetabling.tests import TimetableFixture
from workloads.models import WorkloadRecommendationRun
from .exports import render_csv, render_pdf, render_xlsx
from .services import _filter_context, build_report


class ExportRendererTests(SimpleTestCase):
    def test_formats_share_rows_and_spreadsheet_text_is_safe(self):
        report = {"title": "Faculty report", "source_label": "CURRENT DATA",
                  "meta": [("Institution", "Example")], "headers": ["Name", "Load", "Start"],
                  "rows": [["=CMD()", 3.5, time(9)]], "filename_base": "faculty-report",
                  "orientation": "portrait"}
        csv_text = render_csv(report)
        self.assertIn("'=CMD()", csv_text)
        self.assertIn("3.5", csv_text)
        workbook = load_workbook(BytesIO(render_xlsx(report)))
        sheet = workbook.active
        self.assertEqual(sheet["A6"].value, "'=CMD()")
        self.assertIsInstance(sheet["B6"].value, float)
        self.assertEqual(sheet["C6"].value, time(9))
        self.assertTrue(render_pdf(report).startswith(b"%PDF"))


class ReportTests(TimetableFixture):
    def report(self, user, kind, **params):
        return build_report(user, kind, params,
                            institution="Example University", scope="Test scope")[0]

    def test_master_filter_queries_do_not_grow_with_sections_and_schedules(self):
        params = {"term": str(self.term.pk)}

        def filter_queries():
            with CaptureQueriesContext(connection) as captured:
                selected = _filter_context(self.chair, "master", params)
            return len(captured), {
                item["name"]: len(item["options"])
                for item in selected["filters"]
                if item["name"] in {"section", "schedule"}
            }

        baseline_queries, baseline_options = filter_queries()
        for index in range(2, 7):
            ClassSection.objects.create(
                academic_term=self.term, department=self.department, code=f"S{index}",
            )
            Schedule.objects.create(
                academic_term=self.term, department=self.department,
                family=self.schedule.family, version_number=index, name=f"Version {index}",
            )
        expanded_queries, expanded_options = filter_queries()
        self.assertEqual(expanded_options["section"] - baseline_options["section"], 5)
        self.assertEqual(expanded_options["schedule"] - baseline_options["schedule"], 5)
        self.assertLessEqual(expanded_queries, baseline_queries)

    def test_catalog_and_direct_export_permission(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("reporting:index"))
        self.assertEqual(response.status_code, 200)
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="academics", codename="view_academicterm",
        ))
        self.staff = type(self.staff).objects.get(pk=self.staff.pk)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("reporting:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Master academic schedule")
        self.assertEqual(self.client.get(reverse("reporting:export", args=["master", "csv"])).status_code, 200)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("reporting:index")), "Faculty workload")

    def test_workload_uses_authoritative_calculator_and_term(self):
        from workloads.calculation import calculate_workload
        report = self.report(self.chair, "workload", term=self.term.pk, faculty=self.faculty.pk)
        row = report["rows"][0]
        current = calculate_workload(self.faculty, self.term)
        self.assertEqual(row[6], current["teaching_units"])
        self.assertEqual(row[8], current["assigned_load"])
        self.assertEqual(row[11], current["status"].replace("_", " ").title())
        later = self.report(self.chair, "workload", term=self.later.pk, faculty=self.faculty.pk)
        self.assertEqual(later["rows"][0][6], 0)
        self.assertEqual(self.report(self.chair, "workload", term=self.term.pk,
                                     status="OVERLOAD")["rows"], [])

    def test_forged_scope_filters_and_direct_urls(self):
        with self.assertRaises(Http404):
            self.report(self.chair, "master", term=self.term.pk, department=self.external.pk)
        external_room = self.records[self.external.pk]["rooms"]
        with self.assertRaises(Http404):
            self.report(self.chair, "room-schedule", term=self.term.pk, room=external_room.pk)
        self.client.force_login(self.chair)
        response = self.client.get(reverse("reporting:export", args=["room-schedule", "csv"]),
                                   {"term": self.term.pk, "room": external_room.pk})
        self.assertEqual(response.status_code, 404)

    def _approved(self):
        entry = self.candidate()
        entry.save()
        Schedule.objects.filter(pk=self.schedule.pk).update(status="approved")
        self.schedule.refresh_from_db()
        payload = {"schedule": {"family_name": self.schedule.family.name,
                                "version_number": 1, "department_name": str(self.department)},
                   "entries": [{"entry_id": entry.pk, "faculty_id": self.faculty.pk,
                                "faculty_name": str(self.faculty), "subject_code": self.offering.subject.code,
                                "subject_title": self.offering.subject.title, "section_id": self.section.pk,
                                "section_code": self.section.code, "room_id": self.room.pk,
                                "room_code": self.room.code, "day_of_week": 1,
                                "start_time": "09:00:00", "end_time": "10:00:00",
                                "meeting_type": "lecture"}]}
        ScheduleApprovalSnapshot.objects.create(
            schedule=self.schedule, revision_token=self.schedule.revision_token,
            dependency_signature="a" * 64, approved_by=self.dean, payload=payload,
        )
        return entry

    def test_working_and_historical_sources_never_conflate(self):
        self.candidate().save()
        working = self.report(self.chair, "master", term=self.term.pk, schedule=self.schedule.pk)
        self.assertIn("NOT OFFICIAL", working["source_label"])
        self.assertEqual(len(working["rows"]), 1)
        self.assertEqual(self.report(self.chair, "official", term=self.term.pk)["rows"], [])
        self.assertEqual(self.report(self.chair, "historical", term=self.term.pk,
                                     schedule=self.schedule.pk)["rows"], [])

    def test_official_selection_and_immutable_historical_labels(self):
        self._approved()
        old_code = self.offering.subject.code
        self.offering.subject.code = "CHANGED"
        self.offering.subject.save()
        historical = self.report(self.chair, "historical", term=self.term.pk,
                                 schedule=self.schedule.pk)
        self.assertEqual(historical["rows"][0][5], old_code)
        self.assertIn("HISTORICAL", historical["source_label"])
        self.assertEqual(self.report(self.chair, "official", term=self.term.pk)["rows"], [])
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=self.schedule, selected_by=self.dean)
        official = self.report(self.chair, "official", term=self.term.pk)
        self.assertEqual(official["rows"][0][5], old_code)
        self.assertIn("CURRENT OFFICIAL", official["source_label"])
        newer = Schedule.objects.create(academic_term=self.term, department=self.department,
                                        family=self.schedule.family, version_number=2, name="Later approved")
        Schedule.objects.filter(pk=newer.pk).update(status="approved")
        self.assertEqual(len(self.report(self.chair, "official", term=self.term.pk)["rows"]), 1)

    def test_faculty_section_room_and_master_filters(self):
        self._approved()
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=self.schedule, selected_by=self.dean)
        for kind, name, obj in (("faculty-schedule", "faculty", self.faculty),
                                ("section-schedule", "section", self.section),
                                ("room-schedule", "room", self.room),
                                ("master", "faculty", self.faculty)):
            with self.subTest(kind=kind):
                self.assertEqual(len(self.report(self.chair, kind, term=self.term.pk,
                                                 **{name: obj.pk})["rows"]), 1)
        self.assertEqual(self.report(self.chair, "master", term=self.later.pk)["rows"], [])

    def test_approval_and_phase_five_six_histories(self):
        ScheduleWorkflowEvent.objects.create(schedule=self.schedule, action="submitted",
                                              actor=self.chair, revision_token=self.schedule.revision_token,
                                              remarks="Please review")
        approval = self.report(self.chair, "approvals", term=self.term.pk)
        self.assertEqual(approval["rows"][0][3], "Submitted")
        self.assertEqual(approval["rows"][0][6], "Please review")
        ScheduleGenerationRun.objects.create(schedule=self.schedule, academic_term=self.term,
                                             department=self.department, requested_by=self.chair,
                                             strategy="FILL_GAPS", status="DISCARDED")
        generation = self.report(self.chair, "generation", term=self.term.pk)
        self.assertEqual(generation["rows"][0][8], "Discarded")
        WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.department, initiated_by=self.chair,
            status="ACCEPTED", comparison={"faculty": [{"faculty": str(self.faculty),
                                                    "before": {"assigned_load": "3.0"},
                                                    "after": {"assigned_load": "2.0"}}],
                                           "summary": {"affected_faculty": 1,
                                                       "imbalance_before": "50", "imbalance_after": "0"}},
            proposed_assignments=[{"status": "REASSIGN"}],
        )
        balancing = self.report(self.chair, "balancing", term=self.term.pk)
        self.assertEqual(balancing["rows"][0][7:10], [Decimal("3.0"), Decimal("2.0"), "REASSIGN"])
        self.assertEqual(balancing["rows"][0][-3:], [1, Decimal("50"), Decimal("0")])

    def test_scope_blocks_snapshot_and_schedule_id(self):
        self._approved()
        with self.assertRaises(Http404):
            self.report(self.chair, "historical", term=self.term.pk, schedule=999999)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("reporting:export", args=["historical", "pdf"]),
                                         {"term": self.term.pk, "schedule": self.schedule.pk}).status_code, 200)

    def test_official_export_formats_and_print(self):
        self._approved()
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=self.schedule, selected_by=self.dean)
        self.client.force_login(self.chair)
        for fmt, mime in (("pdf", "application/pdf"), ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                          ("csv", "text/csv")):
            with self.subTest(fmt=fmt):
                response = self.client.get(reverse("reporting:export", args=["official", fmt]),
                                           {"term": self.term.pk})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response["Content-Type"].startswith(mime))
                self.assertIn(f".{fmt}", response["Content-Disposition"])
        printed = self.client.get(reverse("reporting:print", args=["official"]), {"term": self.term.pk})
        self.assertContains(printed, "CURRENT OFFICIAL SCHEDULE")

    def test_conflict_report_is_live_phase_four(self):
        self.candidate().save()
        report = self.report(self.chair, "conflicts", term=self.term.pk, schedule=self.schedule.pk)
        self.assertIn("PHASE 4", report["source_label"])
        self.assertEqual(report["headers"][0], "Severity")
        self.assertTrue(any(self.offering.subject.code in row[2] for row in report["rows"]))


class ExportAuthorizationTests(TimetableFixture):
    def test_staff_default_export_access_is_audited(self):
        for user in (self.admin, self.dean, self.chair):
            with self.subTest(role=user.username):
                self.assertTrue(user.has_perm("core.export_report"))
        self.assertTrue(self.staff.has_perm("core.export_report"))
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="academics", codename="view_academicterm",
        ))
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="timetabling", codename="view_schedulegenerationrun",
        ))
        self.staff = type(self.staff).objects.get(pk=self.staff.pk)
        self.client.force_login(self.staff)
        detail_url = reverse("reporting:detail", args=["generation"])
        print_url = reverse("reporting:print", args=["generation"])
        export_url = reverse("reporting:export", args=["generation", "csv"])
        for url in (detail_url, print_url):
            with self.subTest(url=url):
                response = self.client.get(url, {"term": self.term.pk})
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(self.client.get(detail_url), "Export CSV")
        self.assertEqual(self.client.get(export_url, {"term": self.term.pk}).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="report.exported").exists())
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="core", codename="export_report",
        ))
        self.staff = type(self.staff).objects.get(pk=self.staff.pk)
        self.client.force_login(self.staff)
        response = self.client.get(export_url, {"term": self.term.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(self.client.get(detail_url), "Export CSV")

    def test_every_successful_format_is_audited_and_scope_remains_checked(self):
        self.client.force_login(self.chair)
        for fmt in ("csv", "xlsx", "pdf"):
            with self.subTest(fmt=fmt):
                response = self.client.get(reverse("reporting:export", args=["generation", fmt]),
                                           {"term": self.term.pk})
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertTrue(AuditLog.objects.filter(
                    action="report.exported", actor=self.chair,
                    details={"report": "generation", "format": fmt},
                ).exists())
        response = self.client.get(reverse("reporting:export", args=["room-schedule", "csv"]),
                                   {"term": self.term.pk, "room": self.records[self.external.pk]["rooms"].pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(AuditLog.objects.filter(action="report.exported").count(), 3)

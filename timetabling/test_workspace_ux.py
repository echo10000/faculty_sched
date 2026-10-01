"""Workspace presentation uses existing permissions and authoritative data."""
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from faculty.models import FacultyQualification
from workloads.calculation import calculate_workload
from .models import Schedule
from .tests import TimetableFixture


class WorkspaceUXTests(TimetableFixture):
    def test_unscheduled_is_private_and_scope_checked(self):
        url = reverse("timetabling:unscheduled", args=[self.schedule.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.staff.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_schedule", "view_scheduleentry", "view_academicterm"],
            content_type__app_label__in=["academics", "timetabling"],
        ))
        self.client.force_login(self.staff)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertIsNotNone(page.context["unscheduled_assignments"])
        outside = Schedule.objects.create(academic_term=self.term, department=self.external, name="Outside")
        self.assertEqual(self.client.get(reverse("timetabling:unscheduled", args=[outside.pk])).status_code, 404)

    def test_meeting_presence_is_not_presented_as_complete_coverage(self):
        self.client.force_login(self.chair)
        url = reverse("timetabling:unscheduled", args=[self.schedule.pk])
        page = self.client.get(url)
        self.assertEqual(page.context["summary"]["without_meetings"], 1)
        self.assertContains(page, "No meetings recorded")
        self.candidate().save()
        page = self.client.get(url)
        self.assertEqual(page.context["summary"]["without_meetings"], 0)
        self.assertTrue(page.context["requirement_findings"])
        self.assertContains(page, "partial coverage")
        self.assertNotContains(page, "No available room")

    def test_active_navigation_and_context_across_schedule_tabs(self):
        self.client.force_login(self.chair)
        for name in ("schedules-detail", "timetable", "conflicts", "unscheduled", "schedule-review", "schedule-history"):
            with self.subTest(name=name):
                page = self.client.get(reverse("timetabling:" + name, args=[self.schedule.pk]))
                self.assertEqual(page.status_code, 200)
                active = [link["route"] for link in page.context["navigation_links"] if link["active"]]
                self.assertEqual(active, ["timetabling:schedules"])
                self.assertContains(page, 'aria-label="Schedule views"')
                self.assertContains(page, 'aria-label="Breadcrumb"')

    def test_role_navigation_preserves_grants(self):
        for user in (self.admin, self.dean, self.chair, self.staff):
            self.client.force_login(user)
            page = self.client.get(reverse("home"))
            self.assertEqual(page.status_code, 200)
            groups = page.context["navigation_groups"]
            self.assertEqual(groups[0]["label"], "Dashboard")
            if user == self.staff:
                self.assertGreater(len(groups), 1)
            else:
                self.assertEqual(len([link for group in groups for link in group["links"]]), len(page.context["navigation_links"]))
                self.assertEqual(page.context["workflow_groups"][0]["label"], "Academic setup")

    def test_directory_workload_matches_existing_calculation(self):
        self.policy(recommended_load=12, maximum_load=18)
        self.client.force_login(self.chair)
        page = self.client.get(reverse("faculty-management:list"), {"academic_term": self.term.pk})
        self.assertEqual(page.status_code, 200)
        person = page.context["object_list"][0]
        expected = calculate_workload(self.faculty, self.term)
        self.assertEqual(person.workload["assigned_load"], expected["assigned_load"])
        self.assertEqual(person.workload["status"], expected["status"])
        self.assertNotContains(page, self.records[self.external.pk]["faculty-management"].employee_id)

    def test_qualification_tab_checks_explicit_permission_and_scope(self):
        FacultyQualification.objects.create(faculty=self.faculty, subject=self.offering.subject)
        FacultyQualification.objects.create(faculty=self.faculty, subject=self.records[self.external.pk]["subjects"])
        self.client.force_login(self.chair)
        url = reverse("faculty-management:detail", args=[self.faculty.pk])
        self.assertEqual(self.client.get(url, {"tab": "qualifications"}).status_code, 403)
        self.grant(self.chair, FacultyQualification, "view")
        self.client.force_login(self.chair)
        page = self.client.get(url, {"tab": "qualifications"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.offering.subject.title)
        self.assertNotContains(page, self.records[self.external.pk]["subjects"].title)

    def test_draft_filter_preserves_search_and_pagination_query(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse("timetabling:schedules"), {
            "version_status": "editable", "academic_term": self.term.pk, "q": "Manual",
        })
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["page_obj"].paginator.count, 1)
        self.assertIn("version_status=editable", page.context["page_query"])
        self.assertIn("q=Manual", page.context["page_query"])
        invalid = self.client.get(reverse("timetabling:schedules"), {"version_status": "invented"})
        self.assertEqual(invalid.context["page_obj"].paginator.count, 0)

    def test_workspace_gets_do_not_mutate_and_posts_require_csrf(self):
        from audit.models import AuditLog
        self.client.force_login(self.chair)
        before = AuditLog.objects.count()
        for name in ("schedules-detail", "conflicts", "unscheduled"):
            self.client.get(reverse("timetabling:" + name, args=[self.schedule.pk]))
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "draft")
        self.assertEqual(AuditLog.objects.count(), before)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.chair)
        self.assertEqual(protected.post(reverse("timetabling:validate", args=[self.schedule.pk])).status_code, 403)

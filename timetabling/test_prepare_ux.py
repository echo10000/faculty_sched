"""Presentation routes preserve the established role and scope checks."""

from django.contrib.auth.models import Permission
from django.urls import reverse

from .tests import TimetableFixture


class PrepareScheduleUXTests(TimetableFixture):
    def test_chair_gets_scoped_guide_and_task_navigation(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse("timetabling:prepare"), {"academic_term": self.term.pk})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Prepare academic schedule")
        self.assertContains(page, reverse("workloads:assignments"))
        self.assertContains(page, reverse("timetabling:generator"))
        self.assertContains(page, reverse("timetabling:validate", args=[self.schedule.pk]))
        self.assertContains(page, self.department.name)
        self.assertNotContains(page, self.external.name)
        self.assertContains(self.client.get(reverse("home")), "Prepare schedule")

    def test_staff_sees_only_granted_steps_and_scoped_schedule(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("timetabling:prepare")).status_code, 403)
        home = self.client.get(reverse("home"))
        self.assertContains(home, "dashboard access only")
        self.assertNotContains(home, 'href="' + reverse("timetabling:prepare") + '"')

        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label__in=["academics", "timetabling"],
            codename__in=["view_academicterm", "view_schedule"],
        ))
        self.staff = type(self.staff).objects.get(pk=self.staff.pk)
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:prepare"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Generate schedule")
        self.assertNotContains(page, "Review &amp; submit")
        self.assertEqual(self.client.get(reverse("timetabling:prepare"), {"schedule": self.schedule.pk}).status_code, 404)

    def test_admin_and_dean_dashboard_priorities(self):
        self.client.force_login(self.admin)
        admin = self.client.get(reverse("home"))
        self.assertContains(admin, "Manage access and setup")
        self.assertContains(admin, "Academic terms")
        self.client.force_login(self.dean)
        dean = self.client.get(reverse("home"))
        self.assertContains(dean, "Schedules for review")
        self.assertContains(dean, "No schedules are currently waiting for review")
        self.assertNotContains(dean, "Manage access and setup")

    def test_schedule_workspace_keeps_primary_and_secondary_actions(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse("timetabling:schedules-detail", args=[self.schedule.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Check schedule")
        self.assertContains(page, "Review &amp; submit")
        self.assertContains(page, "More schedule options")
        self.assertContains(page, "Version history")

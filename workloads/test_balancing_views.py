"""Scoped, reviewable workload recommendation interface."""

from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from academics.models import AcademicTerm
from .models import WorkloadRecommendationRun
from .tests import TeachingFixture


class BalancingPageTests(TeachingFixture):
    def setUp(self):
        self.client.force_login(self.chair)
        self.run = WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.department,
            initiated_by=self.chair, status="PROPOSAL_READY", raw_solver_status="FEASIBLE",
            proposed_assignments=[{
                "offering_id": self.offering.pk, "offering": "S1 · MAIN", "status": "NEW",
                "current": [], "proposed": [{"faculty_id": self.faculty.pk,
                                                "faculty": "Person1 Example", "share": "1.00"}],
                "reason": "Balances the workload <safely>.",
            }],
            comparison={"faculty": [{
                "faculty_id": self.faculty.pk, "faculty": "Person1 Example",
                "before": {"assigned_load": "0.00", "recommended_load": "6.00",
                           "maximum_load": "9.00", "utilization": "0.00", "status": "underload"},
                "after": {"assigned_load": "3.00", "recommended_load": "6.00",
                          "maximum_load": "9.00", "utilization": "0.50", "status": "underload"},
            }], "summary": {"affected_faculty": 1, "assignment_changes": 1,
                            "imbalance_before": "1.00", "imbalance_after": "0.50",
                            "overload_before": "0.00", "overload_after": "0.00"}},
            diagnostics=[{"code": "NOTE", "severity": "WARNING", "message": "Check <private> data"}],
        )
        self.foreign_run = WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.external, initiated_by=self.admin,
            status="PROPOSAL_READY",
        )

    def url(self, name, *args):
        return reverse(f"workloads:{name}", args=args)

    def test_request_form_scopes_department_and_term(self):
        AcademicTerm.objects.filter(pk=self.later.pk).update(is_active=False)
        page = self.client.get(self.url("balancing"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.department.code)
        self.assertNotContains(page, self.sibling.code)
        self.assertNotContains(page, self.external.code)
        self.assertNotContains(page, 'value="%s"' % self.later.pk)
        with patch("workloads.balancing_views.request_balancing_run", return_value=self.run) as service:
            response = self.client.post(self.url("balancing"), {
                "academic_term": self.term.pk, "department": self.external.pk,
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("department", response.context["form"].errors)
            service.assert_not_called()
            response = self.client.post(self.url("balancing"), {
                "academic_term": self.term.pk, "department": self.department.pk,
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url("balancing-run-detail", self.run.pk))
            service.assert_called_once()
            self.assertEqual(service.call_args.kwargs["department"].pk, self.department.pk)

    def test_history_and_detail_are_scoped(self):
        history = self.client.get(self.url("balancing-runs"))
        self.assertContains(history, self.department.code)
        self.assertNotContains(history, f'data-label="Department">{self.external.code}</td>')
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.foreign_run.pk)).status_code, 404)
        detail = self.client.get(self.url("balancing-run-detail", self.run.pk))
        self.assertContains(detail, "Faculty workload comparison")
        self.assertContains(detail, "0.00")
        self.assertContains(detail, "Balances the workload")
        self.assertNotContains(detail, "<safely>")
        self.assertContains(detail, "&lt;safely&gt;", html=False)
        self.assertNotContains(detail, "<private>")
        self.assertContains(detail, "Accept recommendation")

    def test_dean_and_system_admin_see_only_their_granted_scope(self):
        self.client.force_login(self.dean)
        page = self.client.get(self.url("balancing"))
        self.assertContains(page, self.department.code)
        self.assertContains(page, self.sibling.code)
        self.assertNotContains(page, self.external.code)
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.foreign_run.pk)).status_code, 404)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("balancing")), self.external.code)
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.foreign_run.pk)).status_code, 200)

    def test_terminal_actions_are_post_only_and_require_csrf(self):
        accept = self.url("balancing-run-accept", self.run.pk)
        discard = self.url("balancing-run-discard", self.run.pk)
        self.assertEqual(self.client.get(accept).status_code, 405)
        self.assertEqual(self.client.get(discard).status_code, 405)
        self.assertEqual(self.client.put(accept).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.chair)
        self.assertEqual(secure.post(accept).status_code, 403)
        self.assertEqual(self.client.post(self.url("balancing-run-accept", self.foreign_run.pk)).status_code, 404)
        with patch("workloads.balancing_views.discard_balancing_run", return_value=self.run) as service:
            self.run.status = "DISCARDED"
            response = self.client.post(discard)
            self.assertEqual(response.status_code, 302)
            service.assert_called_once()

    def test_staff_denied_and_history_only_staff_cannot_finalize(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url("balancing")).status_code, 403)
        self.assertEqual(self.client.get(self.url("balancing-runs")).status_code, 403)
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.run.pk)).status_code, 403)
        self.staff.user_permissions.add(
            Permission.objects.get(content_type__app_label="workloads", codename="view_workloadrecommendationrun"),
            Permission.objects.get(content_type__app_label="academics", codename="view_academicterm"),
        )
        self.client.force_login(type(self.staff).objects.get(pk=self.staff.pk))
        detail = self.client.get(self.url("balancing-run-detail", self.run.pk))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Accept recommendation")
        self.assertEqual(self.client.post(self.url("balancing-run-accept", self.run.pk)).status_code, 403)

    def test_generate_only_staff_can_review_own_run_but_not_history(self):
        self.staff.user_permissions.add(
            Permission.objects.get(content_type__app_label="workloads", codename="generate_workloadrecommendation"),
            Permission.objects.get(content_type__app_label="academics", codename="view_academicterm"),
        )
        staff = type(self.staff).objects.get(pk=self.staff.pk)
        own = WorkloadRecommendationRun.objects.create(
            academic_term=self.term, department=self.department, initiated_by=staff,
            status="PROPOSAL_READY",
        )
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url("balancing-runs")).status_code, 403)
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.run.pk)).status_code, 404)
        detail = self.client.get(self.url("balancing-run-detail", own.pk))
        self.assertContains(detail, "Accept recommendation")
        self.assertNotContains(detail, "Recommendation history")

    def test_navigation_permissions(self):
        page = self.client.get(self.url("balancing"))
        self.assertContains(page, "Workload balancing")
        self.assertContains(page, "Recommendation history")
        self.client.force_login(self.staff)
        page = self.client.get(reverse("home"))
        self.assertNotContains(page, "Workload balancing")
        self.assertNotContains(page, "Recommendation history")

    def test_anonymous_redirects(self):
        self.client.logout()
        for name in ("balancing", "balancing-runs"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 302)

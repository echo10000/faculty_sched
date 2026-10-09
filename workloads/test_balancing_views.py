"""Scoped, reviewable workload recommendation interface."""

from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from academics.models import AcademicTerm
from accounts.models import AdminProfile
from resources.test_frontend_design import Markup
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
        self.assertEqual(list(page.context["form"].fields["department"].queryset), [self.department])
        self.assertNotIn(self.later, page.context["form"].fields["academic_term"].queryset)
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
        self.assertContains(history, self.department.name)
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
        self.assertCountEqual(page.context["form"].fields["department"].queryset,
                              [self.department, self.sibling])
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.foreign_run.pk)).status_code, 404)
        self.client.force_login(self.admin)
        self.assertIn(self.external,
                      self.client.get(self.url("balancing")).context["form"].fields["department"].queryset)
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

    def test_staff_default_access_includes_balancing_review(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url('balancing')).status_code, 200)
        self.assertEqual(self.client.get(self.url('balancing-runs')).status_code, 200)
        self.assertContains(self.client.get(self.url('balancing-run-detail', self.run.pk)), 'Accept recommendation')
        self.assertTrue(self.staff.has_perm('workloads.change_facultysubjectassignment'))

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
        self.assertEqual(self.client.get(self.url("balancing-runs")).status_code, 200)
        self.assertEqual(self.client.get(self.url("balancing-run-detail", self.run.pk)).status_code, 200)
        detail = self.client.get(self.url("balancing-run-detail", own.pk))
        self.assertContains(detail, "Accept recommendation")
        self.assertContains(detail, "Workload recommendation history")

    def test_navigation_permissions(self):
        for user in (self.staff, self.chair, self.college_staff):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                page = self.client.get(reverse("home"), {"academic_term": self.term.pk})
                self.assertEqual(page.status_code, 200)
                routes = {link["route"] for link in page.context["navigation_links"]}
                self.assertIn("workloads:section-assignments", routes)
                self.assertIn("timetabling:generator", routes)
                self.assertIn("timetabling:section-generator", routes)
                self.assertIn("workloads:balancing", routes)
                self.assertIn("workloads:balancing-runs", routes)
                markup = Markup(page.content.decode())
                self.assertTrue(any(tag == "nav" and attrs.get("aria-label") == "Main navigation"
                                    for tag, attrs in markup.elements))
                workspace_link = next(attrs for tag, attrs in markup.elements if tag == "a"
                                      and urlsplit(attrs.get("href", "")).path == self.url("section-assignments"))
                self.assertEqual(workspace_link["aria-label"], "Teaching assignments")
                workspace = self.client.get(workspace_link["href"])
                self.assertContains(workspace, "<summary>More assignment tools</summary>", html=True)
                links = Markup(workspace.content.decode()).elements
                for route, label, heading in (
                    ("balancing", "Workload recommendations", "Balance faculty workload"),
                    ("balancing-runs", "Recommendation history", "Workload recommendation history"),
                ):
                    href = next(attrs["href"] for tag, attrs in links if tag == "a"
                                and urlsplit(attrs.get("href", "")).path == self.url(route))
                    self.assertContains(workspace, f'<a class="btn btn-outline-secondary btn-sm" href="{href}">{label}</a>', html=True)
                    self.assertContains(self.client.get(href), f"<h1>{heading}</h1>", html=True)

        self.client.force_login(self.admin)
        page = self.client.get(reverse("home"))
        routes = {link["route"] for link in page.context["navigation_links"]}
        self.assertIn("workloads:balancing", routes)
        self.assertIn("workloads:balancing-runs", routes)

    def test_faculty_cannot_access_staff_assignment_generation_or_recommendations(self):
        user = get_user_model().objects.create_user(username="navigation-faculty", password="test-only-password")
        self.faculty.user = user
        self.faculty.save()
        AdminProfile.objects.create(user=user, role=AdminProfile.Role.FACULTY)
        self.client.force_login(user)
        page = self.client.get(reverse("faculty-portal:schedule"))
        self.assertEqual(page.status_code, 200)
        routes = {link["route"] for link in page.context["navigation_links"]}
        for route in ("workloads:section-assignments", "timetabling:generator",
                      "timetabling:section-generator", "workloads:balancing", "workloads:balancing-runs"):
            with self.subTest(route=route):
                self.assertNotIn(route, routes)
                self.assertEqual(self.client.get(reverse(route)).status_code, 403)

    def test_anonymous_redirects(self):
        self.client.logout()
        for name in ("balancing", "balancing-runs"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 302)

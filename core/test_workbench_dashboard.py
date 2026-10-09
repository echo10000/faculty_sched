"""Rendered workbench contracts: scoped reads, context, and honest state guidance."""
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.urls import reverse

from academics.models import AcademicTerm
from accounts.models import AdminProfile
from timetabling.models import ActiveSchedule, Schedule
from timetabling.tests import TimetableFixture


class WorkbenchMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.stages = []
        self.in_stages = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "nav" and attrs.get("aria-label") == "Scheduling stages":
            self.in_stages = True
        if tag == "a" and self.in_stages:
            self.stages.append(attrs)

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_stages = False


class WorkbenchDashboardTests(TimetableFixture):
    def page(self, user=None, term=None, **query):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("home"), {
            "academic_term": (term or self.term).pk, **query,
        })

    def test_stage_guidance_keeps_selected_term_without_completed_milestones(self):
        page = self.page(term=self.later)
        self.assertEqual(page.status_code, 200)
        markup = WorkbenchMarkup(page.content.decode())
        self.assertEqual(len(markup.stages), 7)
        html = page.content.decode()
        # Keep the mobile visual order and native reading/focus order together.
        self.assertLess(html.index('class="workbench-desk"'), html.index('class="workbench-ledger"'))
        for number, attrs in enumerate(markup.stages, 1):
            url = urlsplit(attrs["href"])
            self.assertEqual(url.path, reverse("timetabling:prepare"))
            self.assertEqual(parse_qs(url.query), {
                "academic_term": [str(self.later.pk)], "stage": [str(number)],
            })
            self.assertNotIn("aria-current", attrs)
            self.assertNotIn("aria-checked", attrs)
        self.assertContains(page, "Stages are navigation steps, not evidence of completion")

    def test_context_uses_existing_get_selector_and_applies_real_term(self):
        page = self.page(term=self.later)
        markup = WorkbenchMarkup(page.content.decode())
        term_select = next(attrs for tag, attrs in markup.elements
                           if attrs.get("id") == "dashboard-academic-term")
        self.assertEqual(term_select["name"], "academic_term")
        self.assertTrue(any(tag == "form" and attrs.get("method") == "get"
                            and attrs.get("action") == reverse("home")
                            for tag, attrs in markup.elements))
        selected = [attrs["value"] for tag, attrs in markup.elements
                    if tag == "option" and "selected" in attrs]
        self.assertEqual(selected, [str(self.later.pk)])
        self.assertEqual(page.context["selected_term"], self.later)

    def test_ledger_and_validation_findings_do_not_leak_external_scope(self):
        sibling = Schedule.objects.create(academic_term=self.term, department=self.sibling,
                                          name="Sibling working version")
        external = Schedule.objects.create(academic_term=self.term, department=self.external,
                                           name="Protected external working version")
        for user, visible, hidden in (
            (self.admin, (sibling, external), ()),
            (self.dean, (sibling,), (external,)),
            (self.chair, (), (sibling, external)),
        ):
            with self.subTest(user=user.username):
                page = self.page(user, college=self.other_college.pk, department=self.external.pk)
                self.assertContains(page, self.schedule.name)
                for schedule in visible:
                    self.assertContains(page, schedule.name)
                for schedule in hidden:
                    self.assertNotContains(page, schedule.name)
                    self.assertNotContains(page, reverse("timetabling:conflicts", args=[schedule.pk]))

    def test_historical_versions_and_current_official_count_stay_distinct(self):
        Schedule.objects.filter(pk=self.schedule.pk).update(status=Schedule.Status.APPROVED)
        official = Schedule.objects.create(
            academic_term=self.term, department=self.department, name="Current official version",
            family=self.schedule.family, parent_version=self.schedule, version_number=2,
        )
        Schedule.objects.filter(pk=official.pk).update(status=Schedule.Status.APPROVED)
        ActiveSchedule.objects.create(academic_term=self.term, department=self.department,
                                      schedule=official, selected_by=self.admin)
        page = self.page()
        counts = {item["key"]: item["count"]
                  for item in page.context["monitoring"]["schedules"]["counts"]}
        self.assertEqual(counts["approved"], 2)
        self.assertContains(page, '<strong>1</strong><span>Current official', html=False)
        self.assertContains(page, 'data-schedule-state="approved"><strong>2</strong>', html=False)
        self.assertContains(page, "Published includes historical versions.")

    def test_latest_checked_findings_have_an_action_and_version_context(self):
        self.candidate().save()
        page = self.page(self.chair)
        findings = page.context["monitoring"]["schedules"]["issues"]
        self.assertTrue(findings)
        self.assertContains(page, reverse("timetabling:conflicts", args=[self.schedule.pk]))
        self.assertContains(page, "latest checked version")
        self.assertContains(page, "Open validation findings")
        self.assertContains(page, f'{findings[0]["warning_count"]} warning')

    def test_capability_limited_dashboard_omits_operational_data_and_stages(self):
        with patch("accounts.backends.role_permissions", return_value={"core.view_dashboard"}):
            self.client.force_login(self.staff)
            page = self.client.get(reverse("home"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'aria-label="Operational metrics"')
        self.assertNotContains(page, 'aria-label="Scheduling stages"')
        self.assertNotContains(page, self.schedule.name)
        self.assertContains(page, "Contact your system administrator")

    def test_empty_term_and_no_term_explain_next_steps_without_fake_values(self):
        page = self.page(term=self.later)
        self.assertContains(page, "No working versions to review.")
        self.assertContains(page, '<strong>0</strong><span>Current official', html=False)
        with patch("workloads.selectors.accessible_terms", return_value=AcademicTerm.objects.none()):
            self.client.force_login(self.admin)
            no_term = self.client.get(reverse("home"))
        self.assertEqual(no_term.status_code, 200)
        self.assertContains(no_term, "No academic term is available.")
        self.assertNotContains(no_term, 'class="workbench-versions"')
        self.assertNotContains(no_term, "validation errors")

    def test_faculty_dashboard_redirect_and_minimal_navigation_remain(self):
        user = get_user_model().objects.create_user(username="workbench-faculty")
        self.faculty.user = user
        self.faculty.save()
        AdminProfile.objects.create(user=user, role="faculty")
        self.client.force_login(user)
        page = self.client.get(reverse("home"))
        self.assertRedirects(page, reverse("faculty-portal:schedule"), fetch_redirect_response=False)
        schedule = self.client.get(reverse("faculty-portal:schedule"))
        self.assertContains(schedule, "My Teaching Schedule")
        self.assertContains(schedule, "My Teaching Load")
        self.assertNotContains(schedule, 'aria-label="Scheduling stages"')

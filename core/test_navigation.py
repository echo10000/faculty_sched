"""Navigation presentation must retain authorized destinations and workspace context."""
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import resolve, reverse

from accounts.models import AdminProfile
from core.context_processors import navigation
from resources.test_frontend_design import Markup
from timetabling.tests import TimetableFixture


class SidebarNavigationTests(TimetableFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.faculty_user = get_user_model().objects.create_user(username="navigation-faculty")
        cls.faculty.user = cls.faculty_user
        cls.faculty.save()
        AdminProfile.objects.create(user=cls.faculty_user, role="faculty")

    def page(self, route="home", user=None, **query):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse(route), query)

    def secondary_groups(self, page):
        return [group for group in page.context["navigation_groups"] if not group["direct"]]

    def test_everyday_destinations_are_direct_links(self):
        page = self.page()
        self.assertEqual([link["route"] for link in page.context["primary_navigation"]], [
            "home", "timetabling:prepare", "reporting:index",
        ])
        # Primary links are never hidden inside a disclosure.
        sidebar = page.content.decode().split('<nav aria-label="Main navigation"')[1].split('</nav>')[0]
        self.assertLess(sidebar.index(reverse("timetabling:prepare")), sidebar.index('<details'))

    def test_secondary_groups_preserve_every_destination_without_duplicates(self):
        for user in (self.admin, self.chair, self.college_staff):
            with self.subTest(user=user.username):
                page = self.page(user=user)
                visible = [link["route"] for link in page.context["primary_navigation"]]
                visible += [link["route"] for group in self.secondary_groups(page)
                            for link in group["links"]]
                self.assertCountEqual(visible, [link["route"] for link in page.context["navigation_links"]])
                self.assertEqual(len(visible), len(set(visible)))

    def test_secondary_destination_opens_only_its_active_group(self):
        page = self.page("academic-calendar")
        groups = self.secondary_groups(page)
        self.assertEqual([group["label"] for group in groups if group["active"]], ["Management & Settings"])
        markup = Markup(page.content.decode())
        disclosures = [attrs for tag, attrs in markup.elements if tag == "details" and "data-nav-group" in attrs]
        self.assertEqual([attrs["data-nav-label"] for attrs in disclosures if "open" in attrs], ["Management & Settings"])

    def test_primary_destination_does_not_open_secondary_tools(self):
        page = self.page("timetabling:prepare")
        self.assertEqual([link["route"] for link in page.context["primary_navigation"] if link["active"]],
                         ["timetabling:prepare"])
        self.assertFalse(any(group["active"] for group in self.secondary_groups(page)))

    def test_faculty_sees_only_own_teaching_destinations(self):
        page = self.page("faculty-portal:schedule", user=self.faculty_user)
        self.assertEqual([link["route"] for link in page.context["primary_navigation"]],
                         ["faculty-portal:schedule", "faculty-portal:workload"])
        self.assertEqual(self.secondary_groups(page), [])
        self.assertNotContains(page, "System administration")

    def test_disabled_profile_has_no_navigation(self):
        AdminProfile.objects.filter(user=self.chair).update(is_enabled=False)
        user = get_user_model().objects.get(pk=self.chair.pk)
        request = RequestFactory().get(reverse("home"))
        request.user = user
        request.resolver_match = resolve(reverse("home"))
        context = navigation(request)
        self.assertEqual(context["primary_navigation"], [])
        self.assertEqual(context["navigation_groups"], [])

    def test_primary_workflow_entry_keeps_validated_context(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("timetabling:timetable", args=[self.schedule.pk]))
        markup = Markup(page.content.decode())
        link = next(attrs for tag, attrs in markup.elements if tag == "a"
                    and "nav-link" in attrs.get("class", "") and attrs.get("aria-label") == "Prepare Academic Schedule")
        query = parse_qs(urlsplit(link["href"]).query)
        self.assertEqual(query["academic_term"], [str(self.term.pk)])
        self.assertEqual(query["schedule"], [str(self.schedule.pk)])

    def test_admin_assignment_destinations_have_distinct_names(self):
        page = self.page()
        links = {link["route"]: link["label"] for link in page.context["navigation_links"]}
        self.assertEqual(links["workloads:section-assignments"], "Teaching assignments")
        self.assertEqual(links["workloads:assignments"], "Assignment records")

    def test_staff_workload_has_one_active_link_and_one_active_destination(self):
        for route in ("workloads:monitor", "workloads:availability", "workloads:balancing"):
            with self.subTest(route=route):
                page = self.page(route, user=self.chair)
                self.assertEqual([link["route"] for link in page.context["navigation_links"] if link["active"]], [route])
                self.assertEqual([group["label"] for group in page.context["navigation_groups"] if group["active"]],
                                 ["Faculty & Teaching"])

    def test_dashboard_prominent_action_uses_selected_term(self):
        for user in (self.admin, self.chair):
            with self.subTest(user=user.username):
                page = self.page(user=user, academic_term=self.term.pk)
                self.assertContains(page, "Open scheduling workspace")
                self.assertContains(page, f'href="{reverse("timetabling:prepare")}?academic_term={self.term.pk}"')
                self.assertContains(page, "Official schedules, history &amp; campus details")
                self.assertContains(page, "seven-stage guided workflow")

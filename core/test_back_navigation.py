from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse


class BackNavigationTests(SimpleTestCase):
    def render_topbar(self):
        request = SimpleNamespace(user=SimpleNamespace(
            get_full_name=lambda: "Sample staff", username="sample.staff"))
        return render_to_string("includes/topbar.html", {
            "request": request, "role_label": "Authorized Staff", "access_scope": "Sample college",
        })

    def test_back_is_a_get_link_with_dashboard_fallback(self):
        html = self.render_topbar()
        self.assertIn(f'id="page-back" href="{reverse("home")}"', html)
        self.assertIn('title="Return to dashboard"', html)
        self.assertIn('aria-hidden="true">←</span> Back</a>', html)

    def test_sidebar_toggle_has_distinct_action_and_accessible_state(self):
        html = self.render_topbar()
        self.assertIn('id="sidebar-collapse" type="button"', html)
        self.assertIn('aria-controls="sidebar" aria-expanded="true" title="Hide sidebar"', html)
        self.assertIn('<span class="visually-hidden">Hide sidebar</span>', html)

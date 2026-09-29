"""Public landing and protected dashboard route regressions."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import College, Department


class LandingPageTests(TestCase):
    def test_public_landing_is_available_without_login(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/landing.html")
        self.assertContains(response, "Bayawan–Santa Catalina Campus")

    def test_login_calls_to_action_use_authoritative_route(self):
        response = self.client.get(reverse("landing"))
        self.assertContains(response, f'href="{reverse("accounts:login")}"')
        self.assertContains(response, "Login to CampusLoad")

    def test_authenticated_landing_links_to_protected_dashboard(self):
        user = get_user_model().objects.create_superuser(
            username="landing-admin", password="Test-only-password-123!"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("home")}"')
        self.assertContains(response, "Open Dashboard")

    def test_dashboard_still_requires_authentication(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f'{reverse("accounts:login")}?next={reverse("home")}')

    def test_landing_does_not_disclose_protected_records(self):
        college = College.objects.create(code="SECRET", name="Private College Example")
        Department.objects.create(college=college, code="SECRET-D", name="Private Department Example")
        response = self.client.get(reverse("landing"))
        self.assertNotContains(response, "Private College Example")
        self.assertNotContains(response, "Private Department Example")

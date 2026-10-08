"""Presentation contracts: accessible recovery and unchanged directory context."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django import forms
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse

from faculty.models import Faculty
from .tests import ResourceFixture


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def by_id(self, identifier):
        return next(attrs for _, attrs in self.elements if attrs.get("id") == identifier)


class SharedFieldAccessibilityTests(SimpleTestCase):
    def test_help_and_errors_are_connected_for_fields_and_weekday_groups(self):
        class ExampleForm(forms.Form):
            name = forms.CharField(help_text="Use the recorded name.")
            allowed_weekdays = forms.MultipleChoiceField(
                choices=[("1", "Monday")], widget=forms.CheckboxSelectMultiple,
                help_text="Choose at least one day.",
            )

        form = ExampleForm(data={})
        html = render_to_string("resources/fields.html", {"form": form})
        markup = Markup(html)
        for field in form:
            for identifier in field.aria_describedby.split():
                self.assertIsNotNone(markup.by_id(identifier))
        self.assertEqual(markup.by_id("id_name")["aria-invalid"], "true")
        self.assertEqual(markup.by_id("id_name")["aria-describedby"], form["name"].aria_describedby)
        fieldset = next(attrs for tag, attrs in markup.elements if tag == "fieldset")
        self.assertEqual(fieldset["aria-describedby"], form["allowed_weekdays"].aria_describedby)


class DirectoryPresentationTests(ResourceFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_invalid_faculty_form_keeps_input_and_exposes_field_error(self):
        payload = self.payload("faculty-management")
        payload["first_name"] = ""
        before = Faculty.objects.count()
        response = self.client.post(reverse("faculty-management:add"), payload)
        self.assertEqual(response.status_code, 200)
        markup = Markup(response.content.decode())
        self.assertEqual(markup.by_id("id_employee_id")["value"], payload["employee_id"])
        self.assertEqual(markup.by_id("id_first_name")["aria-invalid"], "true")
        self.assertIn("id_first_name_error", markup.by_id("id_first_name")["aria-describedby"])
        self.assertEqual(markup.by_id("id_first_name_error")["role"], "alert")
        self.assertEqual(Faculty.objects.count(), before)

    def test_empty_directory_distinguishes_filters_errors_and_no_records(self):
        url = reverse("subjects:list")
        filtered = self.client.get(url, {"q": "nothing-matches"})
        self.assertContains(filtered, "No subjects match the current filters.")
        invalid = self.client.get(url, {"status": "invalid-status"})
        self.assertContains(invalid, "The selected filters could not be applied.")
        for obj in self.records.values():
            obj["subjects"].delete()
        empty = self.client.get(url)
        self.assertContains(empty, "No subjects are available in your scope.")

    def test_directory_pagination_preserves_filter_parameters(self):
        for number in range(21):
            Faculty.objects.create(
                employee_id=f"PAGE-{number}", first_name="Page", last_name=f"Person {number}",
                home_department=self.department, employment_category=self.category,
            )
        response = self.client.get(reverse("faculty-management:list"), {
            "q": "Page", "department": self.department.pk, "academic_term": self.term.pk,
        })
        markup = Markup(response.content.decode())
        next_page = next(attrs["href"] for tag, attrs in markup.elements
                         if tag == "a" and "page=2" in attrs.get("href", ""))
        query = parse_qs(urlsplit(next_page).query)
        self.assertEqual(query["q"], ["Page"])
        self.assertEqual(query["department"], [str(self.department.pk)])
        self.assertEqual(query["academic_term"], [str(self.term.pk)])

from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.core.management import call_command
from django.test import override_settings
from io import StringIO

from academics.calendar import ensure_calendar_periods
from academics.models import AcademicCalendar, CalendarTermPattern, AcademicTerm, AcademicYear, Semester
from workloads.selectors import accessible_terms
from workloads.tests import TeachingFixture


def sample_calendar():
    calendar = AcademicCalendar.objects.create(name="Sample calendar", year_start_month=6, year_start_day=1)
    for code, name, dates in (("FIRST", "First semester", (8, 1, 12, 20)),
                              ("SECOND", "Second semester", (1, 5, 5, 20))):
        semester, _ = Semester.objects.get_or_create(code=code, defaults={"name": name})
        CalendarTermPattern.objects.create(calendar=calendar, semester=semester, code=code,
            start_month=dates[0], start_day=dates[1], end_month=dates[2], end_day=dates[3])
    return calendar


class CalendarRolloverTests(TestCase):
    def setUp(self):
        self.calendar = sample_calendar()

    def test_creates_current_and_next_year_once_and_advances_across_year_boundary(self):
        ensure_calendar_periods(date(2026, 10, 1))
        self.assertEqual(AcademicYear.objects.count(), 2)
        self.assertEqual(AcademicTerm.objects.count(), 4)
        ensure_calendar_periods(date(2026, 10, 1))
        self.assertEqual(AcademicTerm.objects.count(), 4)
        second = AcademicTerm.objects.get(academic_year__label="2026-2027", code="SECOND")
        self.assertEqual((second.start_date, second.end_date), (date(2027, 1, 5), date(2027, 5, 20)))
        ensure_calendar_periods(date(2027, 6, 1))
        self.assertEqual(AcademicYear.objects.count(), 3)
        self.assertEqual(AcademicTerm.objects.count(), 6)

    def test_preserves_existing_demo_term_identity_and_manual_dates(self):
        year = AcademicYear.objects.create(label="CAS Demo", start_date=date(2026, 6, 1), end_date=date(2027, 5, 31))
        semester = self.calendar.patterns.get(code="FIRST").semester
        demo = AcademicTerm.objects.create(academic_year=year, semester=semester, code="CAS-DEMO",
            start_date=date(2026, 8, 1), end_date=date(2026, 12, 20))
        ensure_calendar_periods(date(2026, 10, 1))
        self.assertEqual(year.terms.filter(semester=semester).count(), 1)
        demo.end_date = date(2026, 12, 19)
        demo.save()
        ensure_calendar_periods(date(2026, 10, 2))
        demo.refresh_from_db()
        self.assertEqual(demo.end_date, date(2026, 12, 19))

    def test_invalid_dates_and_overlapping_patterns_rejected(self):
        pattern = self.calendar.patterns.get(code="FIRST")
        pattern.start_month, pattern.start_day = 2, 30
        with self.assertRaises(ValidationError):
            pattern.full_clean()
        pattern.start_month, pattern.start_day = 1, 10
        pattern.end_month, pattern.end_day = 5, 10
        with self.assertRaises(ValidationError):
            pattern.full_clean()

    def test_editing_calendar_rules_keeps_existing_year_and_term_links(self):
        ensure_calendar_periods(date(2026, 10, 1))
        original_ids = set(AcademicTerm.objects.values_list("pk", flat=True))
        self.calendar.year_start_month = 7
        self.calendar.save()
        ensure_calendar_periods(date(2026, 10, 2))
        self.assertEqual(AcademicYear.objects.count(), 2)
        self.assertEqual(set(AcademicTerm.objects.values_list("pk", flat=True)), original_ids)
        ensure_calendar_periods(date(2027, 7, 1))
        new_year = AcademicYear.objects.get(label="2028-2029")
        self.assertEqual(new_year.start_date, date(2028, 7, 1))


class StaffCurrentTermTests(TeachingFixture):
    def setUp(self):
        self.calendar = sample_calendar()
        ensure_calendar_periods(date(2026, 10, 1))

    @patch("academics.calendar.timezone.localdate", return_value=date(2026, 10, 1))
    def test_staff_current_term_admin_all_terms_and_url_tampering(self, _date):
        current = accessible_terms(self.chair).get()
        self.assertEqual(current.code, "FIRST")
        self.assertGreater(accessible_terms(self.admin).count(), 1)
        self.client.force_login(self.chair)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["selected_term"], current)
        self.assertNotContains(response, 'id="dashboard-academic-term"')
        self.assertEqual(self.client.get(reverse("home"), {"academic_term": self.term.pk}).status_code, 404)
        self.assertEqual(self.client.get(self.url("availability", term=self.term)).status_code, 404)
        self.assertEqual(self.client.post(self.url("availability-add", term=self.term), self.availability_data()).status_code, 404)
        self.assertContains(self.client.get(reverse("workloads:availability")), "Current term:")
        report_url = reverse("reporting:detail", args=["workload"])
        self.assertEqual(self.client.get(report_url, {"term": self.term.pk}).status_code, 404)
        self.assertNotContains(self.client.get(report_url), 'name="term">')
        from timetabling.models import Schedule
        old = Schedule.objects.create(academic_term=self.term, department=self.department, name="Earlier term draft")
        self.assertEqual(self.client.get(reverse("timetabling:timetable", args=[old.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("timetabling:validate", args=[old.pk])).status_code, 404)

    @patch("academics.calendar.timezone.localdate", return_value=date(2027, 1, 6))
    def test_staff_switches_to_second_semester_by_date(self, _date):
        self.assertEqual(accessible_terms(self.chair).get().code, "SECOND")

    @patch("academics.calendar.timezone.localdate", return_value=date(2027, 6, 10))
    def test_calendar_break_does_not_select_a_future_term(self, _date):
        self.assertFalse(accessible_terms(self.chair).exists())
        self.client.force_login(self.chair)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_term"])
        self.assertContains(response, "No term is currently in progress")

    @patch("academics.calendar.timezone.localdate", return_value=date(2026, 10, 1))
    def test_admin_can_edit_calendar_and_staff_cannot(self, _date):
        url = reverse("admin:academics_academiccalendar_change", args=[self.calendar.pk])
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {"name": "Edited sample calendar", "is_enabled": "on",
                "year_start_month": 6, "year_start_day": 1,
                "patterns-TOTAL_FORMS": 2, "patterns-INITIAL_FORMS": 2,
                "patterns-MIN_NUM_FORMS": 0, "patterns-MAX_NUM_FORMS": 1000}
        for index, pattern in enumerate(self.calendar.patterns.all()):
            for field in ("id", "semester", "code", "start_month", "start_day", "end_month", "end_day"):
                value = pattern.pk if field == "id" else pattern.semester_id if field == "semester" else getattr(pattern, field)
                data[f"patterns-{index}-{field}"] = value
        data["patterns-0-end_day"] = 19
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Edited sample calendar")
        self.assertEqual(self.calendar.patterns.get(code="FIRST").end_day, 19)
        self.assertEqual(accessible_terms(self.chair).get().end_date, date(2026, 12, 20))

    @override_settings(DEBUG=True)
    def test_sample_command_preserves_admin_calendar_edits(self):
        self.calendar.name = "Custom admin settings"
        self.calendar.save()
        call_command("configure_sample_calendar", actor=self.admin.username, stdout=StringIO())
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Custom admin settings")
        self.assertEqual(AcademicCalendar.objects.count(), 1)

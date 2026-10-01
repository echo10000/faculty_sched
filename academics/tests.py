from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from .models import AcademicYear, AcademicTerm, Semester


class AcademicCalendarTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(label="Flexible year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.semester = Semester.objects.create(code="TRIMESTER", name="Configured period")

    def term(self):
        return AcademicTerm.objects.create(academic_year=self.year, semester=self.semester, code="P1", start_date=date(2026, 6, 1), end_date=date(2026, 8, 31))

    def test_period_names_are_configurable_and_multiple_terms_can_be_active(self):
        self.term()
        AcademicTerm.objects.create(academic_year=self.year, semester=self.semester, code="P2", start_date=date(2026, 9, 1), end_date=date(2026, 12, 1))
        self.assertEqual(AcademicTerm.objects.filter(is_active=True).count(), 2)

    def test_date_checks_at_model_and_database_levels(self):
        with self.assertRaises(ValidationError):
            AcademicTerm.objects.create(academic_year=self.year, semester=self.semester, code="BAD", start_date=date(2025, 1, 1), end_date=date(2026, 2, 1))
        term = self.term()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicTerm.objects.filter(pk=term.pk).update(end_date=date(2027, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicYear.objects.filter(pk=self.year.pk).update(end_date=date(2026, 5, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicTerm.objects.filter(pk=term.pk).update(end_date=date(2026, 5, 1))

    def test_year_and_semester_references_are_protected(self):
        self.term()
        with self.assertRaises(ProtectedError):
            self.year.delete()
        with self.assertRaises(ProtectedError):
            self.semester.delete()

    def test_year_code_uniqueness_is_enforced(self):
        self.term()
        with self.assertRaises(ValidationError):
            self.term()

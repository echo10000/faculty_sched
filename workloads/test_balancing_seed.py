from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from academics.models import AcademicTerm
from faculty.models import Faculty

from .models import FacultySubjectAssignment, SubjectOffering, WorkloadRecommendationRun


class BalancingSeedTests(TestCase):
    def test_seed_is_idempotent_and_never_regenerates_accepted_assignments(self):
        with override_settings(DEBUG=True):
            call_command("seed_balancing", verbosity=0)
            term = AcademicTerm.objects.get(code="DEMO-BALANCING")
            first = Faculty.objects.get(employee_id="DEMO-F1")
            second = Faculty.objects.get(employee_id="DEMO-F1B")
            offerings = SubjectOffering.objects.filter(academic_term=term)
            self.assertEqual(offerings.count(), 2)
            self.assertEqual(FacultySubjectAssignment.objects.filter(subject_offering__in=offerings).count(), 2)
            moved = FacultySubjectAssignment.objects.get(subject_offering=offerings.first())
            moved.delete()
            FacultySubjectAssignment.objects.create(faculty=second, subject_offering=moved.subject_offering)
            call_command("seed_balancing", verbosity=0)
            self.assertEqual(SubjectOffering.objects.filter(academic_term=term).count(), 2)
            self.assertFalse(FacultySubjectAssignment.objects.filter(
                faculty=first, subject_offering=moved.subject_offering,
            ).exists())
            self.assertEqual(FacultySubjectAssignment.objects.filter(subject_offering__in=offerings).count(), 2)
            self.assertFalse(WorkloadRecommendationRun.objects.exists())

    @override_settings(DEBUG=False)
    def test_seed_rejected_outside_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_balancing", verbosity=0)

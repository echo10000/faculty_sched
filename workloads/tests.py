from datetime import date, time
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from academics.models import AcademicTerm
from audit.models import AuditLog
from faculty.models import Faculty
from resources.tests import ResourceFixture
from resources.forms import FacultyForm, SubjectForm
from resources.services import save_resource
from .calculation import calculate_workload, resolve_policy
from .datasets import faculty_candidates, offering_data
from .forms import AssignmentForm, AvailabilityForm, OfferingForm
from .models import FacultyAvailability, FacultySubjectAssignment, FacultyTermCapacity, SubjectOffering, WorkloadPolicy
from .operations import remove_term_record, save_term_record


class TeachingFixture(ResourceFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.later = AcademicTerm.objects.create(academic_year=cls.term.academic_year, semester=cls.term.semester, code="T2", start_date=date(2026, 6, 1), end_date=date(2026, 12, 31))
        cls.offerings = {}
        for department in (cls.department, cls.sibling, cls.external):
            subject = cls.records[department.pk]["subjects"]
            cls.offerings[department.pk] = SubjectOffering.objects.create(subject=subject, academic_term=cls.term, department=department, lecture_units=2, laboratory_units=1, lecture_hours=2, laboratory_hours=3)
        cls.offering = cls.offerings[cls.department.pk]

    def url(self, name, pk=None, term=None):
        return reverse(f"workloads:{name}", args=[pk] if pk else []) + f"?academic_term={(term or self.term).pk}"

    def assignment_data(self, **changes):
        return {"faculty": self.faculty.pk, "subject_offering": self.offering.pk, "academic_term": self.term.pk, "share": "1", **changes}

    def availability_data(self, **changes):
        return {"faculty": self.faculty.pk, "academic_term": self.term.pk, "day_of_week": "1", "start_time": "08:00", "end_time": "12:00", "availability_type": "available", **changes}

    def offering_data(self, **changes):
        return {"subject": self.offering.subject_id, "academic_term": self.term.pk, "code": "SECOND", "is_active": "on", **changes}

    def save_assignment(self, **changes):
        obj, form, report = save_term_record(user=self.chair, form_class=AssignmentForm, data=self.assignment_data(**changes), term=self.term)
        self.assertFalse(form.errors)
        return obj, report

    def policy(self, **values):
        return WorkloadPolicy.objects.create(academic_term=self.term, department=self.department, lecture_weight=1, laboratory_weight=Decimal("1.5"), **values)


class AvailabilityTests(TeachingFixture):
    def test_create_edit_delete_and_audit(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url("availability-add"), self.availability_data(notes="Private availability note"))
        self.assertEqual(response.status_code, 302)
        entry = FacultyAvailability.objects.get()
        self.assertEqual(entry.created_by, self.chair)
        event = AuditLog.objects.get(action="facultyavailability.created")
        self.assertEqual(event.details["academic_term_id"], self.term.pk)
        self.assertEqual(event.department_id, self.department.pk)
        self.assertNotIn("Private availability note", str(event.details))
        self.assertEqual(self.client.post(self.url("availability-edit", entry.pk), self.availability_data(end_time="11:00")).status_code, 302)
        self.assertEqual(AuditLog.objects.get(action="facultyavailability.updated").details["before"]["end_time"], "12:00:00")
        self.assertEqual(self.client.get(self.url("availability-delete", entry.pk)).status_code, 200)
        self.assertTrue(FacultyAvailability.objects.filter(pk=entry.pk).exists())
        self.assertEqual(self.client.post(self.url("availability-delete", entry.pk)).status_code, 302)
        self.assertFalse(FacultyAvailability.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="facultyavailability.deleted", object_id=str(entry.pk)).exists())

    def test_invalid_range_day_and_type(self):
        self.client.force_login(self.chair)
        for changes in ({"start_time": "12:00"}, {"end_time": "07:00"}, {"day_of_week": "8"}, {"availability_type": "fake"}, {"start_time": "bad"}):
            response = self.client.post(self.url("availability-add"), self.availability_data(**changes))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(FacultyAvailability.objects.exists())

    def test_duplicates_and_incompatible_overlap_rejected(self):
        self.client.force_login(self.chair)
        self.client.post(self.url("availability-add"), self.availability_data())
        for data in (self.availability_data(), self.availability_data(start_time="10:00", end_time="13:00"), self.availability_data(availability_type="unavailable")):
            response = self.client.post(self.url("availability-add"), data)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(FacultyAvailability.objects.count(), 1)

    def test_preferred_can_overlap_available_and_adjacent_intervals_are_valid(self):
        self.client.force_login(self.chair)
        for changes in ({}, {"availability_type": "preferred", "start_time": "09:00", "end_time": "10:00"}, {"start_time": "12:00", "end_time": "14:00"}):
            self.assertEqual(self.client.post(self.url("availability-add"), self.availability_data(**changes)).status_code, 302)
        self.assertEqual(FacultyAvailability.objects.count(), 3)

    def test_database_overlap_protection_and_different_term(self):
        FacultyAvailability.objects.create(faculty=self.faculty, academic_term=self.term, day_of_week=1, start_time=time(8), end_time=time(12), availability_type="unavailable")
        for kind in ("available", "preferred", "unavailable"):
            with self.subTest(kind=kind), self.assertRaises(IntegrityError), transaction.atomic():
                FacultyAvailability.objects.bulk_create([FacultyAvailability(faculty=self.faculty, academic_term=self.term, day_of_week=1, start_time=time(9), end_time=time(10), availability_type=kind)])
        FacultyAvailability.objects.create(faculty=self.faculty, academic_term=self.later, day_of_week=1, start_time=time(8), end_time=time(12), availability_type="available")
        self.assertEqual(FacultyAvailability.objects.count(), 2)

    def test_inactive_faculty_and_term_rejected(self):
        self.client.force_login(self.chair)
        Faculty.objects.filter(pk=self.faculty.pk).update(is_active=False)
        response = self.client.post(self.url("availability-add"), self.availability_data())
        self.assertTrue(response.context["form"].errors)
        AcademicTerm.objects.filter(pk=self.term.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.url("availability-add"), self.availability_data()).status_code, 404)

    def test_identity_and_term_cannot_be_changed_on_edit(self):
        self.client.force_login(self.dean)
        self.client.post(self.url("availability-add"), self.availability_data())
        entry = FacultyAvailability.objects.get()
        response = self.client.post(self.url("availability-edit", entry.pk), self.availability_data(faculty=self.records[self.sibling.pk]["faculty-management"].pk))
        self.assertTrue(response.context["form"].errors)
        response = self.client.post(self.url("availability-edit", entry.pk), self.availability_data(academic_term=self.later.pk))
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(self.client.get(self.url("availability-edit", entry.pk, term=self.later)).status_code, 404)

    def test_filters_and_faculty_specific_view(self):
        self.client.force_login(self.chair)
        self.client.post(self.url("availability-add"), self.availability_data())
        response = self.client.get(self.url("availability") + "&day_of_week=2")
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        response = self.client.get(self.url("faculty-availability", self.faculty.pk) + "&availability_type=available")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)


class OfferingTests(TeachingFixture):
    def test_catalog_values_copied_and_create_edit_audited(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url("offerings-add"), self.offering_data())
        self.assertEqual(response.status_code, 302)
        obj = SubjectOffering.objects.get(code="SECOND")
        self.assertEqual(obj.total_units, 3)
        self.assertEqual(obj.department, self.department)
        data = self.offering_data(code="SECOND", lecture_units="3", laboratory_units="1", lecture_hours="3", laboratory_hours="3")
        self.assertEqual(self.client.post(self.url("offerings-edit", obj.pk), data).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="subjectoffering.updated").exists())

    def test_catalog_edits_do_not_rewrite_offering_values(self):
        subject = self.offering.subject
        subject.lecture_units = 9
        subject.save()
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.lecture_units, 2)

    def test_invalid_values_and_inactive_subject(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url("offerings-add"), self.offering_data(lecture_hours="-1"))
        self.assertTrue(response.context["form"].errors)
        subject = self.offering.subject
        subject.is_active = False
        subject.save()
        response = self.client.post(self.url("offerings-add"), self.offering_data())
        self.assertTrue(response.context["form"].errors)

    def test_subject_term_identity_and_assigned_dimensions_are_frozen(self):
        self.save_assignment()
        self.offering.lecture_hours = 99
        with self.assertRaises(ValidationError):
            self.offering.save()
        self.offering.refresh_from_db()
        self.offering.academic_term = self.later
        with self.assertRaises(ValidationError):
            self.offering.save()

    def test_duplicate_offering_in_term_rejected_and_other_term_allowed(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url("offerings-add"), self.offering_data(code="main"))
        self.assertTrue(response.context["form"].errors)
        response = self.client.post(self.url("offerings-add", term=self.later), self.offering_data(code="MAIN", academic_term=self.later.pk))
        self.assertEqual(response.status_code, 302)

    def test_moving_catalog_or_faculty_cannot_reassign_term_records_scope(self):
        self.save_assignment()
        with self.assertRaises(ValidationError):
            save_resource(user=self.dean, form_class=FacultyForm, data=self.payload("faculty-management", self.sibling, identifier="F1"), pk=self.faculty.pk)
        with self.assertRaises(ValidationError):
            save_resource(user=self.dean, form_class=SubjectForm, data=self.payload("subjects", self.sibling, identifier="S1"), pk=self.offering.subject_id)


class AssignmentTests(TeachingFixture):
    def test_preview_does_not_write_then_save_and_remove(self):
        self.policy(recommended_load=3, maximum_load=6)
        self.client.force_login(self.chair)
        response = self.client.post(self.url("assignments-add"), self.assignment_data(intent="preview"))
        self.assertEqual(response.context["preview"]["assigned_load"], Decimal("3.5"))
        self.assertContains(response, "Resulting workload")
        self.assertEqual(FacultySubjectAssignment.objects.count(), 0)
        self.assertFalse(AuditLog.objects.filter(action="facultysubjectassignment.created").exists())
        self.assertEqual(self.client.post(self.url("assignments-add"), self.assignment_data(intent="save")).status_code, 302)
        assignment = FacultySubjectAssignment.objects.get()
        event = AuditLog.objects.get(action="facultysubjectassignment.created")
        self.assertEqual(event.details["academic_term_id"], self.term.pk)
        self.assertEqual(self.client.get(self.url("assignments-delete", assignment.pk)).status_code, 200)
        self.assertEqual(self.client.post(self.url("assignments-delete", assignment.pk)).status_code, 302)
        self.assertFalse(FacultySubjectAssignment.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="facultysubjectassignment.deleted").exists())

    def test_assignment_share_edit_recalculates_without_double_counting(self):
        self.policy(maximum_load=4, enforce_maximum=True)
        assignment, _ = self.save_assignment()
        self.client.force_login(self.chair)
        response = self.client.post(self.url("assignments-edit", assignment.pk), self.assignment_data(share="0.5", intent="save"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(calculate_workload(self.faculty, self.term)["assigned_load"], Decimal("1.75"))

    def test_duplicate_and_wrong_term_rejected(self):
        self.save_assignment()
        self.client.force_login(self.chair)
        for data in (self.assignment_data(), self.assignment_data(academic_term=self.later.pk)):
            response = self.client.post(self.url("assignments-add"), data)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)

    def test_cross_department_assignment_is_rejected_even_for_dean(self):
        self.client.force_login(self.dean)
        response = self.client.post(self.url("assignments-add"), self.assignment_data(subject_offering=self.offerings[self.sibling.pk].pk))
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_inactive_faculty_subject_and_offering_rejected(self):
        self.client.force_login(self.chair)
        for obj in (self.faculty, self.offering.subject, self.offering):
            type(obj).objects.filter(pk=obj.pk).update(is_active=False)
            response = self.client.post(self.url("assignments-add"), self.assignment_data())
            self.assertTrue(response.context["form"].errors)
            type(obj).objects.filter(pk=obj.pk).update(is_active=True)

    def test_total_offering_shares_cannot_exceed_one(self):
        self.save_assignment(share="0.75")
        second = Faculty.objects.create(employee_id="SHARE", first_name="Second", last_name="Teacher", home_department=self.department)
        with self.assertRaises(ValidationError):
            self.save_assignment(faculty=second.pk, share="0.5")
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)
        self.save_assignment(faculty=second.pk, share="0.25")

    def test_hard_maximum_rejects_and_warning_mode_allows_overload(self):
        policy = self.policy(maximum_load=3, enforce_maximum=True)
        self.client.force_login(self.chair)
        response = self.client.post(self.url("assignments-add"), self.assignment_data(intent="save"))
        self.assertContains(response, "hard maximum")
        self.assertFalse(FacultySubjectAssignment.objects.exists())
        policy.enforce_maximum = False
        policy.save()
        response = self.client.post(self.url("assignments-add"), self.assignment_data(intent="save"), follow=True)
        self.assertContains(response, "exceeds the configured maximum")
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)

    def test_preview_cannot_bypass_later_capacity_change(self):
        policy = self.policy(maximum_load=4, enforce_maximum=True)
        self.client.force_login(self.chair)
        self.assertEqual(self.client.post(self.url("assignments-add"), self.assignment_data(intent="preview")).status_code, 200)
        policy.maximum_load = 3
        policy.save()
        response = self.client.post(self.url("assignments-add"), self.assignment_data(intent="save", assigned_load="0"))
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_missing_weights_cannot_bypass_hard_limit(self):
        WorkloadPolicy.objects.create(academic_term=self.term, department=self.department, maximum_load=12, enforce_maximum=True)
        with self.assertRaises(ValidationError):
            self.save_assignment()
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_database_duplicate_and_share_constraints(self):
        assignment, _ = self.save_assignment()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacultySubjectAssignment.objects.bulk_create([FacultySubjectAssignment(faculty=self.faculty, subject_offering=self.offering)])
        for share in (0, -1, 2):
            with self.assertRaises(IntegrityError), transaction.atomic():
                FacultySubjectAssignment.objects.filter(pk=assignment.pk).update(share=share)

    def test_failed_audit_rolls_back_create_edit_delete(self):
        with patch("workloads.operations.record_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.save_assignment()
        self.assertFalse(FacultySubjectAssignment.objects.exists())
        assignment, _ = self.save_assignment()
        count = AuditLog.objects.count()
        with patch("workloads.operations.record_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                save_term_record(user=self.chair, form_class=AssignmentForm, data=self.assignment_data(share="0.5"), term=self.term, pk=assignment.pk)
            with self.assertRaises(RuntimeError):
                remove_term_record(user=self.chair, model=FacultySubjectAssignment, pk=assignment.pk, term=self.term)
        assignment.refresh_from_db()
        self.assertEqual(assignment.share, 1)
        self.assertEqual(AuditLog.objects.count(), count)


class CalculationTests(TeachingFixture):
    def test_unconfigured_without_policy_and_unknown_weighted_load(self):
        report = calculate_workload(self.faculty, self.term)
        self.assertEqual(report["status"], "UNCONFIGURED")
        self.assertIsNone(report["remaining_capacity"])
        self.save_assignment()
        report = calculate_workload(self.faculty, self.term)
        self.assertEqual(report["teaching_units"], 3)
        self.assertEqual(report["teaching_hours"], 5)
        self.assertIsNone(report["assigned_load"])
        self.assertEqual(report["status"], "UNCONFIGURED")

    def test_weighted_totals_hours_and_term_isolation(self):
        self.policy(recommended_load=3, maximum_load=7)
        self.save_assignment()
        report = calculate_workload(self.faculty, self.term)
        self.assertEqual(report["assigned_load"], Decimal("3.50"))
        self.assertEqual(report["remaining_capacity"], Decimal("3.50"))
        self.assertEqual(report["utilization"], 50)
        self.assertEqual(calculate_workload(self.faculty, self.later)["assignment_count"], 0)

    def test_status_boundaries(self):
        policy = self.policy(recommended_load=3, maximum_load=4)
        self.assertEqual(calculate_workload(self.faculty, self.term)["status"], "UNDERLOAD")
        self.save_assignment()
        self.assertEqual(calculate_workload(self.faculty, self.term)["status"], "WITHIN_LOAD")
        policy.maximum_load = Decimal("3.5")
        policy.save()
        self.assertEqual(calculate_workload(self.faculty, self.term)["status"], "AT_CAPACITY")
        policy.maximum_load = 3
        policy.save()
        self.assertEqual(calculate_workload(self.faculty, self.term)["status"], "OVERLOAD")

    def test_recommended_only_warns_without_hard_enforcement(self):
        self.policy(recommended_load=3)
        _, report = self.save_assignment()
        self.assertEqual(report["status"], "OVERLOAD")
        self.assertIsNone(report["remaining_capacity"])

    def test_precedence_sources_faculty_baseline_and_term_override(self):
        WorkloadPolicy.objects.create(academic_term=self.term, maximum_load=24, enforce_maximum=True)
        WorkloadPolicy.objects.create(academic_term=self.term, college=self.college, maximum_load=21)
        self.policy(maximum_load=18)
        self.assertEqual(resolve_policy(self.faculty, self.term)["maximum_load"], 18)
        self.assertEqual(resolve_policy(self.faculty, self.term)["sources"]["maximum_load"], "Department workload policy")
        self.faculty.maximum_load = 15
        self.faculty.save()
        self.assertEqual(resolve_policy(self.faculty, self.term)["maximum_load"], 15)
        FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term, maximum_load=12, enforce_maximum=False)
        resolved = resolve_policy(self.faculty, self.term)
        self.assertEqual(resolved["maximum_load"], 12)
        self.assertEqual(resolved["sources"]["maximum_load"], "Faculty term override")
        self.assertFalse(resolved["enforce_maximum"])

    def test_zero_is_configured_and_utilization_not_divided_by_zero(self):
        self.policy(recommended_load=0, maximum_load=0)
        report = calculate_workload(self.faculty, self.term)
        self.assertEqual(report["status"], "AT_CAPACITY")
        self.assertIsNone(report["utilization"])
        self.assertEqual(report["remaining_capacity"], 0)

    def test_deactivated_offering_does_not_erase_existing_workload(self):
        self.policy(maximum_load=10)
        self.save_assignment()
        self.offering.is_active = False
        self.offering.save()
        self.assertEqual(calculate_workload(self.faculty, self.term)["assigned_load"], Decimal("3.5"))

    def test_invalid_inherited_configuration_cannot_allow_assignment(self):
        self.policy(maximum_load=4)
        Faculty.objects.filter(pk=self.faculty.pk).update(recommended_load=6)
        self.faculty.refresh_from_db()
        self.assertEqual(calculate_workload(self.faculty, self.term)["status"], "UNCONFIGURED")
        with self.assertRaises(ValidationError):
            self.save_assignment()


class TeachingSecurityTests(TeachingFixture):
    def test_all_sections_require_grants_and_calendar_access(self):
        self.client.force_login(self.staff)
        for section in ("monitor", "availability", "offerings", "assignments"):
            self.assertEqual(self.client.get(self.url(section)).status_code, 403)
        self.grant(self.staff, FacultySubjectAssignment, "view")
        self.assertEqual(self.client.get(self.url("assignments")).status_code, 403)
        self.grant(self.staff, AcademicTerm, "view")
        self.assertEqual(self.client.get(self.url("assignments")).status_code, 200)
        self.assertEqual(self.client.post(self.url("assignments-add"), self.assignment_data()).status_code, 403)

    def test_scoped_lists_and_monitor_counts(self):
        for user, count in ((self.admin, 3), (self.dean, 2), (self.chair, 1)):
            self.client.force_login(user)
            for section in ("monitor", "offerings"):
                self.assertEqual(self.client.get(self.url(section)).context["page_obj"].paginator.count, count)

    def test_foreign_direct_detail_edit_and_delete_urls(self):
        foreign_faculty = self.records[self.external.pk]["faculty-management"]
        entry = FacultyAvailability.objects.create(faculty=foreign_faculty, academic_term=self.term, day_of_week=1, start_time=time(8), end_time=time(10), availability_type="available")
        assignment = FacultySubjectAssignment.objects.create(faculty=foreign_faculty, subject_offering=self.offerings[self.external.pk])
        for user in (self.dean, self.chair):
            self.client.force_login(user)
            for section, obj in (("availability", entry), ("assignments", assignment), ("offerings", self.offerings[self.external.pk])):
                for action in ("detail", "edit"):
                    self.assertEqual(self.client.get(self.url(f"{section}-{action}", obj.pk)).status_code, 404)
                self.assertEqual(self.client.post(self.url(f"{section}-edit", obj.pk), {}).status_code, 404)
                if section != "offerings":
                    self.assertEqual(self.client.post(self.url(f"{section}-delete", obj.pk)).status_code, 404)
            self.assertEqual(self.client.get(self.url("faculty", foreign_faculty.pk)).status_code, 404)

    def test_manipulated_faculty_offering_subject_term_post(self):
        self.client.force_login(self.chair)
        for section, data in [("assignments", self.assignment_data(faculty=self.records[self.sibling.pk]["faculty-management"].pk)), ("assignments", self.assignment_data(subject_offering=self.offerings[self.external.pk].pk)), ("availability", self.availability_data(faculty=self.records[self.external.pk]["faculty-management"].pk)), ("offerings", self.offering_data(subject=self.offerings[self.sibling.pk].subject_id)), ("assignments", self.assignment_data(academic_term=self.later.pk))]:
            response = self.client.post(self.url(f"{section}-add"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_invalid_and_foreign_filters_never_widen_scope(self):
        self.client.force_login(self.chair)
        for section in ("monitor", "offerings", "availability", "assignments"):
            for query in (f"department={self.sibling.pk}", f"college={self.other_college.pk}", "department=garbage"):
                response = self.client.get(self.url(section) + "&" + query)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)
            self.assertEqual(self.client.get(reverse(f"workloads:{section}") + "?academic_term=bad").status_code, 400)

    def test_csrf_and_method_safety(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.chair)
        self.client.force_login(self.chair)
        for section in ("availability", "offerings", "assignments"):
            self.assertEqual(client.post(self.url(f"{section}-add"), {}).status_code, 403)
            self.assertEqual(client.delete(self.url(f"{section}-add")).status_code, 403)
            self.assertEqual(self.client.delete(self.url(f"{section}-add")).status_code, 405)

    def test_dashboard_navigation_and_workload_filters(self):
        self.policy(recommended_load=6, maximum_load=9)
        self.save_assignment()
        self.client.force_login(self.chair)
        # Overview picks the latest active term; the monitor uses the explicit selection.
        AcademicTerm.objects.filter(pk=self.later.pk).update(is_active=False)
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.context["teaching_assignment_count"], 1)
        self.assertContains(dashboard, 'href="/workloads/availability/"')
        response = self.client.get(self.url("monitor") + "&workload_status=UNDERLOAD&q=Person1")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(self.client.get(self.url("faculty", self.faculty.pk)), "Department workload policy")
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get("/dashboard/"), 'href="/workloads/availability/"')

    def test_future_datasets_are_scoped_and_permission_checked(self):
        self.assertEqual(len(list(faculty_candidates(self.chair, self.term))), 1)
        self.assertEqual(len(offering_data(self.dean, self.term)), 2)
        with self.assertRaises(PermissionDenied):
            list(faculty_candidates(self.staff, self.term))
        with self.assertRaises(PermissionDenied):
            offering_data(self.staff, self.term)


class TeachingSeedTests(TestCase):
    def test_optional_seed_is_idempotent_preserves_passwords_and_has_no_schedules(self):
        from scheduling.models import Assignment
        with TemporaryDirectory() as directory, override_settings(DEBUG=True, BASE_DIR=Path(directory)):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("seed_foundation", create_users=True, with_teaching=True, stdout=StringIO())
            passwords = dict(get_user_model().objects.values_list("username", "password"))
            count = FacultyAvailability.objects.count()
            call_command("seed_teaching", stdout=StringIO())
            self.assertEqual(FacultyAvailability.objects.count(), count)
            self.assertEqual(SubjectOffering.objects.count(), 2)
            self.assertEqual(FacultySubjectAssignment.objects.count(), 2)
            self.assertEqual(dict(get_user_model().objects.values_list("username", "password")), passwords)
            self.assertEqual(Assignment.objects.count(), 0)

    def test_teaching_seed_rejects_production(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("seed_teaching", stdout=StringIO())

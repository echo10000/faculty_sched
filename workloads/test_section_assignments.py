from decimal import Decimal

from django.test import Client
from django.urls import reverse

from audit.models import AuditLog
from resources.forms import FacultyForm
from resources.services import save_resource
from faculty.models import Faculty
from timetabling.models import ClassSection, OfferingRequirement
from workloads.models import FacultySubjectAssignment
from workloads.tests import TeachingFixture


class SectionAssignmentWorkspaceTests(TeachingFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.section = ClassSection.objects.create(academic_term=cls.term, department=cls.department,
                                                code="TEST-1A", year_level=1)
        OfferingRequirement.objects.create(section=cls.section, subject_offering=cls.offering)
        cls.foreign_section = ClassSection.objects.create(academic_term=cls.term, department=cls.external,
                                                        code="FOREIGN-1A", year_level=1)
        OfferingRequirement.objects.create(section=cls.foreign_section, subject_offering=cls.offerings[cls.external.pk])

    def workspace_url(self):
        return reverse("workloads:section-assignments")

    def selection(self, **updates):
        return {"academic_term": self.term.pk, "department": self.department.pk,
                "year_level": 1, "section": self.section.pk, **updates}

    def payload(self, **updates):
        return {**self.selection(), **self.assignment_data(), "intent": "preview", **updates}

    def test_subjects_before_assignments_and_grouped_staff_teaching_tools(self):
        self.client.force_login(self.chair)
        response = self.client.get(self.workspace_url(), self.selection())
        self.assertContains(response, self.offering.subject.title)
        self.assertContains(response, "Choose an instructor")
        self.assertNotContains(response, "FOREIGN-1A")
        group = next(group for group in response.context["navigation_groups"] if group["label"] == "Faculty & Teaching")
        self.assertEqual([link["route"] for link in group["links"]],
                         ["faculty-management:list", "workloads:section-assignments", "workloads:monitor",
                          "workloads:availability", "workloads:assignments", "workloads:balancing",
                          "workloads:balancing-runs"])

    def test_preview_has_authoritative_load_but_does_not_write_and_save_is_audited(self):
        self.policy(recommended_load=18, maximum_load=24)
        self.client.force_login(self.chair)
        original_audits = AuditLog.objects.count()
        response = self.client.post(self.workspace_url(), self.payload(), HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.json()["preview"]["assigned_load"]), Decimal("3.5"))
        self.assertFalse(FacultySubjectAssignment.objects.exists())
        self.assertEqual(AuditLog.objects.count(), original_audits)
        response = self.client.post(self.workspace_url(), self.payload(intent="save"))
        self.assertEqual(response.status_code, 302)
        assignment = FacultySubjectAssignment.objects.get()
        self.assertEqual(assignment.subject_offering, self.offering)
        self.assertEqual(assignment.created_by, self.chair)
        self.assertTrue(AuditLog.objects.filter(action="facultysubjectassignment.created").exists())
        self.assertContains(self.client.get(response.url), "3.50")

    def test_scope_and_section_subject_tampering_cannot_save(self):
        self.client.force_login(self.chair)
        for changes in ({"section": self.foreign_section.pk},
                        {"subject_offering": self.offerings[self.external.pk].pk},
                        {"faculty": self.records[self.external.pk]["faculty-management"].pk},
                        {"year_level": 5}):
            with self.subTest(changes=changes):
                response = self.client.post(self.workspace_url(), self.payload(intent="save", **changes))
                self.assertIn(response.status_code, (200, 400, 404))
                self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_hard_limits_and_existing_teaching_shares_are_preserved(self):
        self.policy(recommended_load=1, maximum_load=3, enforce_maximum=True)
        self.client.force_login(self.chair)
        response = self.client.post(self.workspace_url(), self.payload(intent="save"))
        self.assertContains(response, "hard maximum")
        self.assertFalse(FacultySubjectAssignment.objects.exists())
        first, _ = self.save_assignment(share="0.5")
        colleague = Faculty.objects.create(employee_id="COLLEAGUE", first_name="Another", last_name="Instructor",
                                           home_department=self.department, employment_category=self.category)
        response = self.client.post(self.workspace_url(), self.payload(intent="save", share="0.75", faculty=colleague.pk))
        self.assertContains(response, "exceed 1")
        first.refresh_from_db()
        self.assertEqual(first.share, Decimal("0.5"))
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)

    def test_workspace_post_retains_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.chair)
        self.assertEqual(client.post(self.workspace_url(), self.payload(intent="save")).status_code, 403)

    def test_save_rechecks_policy_after_a_successful_preview(self):
        policy = self.policy(recommended_load=18, maximum_load=24, enforce_maximum=True)
        self.client.force_login(self.chair)
        response = self.client.post(self.workspace_url(), self.payload(), HTTP_ACCEPT="application/json")
        self.assertIsNotNone(response.json()["preview"])
        policy.recommended_load, policy.maximum_load = 1, 3
        policy.save()
        response = self.client.post(self.workspace_url(), self.payload(intent="save"))
        self.assertContains(response, "hard maximum")
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_stale_full_share_post_shows_error_without_changing_assignment(self):
        original, _ = self.save_assignment()
        self.client.force_login(self.chair)
        response = self.client.post(self.workspace_url(), self.payload(intent="save"))
        self.assertContains(response, "already exists")
        self.assertEqual(FacultySubjectAssignment.objects.get().pk, original.pk)

    def test_workload_attention_hides_below_target_and_keeps_configuration_warnings(self):
        policy = self.policy(recommended_load=18, maximum_load=24)
        self.save_assignment()
        self.client.force_login(self.chair)
        response = self.client.get(self.url("monitor") + "&attention=1")
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertContains(response, "No workload problems need attention")
        policy.delete()
        response = self.client.get(self.url("monitor") + "&attention=1")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "Unconfigured")

    def test_current_term_policy_blocks_previous_term_posts(self):
        from academics.test_rollover import sample_calendar
        from academics.calendar import ensure_calendar_periods
        from datetime import date
        from unittest.mock import patch
        sample_calendar()
        ensure_calendar_periods(date(2026, 10, 1))
        self.client.force_login(self.chair)
        with patch("academics.calendar.timezone.localdate", return_value=date(2026, 10, 1)):
            self.assertEqual(self.client.post(self.workspace_url(), self.payload(intent="save")).status_code, 404)
            self.assertFalse(FacultySubjectAssignment.objects.exists())


class StaffFacultyLimitTests(TeachingFixture):
    def test_staff_cannot_change_limits_with_a_forged_post_but_admin_can(self):
        self.faculty.recommended_load = 18
        self.faculty.maximum_load = 24
        self.faculty.save()
        data = super().payload("faculty-management", identifier=self.faculty.employee_id)
        data.update(recommended_load="1", maximum_load="2")
        form = FacultyForm(instance=self.faculty, user=self.chair)
        self.assertNotIn("maximum_load", form.fields)
        self.assertNotIn("recommended_load", form.fields)
        obj, form = save_resource(user=self.chair, form_class=FacultyForm, data=data, pk=self.faculty.pk)
        self.assertIsNotNone(obj, form.errors)
        obj.refresh_from_db()
        self.assertEqual((obj.recommended_load, obj.maximum_load), (18, 24))
        obj, form = save_resource(user=self.admin, form_class=FacultyForm, data=data, pk=self.faculty.pk)
        self.assertIsNotNone(obj, form.errors)
        self.assertEqual((obj.recommended_load, obj.maximum_load), (1, 2))

    def test_staff_creation_does_not_set_individual_limits(self):
        data = super().payload("faculty-management")
        obj, form = save_resource(user=self.chair, form_class=FacultyForm, data=data)
        self.assertIsNotNone(obj, form.errors)
        self.assertIsNone(obj.maximum_load)
        self.assertIsNone(obj.recommended_load)

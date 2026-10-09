from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from accounts.models import AdminProfile
from audit.models import AuditLog
from faculty.models import Faculty
from timetabling.models import ClassSection, OfferingRequirement, Schedule
from workloads.models import FacultySubjectAssignment, SubjectOffering
from workloads.tests import TeachingFixture


class AssignmentDeskTests(TeachingFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.section = ClassSection.objects.create(academic_term=cls.term,
            department=cls.department, code="DESK-1A", year_level=1)
        cls.partial = SubjectOffering.objects.create(subject=cls.offering.subject,
            academic_term=cls.term, department=cls.department, code="PARTIAL",
            lecture_units=2, laboratory_units=1, lecture_hours=2, laboratory_hours=3)
        cls.full = SubjectOffering.objects.create(subject=cls.offering.subject,
            academic_term=cls.term, department=cls.department, code="FULL",
            lecture_units=2, laboratory_units=1, lecture_hours=2, laboratory_hours=3)
        for offering in (cls.offering, cls.partial, cls.full):
            OfferingRequirement.objects.create(section=cls.section, subject_offering=offering)
        cls.colleague = Faculty.objects.create(employee_id="DESK-F2", first_name="Casey",
            last_name="Example", home_department=cls.department, employment_category=cls.category)

    def setUp(self):
        self.client.force_login(self.chair)

    def url(self):
        return reverse("workloads:section-assignments")

    def selection(self, **changes):
        return {"academic_term": self.term.pk, "department": self.department.pk,
                "year_level": 1, "section": self.section.pk, **changes}

    def payload(self, **changes):
        return {**self.selection(), **self.assignment_data(), "intent": "preview", **changes}

    def saved_shares(self):
        self.save_assignment(subject_offering=self.partial.pk, share="0.5")
        self.save_assignment(subject_offering=self.full.pk)

    def test_counts_reflect_saved_offering_shares_not_timetable_readiness(self):
        self.saved_shares()
        response = self.client.get(self.url(), self.selection())
        self.assertEqual(response.context.get("assignment_counts"), {"full": 1, "partial": 1, "needed": 1})
        rows = {row["offering"].pk: row for row in response.context["rows"]}
        self.assertEqual(rows[self.offering.pk]["assignment_state"], "needed")
        self.assertEqual(rows[self.partial.pk]["assignment_state"], "partial")
        self.assertEqual(rows[self.full.pk]["assignment_state"], "full")
        self.assertIsNone(rows[self.full.pk]["form"])
        self.assertEqual(rows[self.partial.pk]["form"]["share"].value(), Decimal("0.50"))
        self.assertContains(response, "They do not confirm workload approval or timetable readiness")

    def test_unassigned_offerings_start_without_a_selected_instructor(self):
        response = self.client.get(self.url(), self.selection())
        self.assertEqual(response.context.get("assignment_counts", {}).get("needed"), 3)
        for row in response.context["rows"]:
            self.assertIsNone(row["form"]["faculty"].value())
            self.assertEqual(row["form"].fields["faculty"].choices[0][0], "")
        failed = self.client.post(self.url(), self.payload(faculty="", intent="save"))
        self.assertTrue(failed.context["rows"][0]["form"].errors)
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_preview_comparison_uses_authoritative_units_hours_and_load_without_writes(self):
        self.policy(recommended_load=15, maximum_load=18)
        self.saved_shares()
        before = AuditLog.objects.count()
        response = self.client.post(self.url(), self.payload())
        report = next(row["preview"] for row in response.context["rows"] if row["preview"])
        self.assertEqual(report.get("current_teaching_units"), Decimal("4.5"))
        self.assertEqual(report["current_teaching_hours"], Decimal("7.5"))
        self.assertEqual(report["current_load"], Decimal("5.25"))
        self.assertEqual(report["change_load"], Decimal("3.5"))
        self.assertEqual(report["assigned_load"], Decimal("8.75"))
        self.assertContains(response, "Projected")
        self.assertEqual(FacultySubjectAssignment.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), before)
        result = self.client.post(self.url(), self.payload(), HTTP_ACCEPT="application/json").json()
        self.assertEqual(Decimal(result["preview"]["assigned_load"]), Decimal("8.75"))
        self.assertIn("Current", result["preview"]["summary_html"])
        self.assertIn("Weekly contact hours", result["preview"]["summary_html"])

    def test_missing_policy_does_not_present_zero_capacity_or_approval(self):
        result = self.client.post(self.url(), self.payload(), HTTP_ACCEPT="application/json").json()
        self.assertIsNone(result["preview"]["assigned_load"])
        self.assertIsNone(result["preview"]["maximum_load"])
        self.assertIn("Cannot calculate", result["preview"]["summary_html"])
        self.assertIn("Unconfigured", result["preview"]["summary_html"])
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_shared_save_preserves_existing_instructor_and_refreshes_coverage(self):
        first, _ = self.save_assignment(share="0.5")
        self.assertContains(self.client.get(self.url(), self.selection()), "Partially assigned")
        response = self.client.post(self.url(), self.payload(faculty=self.colleague.pk, share="0.5", intent="save"), follow=True)
        first.refresh_from_db()
        self.assertEqual(first.share, Decimal("0.50"))
        self.assertEqual(FacultySubjectAssignment.objects.filter(subject_offering=self.offering).count(), 2)
        self.assertEqual(response.context["assignment_counts"]["full"], 1)
        self.assertContains(response, "assigned to")

    def test_share_failure_keeps_entered_faculty_share_and_saved_record(self):
        first, _ = self.save_assignment(share="0.5")
        response = self.client.post(self.url(), self.payload(faculty=self.colleague.pk, share="0.75", intent="save"))
        row = next(row for row in response.context["rows"] if row["offering"] == self.offering)
        self.assertEqual(str(row["form"]["faculty"].value()), str(self.colleague.pk))
        self.assertEqual(row["form"]["share"].value(), "0.75")
        self.assertContains(response, "exceed 1")
        first.refresh_from_db()
        self.assertEqual(first.share, Decimal("0.50"))
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)

    def test_save_rechecks_changed_share_after_preview(self):
        response = self.client.post(self.url(), self.payload(), HTTP_ACCEPT="application/json")
        self.assertIsNotNone(response.json()["preview"])
        self.save_assignment(faculty=self.colleague.pk, share="0.5")
        response = self.client.post(self.url(), self.payload(intent="save"))
        self.assertContains(response, "exceed 1")
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)

    def test_save_rechecks_changed_hard_policy_after_preview(self):
        policy = self.policy(recommended_load=15, maximum_load=18, enforce_maximum=True)
        self.assertIsNotNone(self.client.post(self.url(), self.payload(), HTTP_ACCEPT="application/json").json()["preview"])
        policy.maximum_load, policy.recommended_load = 3, 3
        policy.save()
        response = self.client.post(self.url(), self.payload(intent="save"))
        self.assertContains(response, "hard maximum")
        self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_foreign_faculty_and_offering_cannot_save_or_preview(self):
        for changes in ({"faculty": self.records[self.external.pk]["faculty-management"].pk},
                        {"subject_offering": self.offerings[self.external.pk].pk}):
            for intent in ("preview", "save"):
                response = self.client.post(self.url(), self.payload(**changes, intent=intent))
                self.assertIn(response.status_code, (200, 400, 404))
                self.assertFalse(FacultySubjectAssignment.objects.exists())

    def test_existing_authorized_staff_grants_and_faculty_restrictions_are_preserved(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url(), self.selection()).status_code, 200)
        self.assertEqual(self.client.post(self.url(), self.payload(intent="save")).status_code, 302)
        user = get_user_model().objects.create_user(username="desk-faculty")
        self.faculty.user = user
        self.faculty.save()
        AdminProfile.objects.create(user=user, role="faculty")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url(), self.selection()).status_code, 403)

    def test_guided_context_and_selected_offering_survive_save_redirect(self):
        schedule = Schedule.objects.create(name="Assignment desk review", academic_term=self.term, department=self.department)
        response = self.client.post(self.url() + f"?workflow_schedule={schedule.pk}&workflow_stage=2",
                                   self.payload(intent="save"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["workflow_schedule"], [str(schedule.pk)])
        self.assertEqual(urlsplit(response.url).fragment, f"assignment-{self.offering.pk}")
        page = self.client.get(response.url)
        self.assertEqual(page.context["guide_continue"]["number"], 3)
        self.assertContains(page, "Continue to teaching-unit review")
        self.assertContains(page, ">Draft</span>")

    def test_another_section_uses_its_own_offering_of_same_catalog_subject(self):
        other = ClassSection.objects.create(academic_term=self.term, department=self.department, code="DESK-1B", year_level=1)
        offering = SubjectOffering.objects.create(subject=self.offering.subject, academic_term=self.term,
            department=self.department, code="OTHER-SECTION", lecture_units=2, laboratory_units=1,
            lecture_hours=2, laboratory_hours=3)
        OfferingRequirement.objects.create(section=other, subject_offering=offering)
        self.save_assignment(subject_offering=offering.pk)
        page = self.client.get(self.url(), self.selection())
        self.assertEqual(page.context.get("assignment_counts", {}).get("needed"), 3)
        self.assertNotIn(offering.pk, [row["offering"].pk for row in page.context["rows"]])

    def test_csrf_protection_remains_on_the_workspace(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.chair)
        self.assertEqual(client.post(self.url(), self.payload(intent="save")).status_code, 403)

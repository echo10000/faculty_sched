from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit

from django.urls import reverse

from audit.models import AuditLog
from faculty.models import Designation
from timetabling.models import ClassSection, OfferingRequirement, Schedule
from .models import FacultySubjectAssignment, FacultyTermCapacity, WorkloadPolicy
from .tests import TeachingFixture


class TeachingLoadInterfaceTests(TeachingFixture):
    def test_visible_measures_and_designation_do_not_reduce_limits(self):
        self.policy(recommended_load=18, maximum_load=24)
        self.faculty.designation = Designation.objects.create(name="Department chair", units_released=6)
        self.faculty.save()
        self.save_assignment(share="0.5")
        self.client.force_login(self.chair)
        page = self.client.get(self.url("faculty", self.faculty.pk))
        for label in ("Assigned academic teaching units", "Weekly contact hours", "Weighted teaching load",
                      "Recommended teaching target", "Configured maximum load", "Remaining capacity",
                      "Department workload policy", "Department chair", "6.0"):
            self.assertContains(page, label)
        report = page.context["report"]
        self.assertEqual(report["teaching_units"], Decimal("1.5"))
        self.assertEqual(report["teaching_hours"], Decimal("2.5"))
        self.assertEqual(report["assigned_load"], Decimal("1.75"))
        self.assertEqual(report["policy"]["recommended_load"], 18)
        self.assertEqual(report["remaining_capacity"], Decimal("22.25"))
        self.assertNotContains(page, "Configure faculty baseline")

    def test_visible_independent_limit_sources_and_explicit_zero(self):
        WorkloadPolicy.objects.create(academic_term=self.term, recommended_load=18, maximum_load=24,
                                     lecture_weight=1, laboratory_weight=1)
        college = WorkloadPolicy.objects.create(academic_term=self.term, college=self.college, maximum_load=23)
        department = WorkloadPolicy.objects.create(academic_term=self.term, department=self.department, maximum_load=22)
        self.client.force_login(self.admin)
        for expected, mutate in (
            ("Department workload policy", lambda: None),
            ("College workload policy", lambda: department.delete()),
            ("Institution workload policy", lambda: college.delete()),
            ("Faculty baseline", lambda: self.set_baseline(0, 21)),
            ("Faculty term override", lambda: FacultyTermCapacity.objects.create(
                faculty=self.faculty, academic_term=self.term, recommended_load=0, maximum_load=0)),
        ):
            mutate()
            page = self.client.get(self.url("faculty", self.faculty.pk))
            self.assertContains(page, expected)
            self.assertContains(page, "Configured maximum load")
        self.assertEqual(page.context["report"]["policy"]["maximum_load"], 0)
        self.assertContains(page, "0.00")

    def set_baseline(self, recommended, maximum):
        self.faculty.recommended_load, self.faculty.maximum_load = recommended, maximum
        self.faculty.save()

    def test_missing_weights_and_maximum_are_not_displayed_as_zero_capacity(self):
        self.save_assignment()
        self.client.force_login(self.chair)
        page = self.client.get(self.url("faculty", self.faculty.pk))
        self.assertContains(page, "Remaining capacity")
        self.assertContains(page, "Cannot calculate without a maximum and teaching weights")
        self.assertContains(page, "No designation recorded")
        self.assertEqual(page.context["report"]["assigned_load"], None)

    def test_workload_directory_separates_units_hours_limits_and_capacity(self):
        self.policy(recommended_load=18, maximum_load=24)
        self.save_assignment(share="0.5")
        self.client.force_login(self.chair)
        page = self.client.get(self.url("monitor") + f"&faculty={self.faculty.pk}")
        for text in ("Academic units", "Contact hours", "Weighted load", "Recommended target",
                     "Configured maximum", "Remaining capacity", "1.50", "2.50", "1.75", "22.25", "Department workload policy"):
            self.assertContains(page, text)

    def baseline_payload(self, **updates):
        return {"employee_id": self.faculty.employee_id, "first_name": self.faculty.first_name,
                "last_name": self.faculty.last_name, "college": self.college.pk,
                "home_department": self.department.pk, "employment_category": self.category.pk,
                "recommended_load": "18.25", "maximum_load": "24.75", **updates}

    def test_fractional_baseline_save_is_audited_and_returns_to_validated_guide(self):
        schedule = Schedule.objects.create(name="Load review", academic_term=self.term, department=self.department)
        query = {"academic_term": self.term.pk, "workload": "1", "workflow_schedule": schedule.pk,
                 "workflow_stage": 3}
        url = reverse("faculty-management:edit", args=[self.faculty.pk]) + "?" + urlencode(query)
        self.client.force_login(self.admin)
        page = self.client.get(url)
        self.assertContains(page, "data-load-control", count=2)
        self.assertContains(page, "Faculty baseline applies across terms")
        response = self.client.post(url, self.baseline_payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("workloads:faculty", args=[self.faculty.pk]))
        returned = parse_qs(urlsplit(response.url).query)
        self.assertEqual(returned["academic_term"], [str(self.term.pk)])
        self.assertEqual(returned["workflow_schedule"], [str(schedule.pk)])
        self.assertEqual(returned["workflow_stage"], ["3"])
        self.faculty.refresh_from_db()
        self.assertEqual(self.faculty.recommended_load, Decimal("18.25"))
        self.assertEqual(self.faculty.maximum_load, Decimal("24.75"))
        self.assertTrue(AuditLog.objects.filter(action="faculty.updated", actor=self.admin).exists())

    def test_invalid_decimal_or_order_preserves_inputs_and_does_not_audit(self):
        self.client.force_login(self.admin)
        url = reverse("faculty-management:edit", args=[self.faculty.pk])
        for value in ("-1", "25", "1.001", "invalid"):
            page = self.client.post(url, self.baseline_payload(recommended_load=value, maximum_load="24"))
            self.assertEqual(page.status_code, 200)
            self.assertTrue(page.context["form"].errors)
            self.assertEqual(page.context["form"]["recommended_load"].value(), value)
            self.assertContains(page, "data-load-control", count=2)
            if value == "25":
                self.assertIn("recommended_load", page.context["form"].errors)
                self.assertNotContains(page, "faculty_capacity_ordered")
        self.assertFalse(AuditLog.objects.filter(action="faculty.updated").exists())

    def test_staff_forged_slider_values_do_not_write_limits(self):
        self.set_baseline(18, 24)
        self.client.force_login(self.chair)
        url = reverse("faculty-management:edit", args=[self.faculty.pk])
        self.assertNotContains(self.client.get(url), "data-load-control")
        self.assertEqual(self.client.post(url, self.baseline_payload()).status_code, 302)
        self.faculty.refresh_from_db()
        self.assertEqual(self.faculty.recommended_load, 18)
        self.assertEqual(self.faculty.maximum_load, 24)
        self.assertEqual(self.client.get(self.url("faculty", self.records[self.external.pk]["faculty-management"].pk)).status_code, 404)

    def test_invalid_return_context_is_rejected_before_configuration_writes(self):
        self.client.force_login(self.admin)
        url = reverse("faculty-management:edit", args=[self.faculty.pk]) + "?workload=1&academic_term=invalid"
        self.assertEqual(self.client.post(url, self.baseline_payload()).status_code, 404)
        schedule = Schedule.objects.create(name="Different term", academic_term=self.later, department=self.department)
        url = reverse("faculty-management:edit", args=[self.faculty.pk]) + "?" + urlencode({
            "workload": 1, "academic_term": self.term.pk, "schedule": schedule.pk})
        self.assertEqual(self.client.post(url, self.baseline_payload()).status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action="faculty.updated").exists())

    def test_override_link_opens_existing_admin_form_and_preserves_the_workload_page(self):
        override = FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term, maximum_load=24)
        self.client.force_login(self.admin)
        page = self.client.get(self.url("faculty", self.faculty.pk))
        self.assertContains(page, reverse("admin:workloads_facultytermcapacity_change", args=[override.pk]))
        self.assertContains(page, 'target="_blank"')
        self.client.force_login(self.chair)
        self.assertNotContains(self.client.get(self.url("faculty", self.faculty.pk)), "/admin/workloads/")

    def test_admin_term_override_controls_keep_inherited_validation_and_audit(self):
        self.policy(recommended_load=18, maximum_load=24)
        self.client.force_login(self.admin)
        url = reverse("admin:workloads_facultytermcapacity_add")
        self.assertContains(self.client.get(url), "data-load-control", count=2)
        data = {"faculty": self.faculty.pk, "academic_term": self.term.pk,
                "recommended_load": "", "maximum_load": "17.5", "_save": "Save"}
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertFalse(FacultyTermCapacity.objects.exists())
        data["maximum_load"] = "24.75"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(FacultyTermCapacity.objects.get().maximum_load, Decimal("24.75"))
        self.assertTrue(AuditLog.objects.filter(action="record.create", object_type="workloads.FacultyTermCapacity").exists())
        self.assertContains(self.client.get(reverse("admin:workloads_workloadpolicy_add")), "data-load-control", count=2)


class TeachingLoadPreviewTests(TeachingFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.section = ClassSection.objects.create(academic_term=cls.term, department=cls.department,
                                                 code="TEST-1A", year_level=1)
        OfferingRequirement.objects.create(section=cls.section, subject_offering=cls.offering)

    def workspace_url(self):
        return reverse("workloads:section-assignments")

    def payload(self, **updates):
        return {"department": self.department.pk, "year_level": 1, "section": self.section.pk,
                **self.assignment_data(), "intent": "preview", **updates}

    def test_live_and_non_js_previews_share_authoritative_summary_without_writes(self):
        self.policy(recommended_load=18, maximum_load=24)
        self.client.force_login(self.chair)
        response = self.client.post(self.workspace_url(), self.payload(share="0.5"), HTTP_ACCEPT="application/json")
        preview = response.json()["preview"]
        self.assertEqual(Decimal(preview["assigned_load"]), Decimal("1.75"))
        summary = preview["summary_html"]
        for text in ("Assigned academic teaching units", "Weekly contact hours", "Weighted teaching load",
                     "Recommended teaching target", "Configured maximum load", "Remaining capacity",
                     "1.50", "2.50", "1.75", "22.25", "Department workload policy"):
            self.assertIn(text, summary)
        page = self.client.post(self.workspace_url(), self.payload(share="0.5"))
        self.assertContains(page, "Remaining capacity")
        page = self.client.post(self.url("assignments-add"), self.assignment_data(share="0.5", intent="preview"))
        self.assertContains(page, "Recommended teaching target")
        self.assertFalse(FacultySubjectAssignment.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="facultysubjectassignment.created").exists())

    def test_designation_labels_are_escaped_in_live_preview_html(self):
        self.policy(recommended_load=18, maximum_load=24)
        self.faculty.designation = Designation.objects.create(name='<script>alert("designation")</script>', units_released=6)
        self.faculty.save()
        self.client.force_login(self.chair)
        preview = self.client.post(self.workspace_url(), self.payload(), HTTP_ACCEPT="application/json").json()["preview"]
        self.assertIn("&lt;script&gt;", preview["summary_html"])
        self.assertNotIn("<script>", preview["summary_html"])

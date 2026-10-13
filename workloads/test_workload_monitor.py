"""Read-only workload monitoring must retain authoritative teaching reports."""
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import AdminProfile
from audit.models import AuditLog
from faculty.models import Designation, EmploymentCategory, Faculty
from timetabling.models import ClassSection, OfferingRequirement, Schedule
from workloads.calculation import calculate_workload
from workloads.models import FacultySubjectAssignment, FacultyTermCapacity, SubjectOffering, WorkloadPolicy
from workloads.monitoring import decorate_report
from workloads.tests import TeachingFixture


class WorkloadMonitorTests(TeachingFixture):
    def setUp(self):
        self.client.force_login(self.chair)

    def monitor(self, **query):
        return self.client.get(reverse("workloads:monitor"), {"academic_term": self.term.pk, **query})

    def row(self, response, person=None):
        person = person or self.faculty
        return next(row for row in response.context["page_obj"] if row["faculty"].pk == person.pk)

    def counts(self, response):
        return {item["condition"]: item["count"] for item in response.context["condition_summary"]}

    def extra_faculty(self, code, **values):
        return Faculty.objects.create(employee_id=code, first_name=code, last_name="Monitor",
            home_department=self.department, employment_category=self.category, **values)

    def test_distinct_measures_and_shared_contributions_use_saved_decimal_shares(self):
        self.policy(recommended_load=3, maximum_load=6)
        colleague = self.extra_faculty("MON-SHARED")
        assignment, _ = self.save_assignment(share="0.33")
        self.save_assignment(faculty=colleague.pk, share="0.67")
        second = SubjectOffering.objects.create(subject=self.offering.subject, academic_term=self.term,
            department=self.department, code="MON-SECOND", lecture_units=1, laboratory_units=0,
            lecture_hours=4, laboratory_hours=0)
        self.save_assignment(subject_offering=second.pk, share="0.25")
        response = self.monitor(selected_faculty=self.faculty.pk)
        row = self.row(response)
        self.assertEqual(row["teaching_units"], Decimal("1.24"))
        self.assertEqual(row["teaching_hours"], Decimal("2.65"))
        self.assertEqual(row["assigned_load"], Decimal("1.405"))
        contributions = row["monitor"]["contributions"]
        shared = next(item for item in contributions if item["assignment"].pk == assignment.pk)
        self.assertEqual(shared["share_percent"], Decimal("33"))
        self.assertEqual(shared["units"], Decimal("0.99"))
        self.assertEqual(shared["hours"], Decimal("1.65"))
        self.assertEqual(shared["load"], Decimal("1.155"))
        for contribution_field, report_field in (("units", "teaching_units"),
                                                  ("hours", "teaching_hours"), ("load", "assigned_load")):
            self.assertEqual(sum(item[contribution_field] for item in contributions), row[report_field])
        self.assertEqual(self.row(response, colleague)["assigned_load"], Decimal("2.345"))

    def test_all_five_conditions_and_summary_counters_agree(self):
        self.policy(recommended_load=2, maximum_load=5)
        within = self.extra_faculty("MON-WITHIN")
        at = self.extra_faculty("MON-AT", recommended_load=0, maximum_load=0)
        above = self.extra_faculty("MON-ABOVE", recommended_load=1, maximum_load=2)
        for index, person in enumerate((within, above)):
            offering = SubjectOffering.objects.create(subject=self.offering.subject,
                academic_term=self.term, department=self.department, code=f"MON-{index}",
                lecture_units=3, laboratory_units=0, lecture_hours=3, laboratory_hours=0)
            self.save_assignment(faculty=person.pk, subject_offering=offering.pk)
        # No policy applies to the sibling department visible to college staff.
        review = self.records[self.sibling.pk]["faculty-management"]
        self.client.force_login(self.dean)
        response = self.monitor()
        expected = {self.faculty.pk: "UNDERLOAD", within.pk: "WITHIN_LOAD",
            at.pk: "AT_CAPACITY", above.pk: "OVERLOAD", review.pk: "UNCONFIGURED"}
        self.assertEqual({row["faculty"].pk: row["monitor"]["condition"]
            for row in response.context["page_obj"]}, expected)
        self.assertEqual(self.counts(response), dict.fromkeys(expected.values(), 1))
        self.assertEqual(response.context["context_count"], 5)

    def test_target_only_above_target_is_review_without_changing_raw_overload(self):
        self.policy(recommended_load=3)
        self.save_assignment()
        response = self.monitor()
        row = self.row(response)
        self.assertEqual(row["assigned_load"], Decimal("3.5"))
        self.assertIsNone(row["policy"]["maximum_load"])
        self.assertEqual(row["status"], "OVERLOAD")
        self.assertEqual(row["monitor"]["condition"], "UNCONFIGURED")
        self.assertContains(response, "Above target · Maximum not configured")
        self.assertEqual(self.counts(response)["OVERLOAD"], 0)
        self.assertEqual(self.counts(response)["UNCONFIGURED"], 1)
        self.assertEqual(self.monitor(condition="OVERLOAD").context["page_obj"].paginator.count, 0)
        self.assertEqual(self.monitor(condition="UNCONFIGURED").context["page_obj"].paginator.count, 1)

    def test_target_only_at_or_below_target_still_requires_maximum_policy_review(self):
        policy = self.policy(recommended_load=4)
        self.save_assignment()
        for target, status, position in ((Decimal("4"), "UNDERLOAD", "Below target"),
                                         (Decimal("3.5"), "WITHIN_LOAD", "Meets target")):
            with self.subTest(target=target):
                policy.recommended_load = target
                policy.save()
                row = self.row(self.monitor())
                self.assertEqual(row["status"], status)
                self.assertEqual(row["monitor"]["condition"], "UNCONFIGURED")
                self.assertEqual(row["monitor"]["position"], f"{position} · Maximum not configured")

    def test_legacy_status_and_attention_filters_keep_backend_semantics(self):
        self.policy(recommended_load=3)
        self.save_assignment()
        for query in ({"workload_status": "OVERLOAD"}, {"attention": "1"}):
            response = self.monitor(**query)
            self.assertEqual(response.context["page_obj"].paginator.count, 1)
            self.assertEqual(self.row(response)["monitor"]["condition"], "UNCONFIGURED")
        self.assertEqual(self.monitor(workload_status="WITHIN_LOAD").context["page_obj"].paginator.count, 0)

    def test_above_advisory_target_below_maximum_is_within_maximum(self):
        self.policy(recommended_load=3, maximum_load=6, enforce_maximum=True)
        self.save_assignment()
        response = self.monitor()
        row = self.row(response)
        self.assertEqual(row["status"], "WITHIN_LOAD")
        self.assertEqual(row["monitor"]["condition"], "WITHIN_LOAD")
        self.assertEqual(row["remaining_capacity"], Decimal("2.5"))
        self.assertContains(response, "Within maximum")
        self.assertContains(response, "Above target · Within maximum")

    def test_known_load_with_no_limits_stays_visible_and_capacity_unknown(self):
        self.policy()
        self.save_assignment()
        row = self.row(self.monitor())
        self.assertEqual(row["assigned_load"], Decimal("3.5"))
        self.assertEqual(row["teaching_units"], Decimal("3"))
        self.assertIsNone(row["remaining_capacity"])
        self.assertEqual(row["monitor"]["condition"], "UNCONFIGURED")

    def test_missing_required_weight_is_unknown_while_units_and_hours_remain_known(self):
        WorkloadPolicy.objects.create(academic_term=self.term, department=self.department,
            recommended_load=3, maximum_load=6, lecture_weight=1)
        self.save_assignment()
        row = self.row(self.monitor(selected_faculty=self.faculty.pk))
        self.assertIsNone(row["assigned_load"])
        self.assertEqual(row["teaching_units"], Decimal("3"))
        self.assertEqual(row["teaching_hours"], Decimal("5"))
        self.assertEqual(row["monitor"]["condition"], "UNCONFIGURED")
        self.assertIsNone(row["monitor"]["contributions"][0]["load"])

    def test_unused_missing_weight_does_not_hide_a_calculable_load(self):
        WorkloadPolicy.objects.create(academic_term=self.term, department=self.department,
            recommended_load=1, maximum_load=6, lecture_weight=1)
        self.offering.laboratory_units = 0
        self.offering.save()
        self.save_assignment()
        row = self.row(self.monitor())
        self.assertEqual(row["assigned_load"], Decimal("2"))
        self.assertEqual(row["monitor"]["condition"], "WITHIN_LOAD")

    def test_known_maximum_missing_target_retains_supported_backend_capacity_state(self):
        self.policy(maximum_load=6)
        self.save_assignment()
        row = self.row(self.monitor())
        self.assertIsNone(row["policy"]["recommended_load"])
        self.assertEqual(row["status"], "WITHIN_LOAD")
        self.assertEqual(row["monitor"]["condition"], "WITHIN_LOAD")
        self.assertEqual(row["remaining_capacity"], Decimal("2.5"))

    def test_explicit_zero_maximum_is_not_missing_or_unlimited(self):
        self.policy(recommended_load=0, maximum_load=0)
        empty = self.row(self.monitor())
        self.assertEqual(empty["assigned_load"], Decimal("0"))
        self.assertEqual(empty["monitor"]["condition"], "AT_CAPACITY")
        self.save_assignment()
        loaded = self.row(self.monitor())
        self.assertEqual(loaded["monitor"]["condition"], "OVERLOAD")
        self.assertEqual(loaded["remaining_capacity"], Decimal("-3.5"))

    def test_explicit_zero_weights_remain_calculated_values(self):
        WorkloadPolicy.objects.create(academic_term=self.term, department=self.department,
            recommended_load=0, maximum_load=6, lecture_weight=0, laboratory_weight=0)
        self.save_assignment()
        row = self.row(self.monitor())
        self.assertEqual(row["assigned_load"], Decimal("0"))
        self.assertEqual(row["monitor"]["condition"], "WITHIN_LOAD")
        self.assertEqual(row["monitor"]["contributions"][0]["load"], Decimal("0"))
        self.assertTrue(row["monitor"]["meter"]["available"])

    def test_invalid_inherited_limits_show_policy_review_instead_of_capacity_approval(self):
        self.policy(recommended_load=6, maximum_load=9)
        self.faculty.maximum_load = 3
        self.faculty.save()
        row = self.row(self.monitor())
        self.assertEqual(row["monitor"]["condition"], "UNCONFIGURED")
        self.assertTrue(row["warnings"])
        self.assertIsNone(row["remaining_capacity"])

    def test_faculty_without_assignments_and_designation_are_informational(self):
        self.policy(recommended_load=3, maximum_load=6)
        self.faculty.designation = Designation.objects.create(name="Monitor chair", units_released=3)
        self.faculty.save()
        row = self.row(self.monitor())
        self.assertEqual(row["assignment_count"], 0)
        self.assertEqual(row["monitor"]["contributions"], [])
        self.assertEqual(row["assigned_load"], Decimal("0"))
        self.assertEqual(row["policy"]["recommended_load"], 3)
        self.assertEqual(row["policy"]["maximum_load"], 6)

    def test_independent_policy_sources_and_term_override_remain_authoritative(self):
        WorkloadPolicy.objects.create(academic_term=self.term, lecture_weight=1,
            laboratory_weight=2, recommended_load=3, maximum_load=8)
        WorkloadPolicy.objects.create(academic_term=self.term, college=self.college, maximum_load=7)
        self.policy(maximum_load=6)
        self.faculty.recommended_load = 2
        self.faculty.save()
        FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term, maximum_load=5)
        self.save_assignment()
        row = self.row(self.monitor())
        self.assertEqual(row["assigned_load"], Decimal("3.5"))
        self.assertEqual(row["policy"]["sources"]["maximum_load"], "Faculty term override")
        self.assertEqual(row["policy"]["sources"]["recommended_load"], "Faculty baseline")
        self.assertEqual(row["policy"]["sources"]["laboratory_weight"], "Department workload policy")
        self.assertEqual(row["remaining_capacity"], Decimal("1.5"))

    def test_presentation_does_not_modify_calculator_fields_or_enforcement(self):
        self.policy(recommended_load=3)
        self.save_assignment()
        report = calculate_workload(self.faculty, self.term)
        before = {key: value for key, value in report.items()}
        decorated = decorate_report(report)
        for key, value in before.items():
            self.assertEqual(decorated[key], value, key)
        self.assertNotIn("monitor", report)
        self.assertEqual(calculate_workload(self.faculty, self.term)["status"], "OVERLOAD")

    def test_administrator_and_staff_population_aggregation_follow_organizational_scope(self):
        cases = ((self.admin, set(self.records)), (self.dean, {self.department.pk, self.sibling.pk}),
                 (self.chair, {self.department.pk}), (self.staff, {self.department.pk}))
        for actor, departments in cases:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.monitor()
                self.assertEqual(response.status_code, 200)
                self.assertEqual({row["faculty"].home_department_id for row in response.context["page_obj"]}, departments)
                self.assertEqual(sum(self.counts(response).values()), len(departments))

    def test_faculty_and_revoked_staff_cannot_read_administrative_monitor(self):
        faculty_user = get_user_model().objects.create_user(username="monitor-faculty", password="test-only-password")
        self.faculty.user = faculty_user
        self.faculty.save()
        AdminProfile.objects.create(user=faculty_user, role=AdminProfile.Role.FACULTY)
        for actor in (faculty_user, self.staff):
            if actor == self.staff:
                actor.admin_profile.is_enabled = False
                actor.admin_profile.save()
            self.client.force_login(actor)
            self.assertEqual(self.monitor().status_code, 403)

    def test_selected_faculty_id_is_strictly_scoped_and_validated(self):
        sibling = self.records[self.sibling.pk]["faculty-management"]
        external = self.records[self.external.pk]["faculty-management"]
        for identifier in (sibling.pk, external.pk, "invalid", -1):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.monitor(selected_faculty=identifier).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.monitor(department=self.department.pk, selected_faculty=sibling.pk).status_code, 404)

    def test_explicit_selection_cannot_bypass_active_roster_filters(self):
        self.policy(recommended_load=3, maximum_load=6)
        self.assertEqual(self.monitor(selected_faculty=self.faculty.pk, q="not-a-matching-name").status_code, 404)
        self.assertEqual(self.monitor(selected_faculty=self.faculty.pk, condition="OVERLOAD").status_code, 404)

    def test_invalid_filters_show_errors_and_no_results_or_summary(self):
        for query, field in (({"department": self.external.pk}, "department"),
                             ({"condition": "APPROVED"}, "condition"),
                             ({"employment_category": "invalid"}, "employment_category"),
                             ({"workload_status": "APPROVED"}, "workload_status")):
            with self.subTest(query=query):
                response = self.monitor(**query)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["filter_form"].errors)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)
                self.assertEqual(sum(self.counts(response).values()), 0)
        self.assertEqual(self.monitor(academic_term="invalid").status_code, 400)
        self.assertEqual(self.monitor(academic_term=-1).status_code, 404)

    def test_search_category_and_condition_filters_do_not_recompute_context_counts(self):
        self.policy(recommended_load=2, maximum_load=6)
        part_time = EmploymentCategory.objects.create(code="monitor-part-time", name="Monitor part time")
        other = self.extra_faculty("MON-FILTER", maximum_load=0, recommended_load=0)
        other.employment_category = part_time
        other.save()
        for query in ({"q": "Person1"}, {"q": "F1"}, {"employment_category": self.category.pk},
                      {"condition": "UNDERLOAD"}):
            with self.subTest(query=query):
                response = self.monitor(**query)
                self.assertEqual(response.context["context_count"], 2)
                self.assertEqual(self.counts(response)["UNDERLOAD"], 1)
                self.assertEqual(self.counts(response)["AT_CAPACITY"], 1)
                self.assertEqual(response.context["page_obj"].paginator.count, 1)
                self.assertEqual(self.row(response)["faculty"].pk, self.faculty.pk)
        self.assertEqual(self.monitor(q="no-matching-faculty").context["page_obj"].paginator.count, 0)

    def test_term_filter_uses_only_assignments_and_policies_from_that_term(self):
        self.policy(recommended_load=3, maximum_load=6)
        self.save_assignment()
        WorkloadPolicy.objects.create(academic_term=self.later, department=self.department,
            recommended_load=0, maximum_load=0, lecture_weight=1, laboratory_weight=1)
        row = self.row(self.monitor(academic_term=self.later.pk))
        self.assertEqual(row["assigned_load"], Decimal("0"))
        self.assertEqual(row["assignment_count"], 0)
        self.assertEqual(row["monitor"]["condition"], "AT_CAPACITY")

    def test_contributions_use_real_section_relationship(self):
        self.policy(recommended_load=3, maximum_load=6)
        section = ClassSection.objects.create(academic_term=self.term, department=self.department,
            code="MON-1A", year_level=1)
        OfferingRequirement.objects.create(section=section, subject_offering=self.offering)
        self.save_assignment(share="0.5")
        row = self.row(self.monitor(selected_faculty=self.faculty.pk))
        self.assertEqual(row["monitor"]["contributions"][0]["section_code"], "MON-1A")

    def test_monitor_requests_are_read_only_and_post_is_not_a_write_endpoint(self):
        self.policy(recommended_load=3, maximum_load=6)
        assignment, _ = self.save_assignment(share="0.5")
        before_events = AuditLog.objects.count()
        response = self.monitor(selected_faculty=self.faculty.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(reverse("workloads:monitor"), self.assignment_data()).status_code, 405)
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.share, Decimal("0.5"))
        self.assertEqual(AuditLog.objects.count(), before_events)

    def test_assignment_and_report_links_retain_real_scheduling_context(self):
        schedule = Schedule.objects.create(name="Monitor schedule", academic_term=self.term, department=self.department)
        response = self.monitor(department=self.department.pk, schedule=schedule.pk)
        assignment_link = response.context["assignment_desk_url"]
        self.assertEqual(urlsplit(assignment_link).path, reverse("workloads:section-assignments"))
        query = parse_qs(urlsplit(assignment_link).query)
        for name, value in (("academic_term", self.term.pk), ("department", self.department.pk), ("schedule", schedule.pk)):
            self.assertEqual(query[name], [str(value)])
        report_query = parse_qs(urlsplit(response.context["workload_report_url"]).query)
        self.assertEqual(report_query["term"], [str(self.term.pk)])
        self.assertEqual(report_query["department"], [str(self.department.pk)])
        later_schedule = Schedule.objects.create(name="Wrong monitor term", academic_term=self.later, department=self.department)
        self.assertEqual(self.monitor(schedule=later_schedule.pk).status_code, 404)

    def test_guided_navigation_preserves_validated_schedule_and_stage_context(self):
        schedule = Schedule.objects.create(name="Guided monitor", academic_term=self.term, department=self.department)
        response = self.monitor(department=self.department.pk, workflow_schedule=schedule.pk, workflow_stage=3)
        self.assertEqual(response.status_code, 200)
        query = parse_qs(urlsplit(response.context["assignment_desk_url"]).query)
        self.assertEqual(query["workflow_schedule"], [str(schedule.pk)])
        self.assertIn("workflow_stage", query)
        self.assertEqual(self.monitor(department=self.sibling.pk, workflow_schedule=schedule.pk,
            workflow_stage=3).status_code, 404)

    def test_pagination_and_query_volume_do_not_add_one_query_per_faculty_detail(self):
        self.policy(recommended_load=3, maximum_load=6)
        # Keep authentication caches equally warm in both measured requests.
        self.monitor()
        with CaptureQueriesContext(connection) as small:
            self.monitor()
        for index in range(22):
            self.extra_faculty(f"MON-PAGE-{index:02d}")
        with CaptureQueriesContext(connection) as large:
            response = self.monitor()
        self.assertEqual(response.context["page_obj"].paginator.count, 23)
        self.assertEqual(len(response.context["page_obj"]), 20)
        self.assertEqual(response.context["context_count"], 23)
        self.assertLessEqual(len(large), len(small) + 3)
        page_two = self.monitor(page=2)
        self.assertEqual(len(page_two.context["page_obj"]), 3)
        self.assertEqual(sum(self.counts(page_two).values()), 23)

    def test_explicit_selected_faculty_opens_the_page_containing_its_roster_entry(self):
        self.policy(recommended_load=3, maximum_load=6)
        for index in range(22):
            self.extra_faculty(f"MON-SELECT-{index:02d}")
        person = Faculty.objects.create(employee_id="MON-LAST", first_name="Last", last_name="Zzz",
            home_department=self.department, employment_category=self.category)
        response = self.monitor(selected_faculty=person.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_report"]["faculty"].pk, person.pk)
        self.assertIn(person.pk, [row["faculty"].pk for row in response.context["page_obj"]])
        back = parse_qs(urlsplit(response.context["monitor_back_url"]).query)
        self.assertEqual(back["page"], [str(response.context["page_obj"].number)])

"""Guided navigation uses real scheduling data and retains scoped context."""
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from django.urls import reverse

from .models import Schedule, ScheduleGenerationRun
from .test_connected_workspace import Links
from .tests import TimetableFixture


class GuidedWorkspaceTests(TimetableFixture):
    def context_query(self, stage=2):
        return {"academic_term": self.term.pk, "department": self.department.pk,
                "schedule": self.schedule.pk, "stage": stage}

    def flow_query(self, stage=2):
        return {"academic_term": self.term.pk, "department": self.department.pk,
                "workflow_schedule": self.schedule.pk, "workflow_stage": stage}

    def test_seven_stages_and_current_stage_for_admin_and_staff(self):
        for user in (self.admin, self.staff, self.college_staff):
            self.client.force_login(user)
            page = self.client.get(reverse("timetabling:prepare"), self.context_query(4))
            self.assertEqual(page.status_code, 200)
            self.assertEqual(len(page.context["stages"]), 7)
            self.assertEqual(page.context["current_stage"]["number"], 4)
            self.assertContains(page, 'aria-current="step"')
            self.assertContains(page, "Back: Review teaching units")
            self.assertContains(page, "Continue: Generate timetable")

    def test_department_selection_filters_versions(self):
        other = Schedule.objects.create(name="Sibling version", department=self.sibling, academic_term=self.term)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("timetabling:prepare"), {
            "academic_term": self.term.pk, "department": self.department.pk})
        self.assertNotIn(other, page.context["schedules"])
        self.assertEqual(page.context["department"], self.department)

    def test_guided_forms_keep_related_records_in_selected_context(self):
        from workloads.models import SubjectOffering
        self.client.force_login(self.college_staff)
        for route, field, other in (
                ("workloads:offerings-add", "subject", self.records[self.sibling.pk]["subjects"]),
                ("workloads:assignments-add", "subject_offering", self.offerings[self.sibling.pk]),
                ("timetabling:requirements-add", "subject_offering", self.offerings[self.sibling.pk])):
            page = self.client.get(reverse(route), self.flow_query())
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(other, page.context["form"].fields[field].queryset)
        before = SubjectOffering.objects.count()
        response = self.client.post(reverse("workloads:offerings-add") + "?" + urlencode(self.flow_query()),
            self.offering_data(subject=self.records[self.sibling.pk]["subjects"].pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn("subject", response.context["form"].errors)
        self.assertEqual(SubjectOffering.objects.count(), before)

    def test_guided_rooms_show_shared_rooms_and_allow_ownership_filters(self):
        from scheduling.models import Room
        shared = Room.objects.create(code="GUIDE-SHARED", name="Shared college room",
            capacity=50, owner_college=self.college, category=self.room_type)
        self.client.force_login(self.college_staff)
        page = self.client.get(reverse("timetabling:prepare"), self.context_query(4))
        url = next(action["url"] for action in page.context["task_actions"] if action["label"] == "Review rooms")
        self.assertNotIn("department", parse_qs(urlsplit(url).query))
        rooms = self.client.get(url)
        self.assertContains(rooms, shared.name)
        filtered = self.client.get(reverse("rooms:list"), {**self.flow_query(4), "department": self.sibling.pk})
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, self.records[self.sibling.pk]["rooms"].name)

    def test_invalid_context_is_rejected(self):
        self.client.force_login(self.staff)
        for changes in ({"department": self.external.pk}, {"department": "bad"},
                        {"schedule": "bad"}, {"academic_term": self.later.pk}, {"stage": "bad"}, {"stage": 8}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.get(reverse("timetabling:prepare"),
                    {**self.context_query(), **changes}).status_code, 404)

    def test_backend_readiness_has_contextual_fix_links_and_no_writes(self):
        self.client.force_login(self.staff)
        before = ScheduleGenerationRun.objects.count()
        page = self.client.get(reverse("timetabling:prepare"), self.context_query(4))
        codes = {row["code"] for row in page.context["readiness_findings"]}
        self.assertIn("CONFIGURATION_MISSING", codes)
        self.assertIn("MEETING_REQUIREMENT_MISSING", codes)
        self.assertContains(page, "Review meeting requirements")
        self.assertEqual(ScheduleGenerationRun.objects.count(), before)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "draft")
        missing = next(row for row in page.context["readiness_findings"] if row["code"] == "MEETING_REQUIREMENT_MISSING")
        self.assertEqual(len(missing["targets"]), 2)
        editor = self.client.get(missing["targets"][0]["url"])
        self.assertEqual(editor.status_code, 200)
        self.assertEqual(editor.context["form"].initial["assignment"], self.assignment.pk)

    def test_schedule_creation_preselects_valid_department(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("timetabling:schedules-add"), {
            "academic_term": self.term.pk, "department": self.department.pk})
        self.assertEqual(response.context["form"].initial["department"], self.department.pk)

    def test_section_generation_navigation_preselects_a_valid_year(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:prepare"), self.context_query(4))
        url = next(url for url in Links(page).urls if urlsplit(url).path == reverse("timetabling:section-generator"))
        section_page = self.client.get(url)
        self.assertEqual(section_page.status_code, 200)
        self.assertNotIn("year_level", section_page.context["form"].errors)

    def test_proposal_history_pagination_keeps_context_without_duplicate_page(self):
        ScheduleGenerationRun.objects.bulk_create([ScheduleGenerationRun(schedule=self.schedule,
            academic_term=self.term, department=self.department, requested_by=self.staff,
            strategy="FILL_GAPS", status="INPUT_INVALID") for _ in range(21)])
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:generation-runs"), {**self.flow_query(6), "page": 2})
        previous = next(url for url in Links(page).urls if "page=1" in url)
        self.assertEqual(parse_qs(urlsplit(previous).query)["page"], ["1"])
        self.assertEqual(self.client.get(urljoin(reverse("timetabling:generation-runs"), previous)).context["page_obj"].number, 1)

    def test_generation_and_publication_retain_context_and_advance_stage(self):
        from datetime import time
        from .models import AssignmentMeetingRequirement, SchedulingConfiguration, ActiveSchedule
        self.policy(recommended_load=12, maximum_load=18)
        SchedulingConfiguration.objects.create(academic_term=self.term, department=self.department,
            allowed_weekdays=[1, 2, 3, 4, 5], earliest_start=time(8), latest_end=time(17),
            slot_increment_minutes=30, solver_time_limit_seconds=5, random_seed=17, worker_count=1)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,
            meeting_type="lecture", meetings_per_week=2, duration_minutes=60)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,
            meeting_type="laboratory", meetings_per_week=1, duration_minutes=180)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("timetabling:generator") + "?" + urlencode(self.flow_query(5)),
            {"schedule": self.schedule.pk, "strategy": "FILL_GAPS"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["workflow_stage"], ["6"])
        proposal = self.client.get(response.url)
        self.assertContains(proposal, "Accept proposal")
        # Form actions are checked directly because Links intentionally includes only anchors.
        from html.parser import HTMLParser
        actions = []
        class Forms(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == "form":
                    actions.extend(value for name, value in attrs if name == "action")
        Forms().feed(proposal.content.decode())
        accept_url = next(url for url in actions if "/accept/" in url)
        accepted = self.client.post(accept_url, follow=True)
        self.assertEqual(accepted.status_code, 200)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.entries.count(), 3)
        self.assertEqual(self.schedule.status, "draft")
        checked = self.client.post(reverse("timetabling:validate", args=[self.schedule.pk]) +
            "?" + urlencode(self.flow_query(7)), follow=True)
        self.assertEqual(checked.status_code, 200)
        self.schedule.refresh_from_db()
        review = self.client.get(reverse("timetabling:schedule-review", args=[self.schedule.pk]), self.flow_query(7))
        warnings = review.context["warning_codes"]
        finalized = self.client.post(reverse("timetabling:schedule-finalize", args=[self.schedule.pk]) +
            "?" + urlencode(self.flow_query(7)), {"revision_token": self.schedule.revision_token,
                                                "acknowledged_warnings": list(warnings)}, follow=True)
        self.assertEqual(finalized.status_code, 200)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "approved")
        self.assertTrue(ActiveSchedule.objects.filter(schedule=self.schedule).exists())
        self.assertContains(finalized, "Return to scheduling workspace")

    def test_task_links_and_filter_forms_keep_context(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:prepare"), self.context_query())
        url = next(url for url in Links(page).urls if urlsplit(url).path == reverse("workloads:section-assignments"))
        self.assertEqual(parse_qs(urlsplit(url).query)["workflow_schedule"], [str(self.schedule.pk)])
        task = self.client.get(url)
        self.assertEqual(task.status_code, 200)
        self.assertContains(task, "Return to scheduling workspace")
        self.assertContains(task, 'name="workflow_schedule"')

    def test_save_redirect_retains_workflow(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("timetabling:sections-add") + "?" +
            urlencode(self.flow_query()), {
                "academic_term": self.term.pk, "department": self.department.pk,
                "code": "NEW", "year_level": 1, "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["workflow_schedule"], [str(self.schedule.pk)])

    def test_workflow_context_is_validated_before_task_post(self):
        self.client.force_login(self.staff)
        outside = Schedule.objects.create(name="External version", department=self.external, academic_term=self.term)
        response = self.client.post(reverse("timetabling:sections-add") +
            f"?workflow_schedule={outside.pk}&workflow_stage=2", {})
        self.assertEqual(response.status_code, 404)

    def test_malformed_assignment_form_context_is_rejected(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("workloads:assignments-add"), {
            **self.flow_query(), "subject_offering": "bad"}).status_code, 404)

    def test_faculty_cannot_enter_workspace_or_spoof_workflow_context(self):
        from django.contrib.auth import get_user_model
        from accounts.models import AdminProfile
        user = get_user_model().objects.create_user(username="guided-faculty")
        self.faculty.user = user
        self.faculty.save()
        AdminProfile.objects.create(user=user, role="faculty")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("timetabling:prepare")).status_code, 403)
        self.assertEqual(self.client.get(reverse("workloads:section-assignments"), self.flow_query()).status_code, 403)

    def test_published_version_has_review_action_without_generation(self):
        Schedule.objects.filter(pk=self.schedule.pk).update(status="approved")
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:prepare"), self.context_query(5))
        self.assertNotContains(page, "Generate department timetable</a>")
        self.assertContains(page, "published")
        page = self.client.get(reverse("timetabling:prepare"), self.context_query(7))
        self.assertContains(page, "Review &amp; finalize")

"""Room explanations must agree with generation and preserve scoped navigation."""
from datetime import time
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import Permission
from django.urls import reverse
from django.test import override_settings

from resources.models import RoomType, Building
from scheduling.models import Room
from .models import AssignmentMeetingRequirement, SchedulingConfiguration, ScheduleGenerationRun
from .tests import TimetableFixture


class RoomEligibilityTests(TimetableFixture):
    def preview(self, user=None, **query):
        self.client.force_login(user or self.staff)
        return self.client.get(reverse("timetabling:room-eligibility"), {
            "academic_term": self.term.pk, "department": self.department.pk,
            "subject_offering": self.offering.pk, **query})

    def test_preview_is_read_only_and_explains_capacity_and_availability(self):
        before = (ScheduleGenerationRun.objects.count(), self.schedule.revision_token)
        page = self.preview()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Suitable with warnings")
        self.assertContains(page, "45 students")
        self.assertContains(page, "Not checked")
        self.assertContains(page, "lecture and laboratory")
        self.schedule.refresh_from_db()
        self.assertEqual(before, (ScheduleGenerationRun.objects.count(), self.schedule.revision_token))
        self.assertEqual(self.schedule.status, "draft")

    def test_mandatory_type_and_capacity_explain_both_failures(self):
        lab = RoomType.objects.create(code="high-spec", name="High-spec computer laboratory")
        self.requirement.room_type = lab
        self.requirement.room_type_mandatory = True
        self.requirement.capacity_is_hard = True
        self.requirement.save()
        page = self.preview()
        self.assertContains(page, "No suitable rooms")
        self.assertContains(page, lab.name)
        self.assertContains(page, "Mandatory")
        row = next(row for row in page.context["preview"]["rooms"] if row["room"].pk == self.room.pk)
        self.assertFalse(row["eligible"])
        self.assertEqual(len(row["failures"]), 2)
        self.assertNotContains(page, self.records[self.external.pk]["rooms"].name)

    def test_preferred_type_keeps_nonmatching_room_eligible(self):
        self.requirement.room_type = RoomType.objects.create(code="network", name="Networking laboratory")
        self.requirement.save()
        page = self.preview()
        self.assertContains(page, "Preferred")
        row = next(row for row in page.context["preview"]["rooms"] if row["room"].pk == self.room.pk)
        self.assertTrue(row["eligible"])
        self.assertTrue(row["warnings"])

    def test_catalog_fallback_and_explicit_override(self):
        from academics.models import Subject
        Subject.objects.filter(pk=self.offering.subject_id).update(required_room_type="unknown-type")
        page = self.preview()
        self.assertContains(page, "unknown-type")
        self.assertContains(page, "No suitable rooms")
        self.requirement.room_type = self.room_type
        self.requirement.save()
        page = self.preview()
        self.assertTrue(page.context["preview"]["eligible_count"])
        self.assertContains(page, "Preferred")

    def test_inactive_room_type_and_building_are_explained(self):
        building = Building.objects.create(code="CLOSED", name="Closed building", is_active=False)
        Room.objects.filter(pk=self.room.pk).update(building=building)
        page = self.preview()
        self.assertContains(page, "Building is inactive")
        Room.objects.filter(pk=self.room.pk).update(building=None, is_active=False)
        self.assertContains(self.preview(), "Room is inactive")

    def test_missing_requirement_and_unknown_class_size(self):
        self.section.expected_size = None
        self.section.save()
        self.assertContains(self.preview(), "Class size not recorded")
        self.requirement.delete()
        self.assertContains(self.preview(), "Link this class to a section")

    def test_forged_offering_and_context_are_rejected(self):
        page = self.preview(subject_offering=self.offerings[self.external.pk].pk)
        self.assertIsNone(page.context["preview"])
        self.assertContains(page, "Select a valid choice")
        page = self.preview(workflow_schedule=self.schedule.pk, workflow_stage=4,
                            academic_term=self.later.pk)
        self.assertEqual(page.status_code, 404)

    @override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"])
    def test_preview_needs_view_permissions_but_not_generation_permissions(self):
        from .room_eligibility import PREVIEW_PERMISSIONS
        user = self.make_user("room-reader", "staff", department=self.department)
        user.groups.clear()
        user.user_permissions.clear()
        for permission in PREVIEW_PERMISSIONS:
            app, code = permission.split(".")
            user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        self.assertEqual(self.preview(user=user).status_code, 200)
        self.requirement.capacity_is_hard = True
        self.requirement.save()
        page = self.preview(user=user)
        self.assertContains(page, "Ask an authorized room manager")
        self.assertNotContains(page, "Edit this class requirement")
        user.user_permissions.remove(Permission.objects.get(codename="view_room"))
        self.assertEqual(self.preview(user=user).status_code, 403)

    def test_stage_four_and_returns_preserve_workflow_context(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:prepare"), {
            "academic_term": self.term.pk, "department": self.department.pk,
            "schedule": self.schedule.pk, "stage": 4})
        url = next(action["url"] for action in page.context["task_actions"]
                   if "room-eligibility" in action["url"])
        self.assertEqual(parse_qs(urlsplit(url).query)["workflow_stage"], ["4"])
        self.assertContains(self.client.get(url), "Return to scheduling workspace")
        page = self.preview(workflow_schedule=self.schedule.pk, workflow_stage=4)
        self.assertContains(page, 'name="workflow_schedule"')
        self.assertContains(page, "This comparison is read-only; no changes need to be saved here")
        self.assertNotContains(page, "Save changes on this page before continuing")

    def test_preview_eligible_rooms_agree_with_generator_candidates(self):
        from .generation_inputs import prepare_generation_input, GenerationOverrides
        SchedulingConfiguration.objects.create(academic_term=self.term, department=self.department,
            allowed_weekdays=[1, 2], earliest_start=time(8), latest_end=time(17),
            slot_increment_minutes=30, solver_time_limit_seconds=5, random_seed=17, worker_count=1)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,
            meeting_type="lecture", meetings_per_week=2, duration_minutes=60)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,
            meeting_type="laboratory", meetings_per_week=1, duration_minutes=180)
        self.policy(recommended_load=12, maximum_load=18)
        lab = RoomType.objects.create(code="special-lab", name="Specialized laboratory")
        for code, category, capacity in (("LAB45", lab, 45), ("LAB44", lab, 44), ("LEC45", self.room_type, 45)):
            Room.objects.create(name=code, code=code, category=category, capacity=capacity,
                                owner_department=self.department)
        for mandatory, hard_capacity in ((False, False), (True, False), (False, True), (True, True)):
            self.requirement.room_type = lab
            self.requirement.room_type_mandatory = mandatory
            self.requirement.capacity_is_hard = hard_capacity
            self.requirement.save()
            prepared = prepare_generation_input(user=self.staff, schedule_id=self.schedule.pk,
                strategy="FILL_GAPS", overrides=GenerationOverrides())
            candidates = {item.room_id for item in prepared.solver_input.candidates
                          if item.assignment_id == self.assignment.pk} if prepared.solver_input else set()
            eligible = {row["room"].pk for row in self.preview().context["preview"]["rooms"] if row["eligible"]}
            self.assertEqual(eligible, candidates)

    def test_occupied_and_blocked_room_remains_suitable_without_time_selection(self):
        from .models import RoomUnavailability
        self.candidate().save()
        RoomUnavailability.objects.create(academic_term=self.term, room=self.room,
            day_of_week=1, start_time=time(8), end_time=time(17))
        page = self.preview()
        self.assertTrue(page.context["preview"]["rooms"][0]["eligible"])
        self.assertContains(page, "Not checked")
        self.assertEqual(self.schedule.entries.count(), 1)

    def test_inactive_preferred_requirement_blocks_generation_readiness(self):
        RoomType.objects.filter(pk=self.room_type.pk).update(is_active=False)
        self.requirement.room_type = self.room_type
        self.requirement.save()
        page = self.preview()
        self.assertContains(page, "selected room specialization is inactive")
        self.assertFalse(page.context["preview"]["eligible_count"])

    def test_preview_get_only_and_empty_selection(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("timetabling:room-eligibility"))
        self.assertContains(page, "Select a class to compare rooms")
        self.assertEqual(self.client.post(reverse("timetabling:room-eligibility"), {}).status_code, 405)

    def test_pagination_and_room_links_preserve_selected_term(self):
        for index in range(22):
            Room.objects.create(name=f"Room preview {index}", code=f"RP{index}",
                capacity=50, category=self.room_type, owner_department=self.department)
        page = self.preview(workflow_schedule=self.schedule.pk, workflow_stage=4, page=2)
        self.assertEqual(page.context["page_obj"].number, 2)
        self.assertEqual(len(page.context["page_obj"]), 3)
        from .test_connected_workspace import Links
        room_link = next(url for url in Links(page).urls if urlsplit(url).path.startswith("/rooms/")
                         and urlsplit(url).path != reverse("rooms:list"))
        self.assertEqual(parse_qs(urlsplit(room_link).query)["academic_term"], [str(self.term.pk)])
        self.assertEqual(self.client.get(room_link).context["term"].pk, self.term.pk)

    def test_stage_four_requirement_editor_keeps_stage_and_uses_saved_data(self):
        page = self.preview(workflow_schedule=self.schedule.pk, workflow_stage=4)
        from .test_connected_workspace import Links
        edit_link = next(url for url in Links(page).urls if "/requirements/" in url and "/edit/" in url)
        self.assertEqual(parse_qs(urlsplit(edit_link).query)["workflow_stage"], ["4"])
        editor = self.client.get(edit_link)
        self.assertContains(editor, "Preview saved room requirements")
        self.assertContains(editor, "Blank inherits the subject catalog")

    def test_requirement_preview_link_outside_guide_uses_its_saved_term(self):
        self.client.force_login(self.staff)
        editor = self.client.get(reverse("timetabling:requirements-edit", args=[self.requirement.pk]))
        from .test_connected_workspace import Links
        url = next(url for url in Links(editor).urls if "room-eligibility" in url)
        self.assertEqual(parse_qs(urlsplit(url).query)["academic_term"], [str(self.term.pk)])
        self.assertEqual(self.client.get(url).context["preview"]["offering"].pk, self.offering.pk)

    def test_room_ownership_matches_existing_staff_scope(self):
        college_room = Room.objects.create(name="College room", code="COLLEGE",
            capacity=50, category=self.room_type, owner_college=self.college)
        chair_rows = self.preview().context["preview"]["rooms"]
        self.assertNotIn(college_room.pk, {row["room"].pk for row in chair_rows})
        dean_rows = self.preview(user=self.college_staff).context["preview"]["rooms"]
        self.assertIn(college_room.pk, {row["room"].pk for row in dean_rows})
        self.assertNotIn(self.records[self.external.pk]["rooms"].pk, {row["room"].pk for row in dean_rows})

    def test_unknown_catalog_type_never_matches_uncategorized_room(self):
        from academics.models import Subject
        Room.objects.filter(pk=self.room.pk).update(category=None, room_type="")
        Subject.objects.filter(pk=self.offering.subject_id).update(required_room_type="missing-lab")
        row = self.preview().context["preview"]["rooms"][0]
        self.assertFalse(row["eligible"])
        self.assertFalse(any("Matches mandatory" in reason for reason in row["reasons"]))

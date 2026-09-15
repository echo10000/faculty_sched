from datetime import time

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory, SimpleTestCase

from .models import (
    AssignmentMeetingRequirement,
    ScheduleEntry,
    ScheduleGenerationRun,
    SchedulingConfiguration,
)
from .tests import TimetableFixture


class GenerationSettingsTests(SimpleTestCase):
    def test_preprocessing_limits_are_positive(self):
        self.assertGreater(settings.SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS, 0)
        self.assertGreater(settings.SCHEDULER_MAX_CANDIDATES, 0)
        self.assertGreater(settings.SCHEDULER_MAX_SLOT_LITERALS, 0)


class GenerationModelTests(TimetableFixture):
    def configuration(self, **changes):
        values = {
            "academic_term": self.term,
            "department": self.department,
            "allowed_weekdays": [1, 2, 3, 4, 5],
            "earliest_start": time(8),
            "latest_end": time(17),
            "slot_increment_minutes": 30,
            "solver_time_limit_seconds": 10,
            "random_seed": 17,
            "worker_count": 1,
            "faculty_preference_weight": 1,
            "faculty_gap_weight": 1,
            "section_gap_weight": 1,
            "meeting_distribution_weight": 1,
            "room_fit_weight": 1,
        }
        values.update(changes)
        return SchedulingConfiguration(**values)

    def test_requirement_minutes_must_equal_assignment_component(self):
        requirement = AssignmentMeetingRequirement(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=1,
            duration_minutes=60,
        )
        with self.assertRaises(ValidationError):
            requirement.full_clean()

    def test_valid_requirement_matches_assignment_component_minutes(self):
        requirement = AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )
        self.assertEqual(requirement.assignment_id, self.assignment.pk)

    def test_incomplete_requirement_reports_validation_errors(self):
        requirement = AssignmentMeetingRequirement(
            assignment=self.assignment,
            meeting_type="lecture",
        )
        with self.assertRaises(ValidationError):
            requirement.full_clean()

    def test_configuration_rejects_bad_grid_and_bounds(self):
        config = SchedulingConfiguration(
            academic_term=self.term,
            department=self.department,
            allowed_weekdays=[1, 1, 8],
            earliest_start=time(8, 5),
            latest_end=time(17),
            slot_increment_minutes=30,
            solver_time_limit_seconds=301,
            random_seed=17,
            worker_count=65,
        )
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_configuration_normalizes_valid_weekdays(self):
        config = self.configuration(allowed_weekdays=[5, 1, 3])
        config.full_clean()
        self.assertEqual(config.allowed_weekdays, [1, 3, 5])

    def test_configuration_rejects_each_invalid_policy_bound(self):
        invalid_values = (
            {"allowed_weekdays": []},
            {"allowed_weekdays": [1, 1]},
            {"allowed_weekdays": [True, 2]},
            {"allowed_weekdays": [0, 2]},
            {"allowed_weekdays": [2, 8]},
            {"earliest_start": time(8, 0, 1)},
            {"latest_end": time(17, 0, 1)},
            {"earliest_start": time(17)},
            {"earliest_start": time(8, 5)},
            {"latest_end": time(17, 10)},
            {"latest_end": time(17, 15)},
            {"slot_increment_minutes": 0},
            {"solver_time_limit_seconds": 0},
            {"solver_time_limit_seconds": 301},
            {"worker_count": 0},
            {"worker_count": 65},
            {"faculty_preference_weight": -1},
            {"faculty_gap_weight": -1},
            {"section_gap_weight": -1},
            {"meeting_distribution_weight": -1},
            {"room_fit_weight": -1},
        )
        for changes in invalid_values:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.configuration(**changes).full_clean()

    def test_configuration_database_constraints_reject_invalid_bounds(self):
        config = self.configuration()
        config.save()
        for changes in (
            {"slot_increment_minutes": 0},
            {"solver_time_limit_seconds": 301},
            {"worker_count": 65},
            {"room_fit_weight": -1},
        ):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                SchedulingConfiguration.objects.filter(pk=config.pk).update(**changes)

    def test_schedule_entry_defaults_to_locked_manual_provenance(self):
        entry = ScheduleEntry.objects.create(
            schedule=self.schedule,
            assignment=self.assignment,
            room=self.room,
            day_of_week=1,
            start_time=time(9),
            end_time=time(10),
            meeting_type="lecture",
        )
        self.assertTrue(entry.is_locked)
        self.assertIsNone(entry.generation_run_id)

    def test_generation_run_defaults_and_choices(self):
        run = ScheduleGenerationRun.objects.create(
            schedule=self.schedule,
            academic_term=self.term,
            department=self.department,
            requested_by=self.chair,
            strategy=ScheduleGenerationRun.Strategy.FILL_GAPS,
        )
        self.assertEqual(run.status, ScheduleGenerationRun.Status.PENDING)
        self.assertEqual(run.solver_status, "")
        self.assertIsNotNone(run.requested_at)
        self.assertIsNone(run.started_at)
        self.assertIsNone(run.finished_at)
        self.assertIsNone(run.accepted_at)
        self.assertIsNone(run.discarded_at)
        self.assertIsNone(run.accepted_by_id)
        self.assertEqual(run.configuration_snapshot, {})
        self.assertEqual(run.input_summary, {})
        self.assertEqual(run.proposed_meetings, [])
        self.assertEqual(run.diagnostics, [])
        self.assertEqual(run.solver_statistics, {})
        self.assertEqual(run.proposed_meeting_count, 0)
        self.assertEqual(run.accepted_meeting_count, 0)

    def test_dean_and_chair_generate_but_staff_needs_explicit_grant(self):
        self.assertTrue(self.dean.has_perm("timetabling.generate_schedule"))
        self.assertTrue(self.chair.has_perm("timetabling.generate_schedule"))
        self.assertFalse(self.staff.has_perm("timetabling.generate_schedule"))
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="timetabling",
                codename="generate_schedule",
            )
        )
        refreshed_staff = type(self.staff).objects.get(pk=self.staff.pk)
        self.assertTrue(refreshed_staff.has_perm("timetabling.generate_schedule"))

    def test_bootstrap_roles_adds_generation_grant_to_existing_role(self):
        dean_group = Group.objects.create(name="College Dean")
        call_command("bootstrap_roles", verbosity=0)
        self.assertTrue(
            dean_group.permissions.filter(
                content_type__app_label="timetabling",
                codename="generate_schedule",
            ).exists()
        )

    def test_generation_run_admin_is_read_only(self):
        model_admin = admin.site._registry[ScheduleGenerationRun]
        request = RequestFactory().get("/admin/")
        request.user = self.admin
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        concrete_fields = {field.name for field in ScheduleGenerationRun._meta.concrete_fields}
        self.assertEqual(set(model_admin.get_readonly_fields(request)), concrete_fields)

    def test_configuration_and_requirement_admin_lock_identity_on_edit(self):
        request = RequestFactory().get("/admin/")
        request.user = self.admin
        requirement_admin = admin.site._registry[AssignmentMeetingRequirement]
        configuration_admin = admin.site._registry[SchedulingConfiguration]
        requirement = AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )
        config = self.configuration()
        config.save()
        self.assertIn("assignment", requirement_admin.get_readonly_fields(request, requirement))
        self.assertIn("meeting_type", requirement_admin.get_readonly_fields(request, requirement))
        self.assertIn("academic_term", configuration_admin.get_readonly_fields(request, config))
        self.assertIn("department", configuration_admin.get_readonly_fields(request, config))

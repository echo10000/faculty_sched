from unittest.mock import patch
from datetime import time

from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from .generation_inputs import GenerationOverrides, GENERATION_PERMISSIONS
from .generation import InvalidRunTransition
from .models import Schedule, ScheduleGenerationRun, SchedulingConfiguration
from .tests import TimetableFixture


class GenerationPageTests(TimetableFixture):
    def setUp(self):
        self.client.force_login(self.chair)
        SchedulingConfiguration.objects.create(
            academic_term=self.term, department=self.department,
            allowed_weekdays=[1, 2, 3, 4, 5], earliest_start=time(8),
            latest_end=time(17), slot_increment_minutes=30,
            solver_time_limit_seconds=10, random_seed=17, worker_count=1,
        )
        self.foreign_schedule = Schedule.objects.create(
            academic_term=self.term, department=self.external, name="Protected timetable",
        )
        self.run = ScheduleGenerationRun.objects.create(
            schedule=self.schedule, academic_term=self.term,
            department=self.department, requested_by=self.chair,
            strategy="FILL_GAPS", status="PROPOSAL_READY",
        )
        self.foreign_run = ScheduleGenerationRun.objects.create(
            schedule=self.foreign_schedule, academic_term=self.term,
            department=self.external, requested_by=self.admin,
            strategy="FILL_GAPS", status="PROPOSAL_READY",
        )

    def url(self, name, *args):
        return reverse(f"timetabling:{name}", args=args)

    def test_scoped_generator_history_and_detail(self):
        self.assertContains(self.client.get(self.url("generator")), self.schedule.name)
        self.assertNotContains(self.client.get(self.url("generator")), self.foreign_schedule.name)
        self.assertContains(self.client.get(self.url("generation-runs")), self.schedule.name)
        self.assertNotContains(self.client.get(self.url("generation-runs")), self.foreign_schedule.name)
        self.assertEqual(self.client.get(self.url("generation-run-detail", self.foreign_run.pk)).status_code, 404)
        self.assertContains(self.client.get(self.url("generation-run-detail", self.run.pk)), "Proposal ready")

    def test_generator_post_delegates_to_service(self):
        with patch("timetabling.views.request_generation", return_value=self.run) as service:
            response = self.client.post(self.url("generator"), {
                "schedule": self.schedule.pk, "strategy": "FILL_GAPS",
                "solver_time_limit_seconds": "0",
            })
            self.assertEqual(response.status_code, 200)
            service.assert_not_called()
            response = self.client.post(self.url("generator"), {
                "schedule": self.schedule.pk, "strategy": "FILL_GAPS",
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(service.call_args.kwargs["schedule_id"], self.schedule.pk)
            self.assertIsInstance(service.call_args.kwargs["overrides"], GenerationOverrides)

    def test_terminal_actions_require_post_csrf_and_scope(self):
        accept = self.url("generation-run-accept", self.run.pk)
        discard = self.url("generation-run-discard", self.run.pk)
        self.assertEqual(self.client.get(accept).status_code, 405)
        self.assertEqual(self.client.get(discard).status_code, 405)
        self.assertEqual(self.client.put(accept).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.chair)
        self.assertEqual(secure.post(accept).status_code, 403)
        self.assertEqual(self.client.post(self.url("generation-run-accept", self.foreign_run.pk)).status_code, 404)

    def test_anonymous_redirects_and_staff_has_generator(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url("generator")).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url("generator")).status_code, 200)

    def test_staff_replace_generation_produces_draft_only(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url('generator')).status_code, 200)
        self.assertTrue(self.staff.has_perm('timetabling.delete_scheduleentry'))
        response = self.client.post(self.url('generator'), {'schedule': self.schedule.pk, 'strategy': 'REPLACE_UNLOCKED'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Schedule.objects.filter(pk=self.schedule.pk, status='approved').exists())

    def test_exact_generation_bundle_can_review_own_proposal_without_history_permission(self):
        self.staff.user_permissions.add(*(Permission.objects.get(
            content_type__app_label=code.split(".")[0], codename=code.split(".")[1],
        ) for code in GENERATION_PERMISSIONS))
        staff = type(self.staff).objects.get(pk=self.staff.pk)
        own_run = ScheduleGenerationRun.objects.create(
            schedule=self.schedule, academic_term=self.term,
            department=self.department, requested_by=staff,
            strategy="FILL_GAPS", status="PROPOSAL_READY",
        )
        self.client.force_login(staff)
        with patch("timetabling.views.request_generation", return_value=own_run):
            response = self.client.post(self.url("generator"), {
                "schedule": self.schedule.pk, "strategy": "FILL_GAPS",
            }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accept proposal")
        self.assertEqual(self.client.get(self.url("generation-runs")).status_code, 200)
        self.assertEqual(self.client.get(self.url("generation-run-detail", self.run.pk)).status_code, 200)
        self.assertContains(self.client.get(self.url("generator")), "Generated schedule history")
        self.assertContains(self.client.get(self.url("generation-run-detail", own_run.pk)),
                               "Generated schedule history")
        self.assertEqual(self.client.post(self.url("generation-run-discard", own_run.pk)).status_code, 302)
        own_run.refresh_from_db()
        self.assertEqual(own_run.status, "DISCARDED")

    def test_staff_default_access_includes_history_and_terminal_controls(self):
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="timetabling", codename="view_schedulegenerationrun",
        ), *Permission.objects.filter(
            content_type__app_label="academics", codename="view_academicterm",
        ))
        self.client.force_login(type(self.staff).objects.get(pk=self.staff.pk))
        self.assertEqual(self.client.get(self.url("generator")).status_code, 200)
        page = self.client.get(self.url("generation-run-detail", self.run.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Accept proposal")
        self.assertContains(page, "Discard proposal")
        home = self.client.get("/dashboard/")
        self.assertContains(home, "Generated schedule history")
        self.assertNotContains(home, "Automated generator")

    def test_terminal_transition_conflict_and_stale_feedback(self):
        with patch("timetabling.views.accept_generation", side_effect=InvalidRunTransition):
            response = self.client.post(self.url("generation-run-accept", self.run.pk))
            self.assertEqual(response.status_code, 409)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "PROPOSAL_READY")
        self.run.status = "STALE"
        with patch("timetabling.views.accept_generation", return_value=self.run):
            response = self.client.post(self.url("generation-run-accept", self.run.pk), follow=True)
            self.assertContains(response, "regenerate before accepting")
            self.assertNotContains(response, "Generated meetings were accepted")

    def test_detail_escapes_diagnostics_and_shows_zero_metrics(self):
        self.run.objective_value = 0
        self.run.best_bound = 0
        self.run.runtime_seconds = 0
        self.run.solver_status = "FEASIBLE"
        self.run.diagnostics = [{"severity": "ERROR", "code": "BLOCKER", "message": "<script>private</script>"}]
        self.run.save()
        page = self.client.get(self.url("generation-run-detail", self.run.pk))
        self.assertContains(page, "Objective: 0")
        self.assertContains(page, "Runtime: 0")
        self.assertContains(page, "optimality was not proven")
        self.assertNotContains(page, "<script>")
        self.assertContains(page, "&lt;script&gt;", html=False)

    def test_corrupt_historical_diagnostics_degrade_safely(self):
        self.run.diagnostics = None
        self.run.proposed_meetings = None
        with patch("timetabling.views._visible_run", return_value=self.run):
            page = self.client.get(self.url("generation-run-detail", self.run.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Proposal ready")
        self.run.diagnostics = {"severity": "ERROR", "message": "Bad JSON shape"}
        with patch("timetabling.views._visible_run", return_value=self.run):
            self.assertEqual(self.client.get(self.url("generation-run-detail", self.run.pk)).status_code, 200)

    def test_preview_and_history_include_complete_whitelisted_context(self):
        preview = self.client.get(self.url("generator"), {"schedule": self.schedule.pk})
        self.assertContains(preview, "Allowed weekdays")
        self.assertContains(preview, "Faculty preference weight")
        self.assertContains(preview, "Eligible rooms")
        self.assertContains(preview, "Protected peer occupancy")
        history = self.client.get(self.url("generation-runs"))
        self.assertContains(history, "Requested by")
        self.assertContains(history, self.department.name)
        self.assertContains(history, self.chair.username)

    def test_form_scope_and_configuration_weekday_round_trip(self):
        response = self.client.get(self.url("generator"), {"academic_term": self.term.pk})
        self.assertNotContains(response, self.foreign_schedule.name)
        bad = self.client.post(self.url("generator"), {
            "academic_term": "999999", "schedule": self.schedule.pk, "strategy": "FILL_GAPS",
        })
        self.assertEqual(bad.status_code, 200)
        self.assertTrue(bad.context["form"].errors)
        configuration = SchedulingConfiguration.objects.get(department=self.department)
        form_page = self.client.get(self.url("configurations-edit", configuration.pk))
        self.assertContains(form_page, "Allowed weekdays")
        payload = {
            "academic_term": self.term.pk, "department": self.department.pk,
            "allowed_weekdays": ["1", "3", "5"], "earliest_start": "08:00",
            "latest_end": "17:00", "slot_increment_minutes": "30",
            "solver_time_limit_seconds": "10", "random_seed": "17",
            "worker_count": "1", **{name: "0" for name in configuration.WEIGHT_FIELDS},
        }
        self.assertEqual(self.client.post(self.url("configurations-edit", configuration.pk), payload).status_code, 302)
        configuration.refresh_from_db()
        self.assertEqual(configuration.allowed_weekdays, [1, 3, 5])

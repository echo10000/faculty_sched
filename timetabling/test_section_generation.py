from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import AdminProfile
from academics.models import AcademicTerm
from core.models import College, Department, Program
from workloads.models import SubjectOffering
from .conflicts import get_schedule_conflicts
from .finalization_validation import publication_conflicts
from .forms import SectionGenerationForm
from .generation import accept_generation, request_generation
from .generation_inputs import GenerationOverrides
from .models import AssignmentMeetingRequirement, ClassSection, Schedule, ScheduleEntry, ScheduleGenerationRun, SchedulingConfiguration


class SectionGenerationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("section-admin", "", "test-only")
        with override_settings(DEBUG=True):
            call_command("seed_section_demo", actor=cls.admin.username, stdout=StringIO())
        cls.first = ClassSection.objects.get(code="BSIT-1A")
        cls.second = ClassSection.objects.get(code="BSIT-1B")
        cls.foreign = ClassSection.objects.get(code="BSCS-1A")
        cls.schedule = Schedule.objects.get(department=cls.first.department)
        cls.staff = get_user_model().objects.create_user("section-staff", password="test-only")
        AdminProfile.objects.create(user=cls.staff, role="staff", department=cls.first.department)
        # Keep the real solver bounded for these integration tests.
        SchedulingConfiguration.objects.update(solver_time_limit_seconds=2)

    def data(self, section=None):
        section = section or self.first
        return {"academic_term": section.academic_term_id, "department": section.department_id,
                "year_level": section.year_level, "section": section.pk,
                "schedule": self.schedule.pk, "strategy": "FILL_GAPS"}

    def generate(self, section=None, strategy="FILL_GAPS"):
        run = request_generation(user=self.admin, schedule_id=self.schedule.pk,
            section_id=(section or self.first).pk, strategy=strategy, overrides=GenerationOverrides())
        self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
        return run

    def test_demo_has_unscheduled_subjects_and_no_official_decisions(self):
        self.assertEqual(ClassSection.objects.filter(department__college__code="CAS").count(), 6)
        self.assertEqual(SubjectOffering.objects.filter(academic_term=self.first.academic_term).count(), 24)
        self.assertFalse(ScheduleEntry.objects.exists())
        self.assertFalse(ScheduleGenerationRun.objects.exists())

    def test_demo_calendar_names_and_reseeding_preserve_period_links(self):
        term = self.first.academic_term
        self.assertEqual(str(term), "2026-2027 - First semester (FIRST)")
        # An older installation may still use the original labels.
        term.academic_year.label = "CAS Demo 2026-2027"
        term.academic_year.save()
        term.code = "CAS-DEMO"
        term.save()
        term.semester.code = "CAS-DEMO-1"
        term.semester.name = "First semester (section demo)"
        term.semester.save()
        for normalized in (False, True):
            if normalized:
                term.academic_year.label = "2026-2027"
                term.academic_year.save()
                term.code = "FIRST"
                term.save()
                term.semester.code = "FIRST"
                term.semester.name = "First semester"
                term.semester.save()
            with self.subTest(normalized=normalized), override_settings(DEBUG=True):
                call_command("seed_section_demo", actor=self.admin.username, stdout=StringIO())
                self.assertEqual(AcademicTerm.objects.count(), 1)
                self.assertEqual(ClassSection.objects.count(), 6)
                self.assertEqual(SubjectOffering.objects.count(), 24)
                self.assertEqual(Schedule.objects.count(), 2)
                self.assertEqual(set(ClassSection.objects.values_list("academic_term_id", flat=True)), {term.pk})

    def test_full_college_demo_is_repeatable_and_preserves_cas_meetings(self):
        run = self.generate()
        self.assertEqual(accept_generation(user=self.admin, run_id=run.pk).status, "ACCEPTED")
        original = list(ScheduleEntry.objects.order_by("pk").values_list(
            "pk", "assignment_id", "room_id", "day_of_week", "start_time", "end_time"))
        for repeat in range(2):
            with self.subTest(repeat=repeat), override_settings(DEBUG=True):
                call_command("seed_section_demo", actor=self.admin.username,
                             all_colleges=True, stdout=StringIO())
                self.assertEqual(College.objects.count(), 5)
                self.assertEqual(Department.objects.count(), 11)
                self.assertEqual(Program.objects.count(), 11)
                self.assertEqual(ClassSection.objects.count(), 88)
                self.assertEqual(SubjectOffering.objects.count(), 352)
                self.assertEqual(Schedule.objects.count(), 11)
                self.assertEqual(list(ScheduleEntry.objects.order_by("pk").values_list(
                    "pk", "assignment_id", "room_id", "day_of_week", "start_time", "end_time")), original)
        for college, codes in {"CAS": {"BSIT", "BSCS"}, "CCJE": {"CRIM"},
                               "CIT": {"IT", "COMPTECH", "AUTO"}, "CAF": {"ANSCI", "AGRON"},
                               "CBA": {"HM", "OA", "BA"}}.items():
            self.assertEqual(set(Department.objects.filter(college__code=college).values_list(
                "code", flat=True)), codes)
        for department in Department.objects.all():
            for level in (1, 2, 3, 4):
                sections = ClassSection.objects.filter(department=department, year_level=level)
                self.assertEqual(set(sections.values_list("code", flat=True)),
                                 {f"{department.code}-{level}A", f"{department.code}-{level}B"})
                for section in sections:
                    self.assertEqual(section.offering_requirements.count(), 4)
        for code in ("CRIM", "IT", "COMPTECH", "AUTO", "ANSCI", "AGRON", "HM", "OA", "BA"):
            department = Department.objects.get(code=code)
            schedule = Schedule.objects.get(department=department)
            section = ClassSection.objects.get(department=department, code=f"{code}-4B")
            run = request_generation(user=self.admin, schedule_id=schedule.pk, section_id=section.pk,
                                     strategy="FILL_GAPS", overrides=GenerationOverrides())
            self.assertEqual(run.status, "PROPOSAL_READY", (code, run.diagnostics))
            self.assertEqual(run.proposed_meeting_count, 5)

    def test_legacy_archive_hides_only_known_demo_terms_and_preserves_history(self):
        with override_settings(DEBUG=True):
            call_command("seed_foundation", stdout=StringIO())
        unrelated = College.objects.create(code="CUSTOM", name="User-created college")
        before = list(Schedule.objects.values_list("pk", "status"))
        with override_settings(DEBUG=True):
            call_command("seed_section_demo", actor=self.admin.username,
                         archive_legacy=True, stdout=StringIO())
        self.assertFalse(College.objects.get(code="DEMO-A").is_active)
        self.assertFalse(College.objects.get(code="DEMO-B").is_active)
        self.assertFalse(AcademicTerm.objects.get(code="DEMO-TERM").is_active)
        self.assertTrue(AcademicTerm.objects.get(code="FIRST").is_active)
        unrelated.refresh_from_db()
        self.assertTrue(unrelated.is_active)
        self.assertEqual(list(Schedule.objects.values_list("pk", "status")), before)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("timetabling:section-generator"))
        self.assertNotContains(response, "Example teaching period")
        self.assertNotContains(response, "Example Department One")

    def test_subject_preview_is_read_only_and_lists_only_selected_section(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("timetabling:section-generator"), self.data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["subject_rows"]), 4)
        self.assertContains(response, "Not scheduled", count=4)
        self.assertContains(response, "Generate schedule for BSIT-1A")
        self.assertFalse(ScheduleGenerationRun.objects.exists())
        self.assertFalse(ScheduleEntry.objects.exists())

    def test_year_level_accepts_one_to_four_and_rejects_other_values(self):
        for year in ("1", "2", "3", "4"):
            with self.subTest(year=year):
                form = SectionGenerationForm(
                    data={**self.data(), "year_level": year, "section": ""}, user=self.admin)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["year_level"], int(year))
        for year in ("0", "5", "-1", "1.5", "invalid"):
            with self.subTest(year=year):
                form = SectionGenerationForm(
                    data={**self.data(), "year_level": year, "section": ""}, user=self.admin)
                self.assertFalse(form.is_valid())
                self.assertIn("year_level", form.errors)

    def test_section_proposal_acceptance_and_second_section_preserve_meetings(self):
        first_run = self.generate()
        allowed = set(self.first.offering_requirements.values_list("subject_offering__teaching_assignments__pk", flat=True))
        self.assertEqual({row["assignment_id"] for row in first_run.proposed_meetings}, allowed)
        self.assertEqual(first_run.proposed_meeting_count, 5)
        self.assertFalse(ScheduleEntry.objects.exists())
        self.assertEqual(accept_generation(user=self.admin, run_id=first_run.pk).status, "ACCEPTED")
        original = list(ScheduleEntry.objects.order_by("pk").values_list("pk", "day_of_week", "start_time", "end_time", "room_id"))
        second_run = self.generate(self.second)
        self.assertEqual(accept_generation(user=self.admin, run_id=second_run.pk).status, "ACCEPTED")
        self.assertEqual(list(ScheduleEntry.objects.filter(pk__in=[row[0] for row in original]).order_by("pk").values_list(
            "pk", "day_of_week", "start_time", "end_time", "room_id")), original)
        self.assertFalse([item for item in get_schedule_conflicts(self.schedule, user=self.admin,
            section_id=self.second.pk) if item.severity == "ERROR"])
        self.assertTrue(any(item.code == "TEACHING_HOURS_INCOMPLETE" for item in
                            publication_conflicts(self.schedule, self.admin)))
        replacement = self.generate(self.second, "REPLACE_UNLOCKED")
        self.assertEqual(len(replacement.input_summary["replace_entry_ids"]), 5)
        self.assertEqual(accept_generation(user=self.admin, run_id=replacement.pk).status, "ACCEPTED")
        self.assertEqual(list(ScheduleEntry.objects.filter(pk__in=[row[0] for row in original]).order_by("pk").values_list(
            "pk", "day_of_week", "start_time", "end_time", "room_id")), original)
        self.assertEqual(ScheduleEntry.objects.count(), 10)
        with override_settings(DEBUG=True):
            call_command("seed_section_demo", actor=self.admin.username, stdout=StringIO())
        self.assertEqual(ScheduleEntry.objects.count(), 10)
        self.assertEqual(SubjectOffering.objects.count(), 24)

    def test_default_department_generation_still_covers_every_section(self):
        run = request_generation(user=self.admin, schedule_id=self.schedule.pk,
                                 strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
        self.assertEqual(run.proposed_meeting_count, 15)
        self.assertNotIn("generation_section_id", run.configuration_snapshot)

    def test_section_outside_schedule_and_inactive_sections_are_rejected_before_run(self):
        for section in (self.foreign, self.first):
            if section == self.first:
                ClassSection.objects.filter(pk=section.pk).update(is_active=False)
            with self.assertRaises(Http404):
                request_generation(user=self.admin, schedule_id=self.schedule.pk,
                    section_id=section.pk, strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertFalse(ScheduleGenerationRun.objects.exists())

    def test_staff_scope_and_year_mismatch_cannot_generate(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("timetabling:section-generator"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BSCS Department")
        for data in (self.data(self.foreign), {**self.data(), "year_level": 2}):
            response = self.client.post(reverse("timetabling:section-generator"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(ScheduleGenerationRun.objects.exists())

    def test_post_creates_selected_section_proposal_and_tampering_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("timetabling:section-generator"), self.data())
        run = ScheduleGenerationRun.objects.get()
        self.assertRedirects(response, reverse("timetabling:generation-run-detail", args=[run.pk]))
        self.assertEqual(run.configuration_snapshot["generation_section_id"], self.first.pk)
        self.assertContains(self.client.get(response.url), "Return to section subjects")
        run.input_summary["generation_section_id"] = self.second.pk
        run.save(update_fields=["input_summary"])
        self.assertEqual(accept_generation(user=self.admin, run_id=run.pk).status, "VALIDATION_FAILED")
        self.assertFalse(ScheduleEntry.objects.exists())

    def test_seed_requires_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_section_demo", stdout=StringIO())

    def test_shared_college_rooms_are_protected_across_bsit_and_bscs(self):
        other_schedule = Schedule.objects.get(department=self.foreign.department)
        other_run = request_generation(user=self.admin, schedule_id=other_schedule.pk,
            section_id=self.foreign.pk, strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertEqual(other_run.status, "PROPOSAL_READY", other_run.diagnostics)
        self.assertEqual(accept_generation(user=self.admin, run_id=other_run.pk).status, "ACCEPTED")
        run = self.generate()
        self.assertEqual(accept_generation(user=self.admin, run_id=run.pk).status, "ACCEPTED")
        self.assertFalse([finding for finding in get_schedule_conflicts(self.schedule,
            user=self.admin, section_id=self.first.pk) if finding.severity == "ERROR"])

    def test_other_unscheduled_section_does_not_need_complete_meeting_requirements(self):
        AssignmentMeetingRequirement.objects.filter(
            assignment__subject_offering__scheduling_requirement__section=self.second).delete()
        self.assertEqual(self.generate().status, "PROPOSAL_READY")

    def test_empty_section_gives_readiness_error_without_writing_meetings(self):
        empty = ClassSection.objects.create(academic_term=self.first.academic_term,
            department=self.first.department, year_level=1, code="BSIT-EMPTY")
        run = request_generation(user=self.admin, schedule_id=self.schedule.pk,
            section_id=empty.pk, strategy="FILL_GAPS", overrides=GenerationOverrides())
        self.assertEqual(run.status, "INPUT_INVALID")
        self.assertIn("SECTION_EMPTY", {item["code"] for item in run.diagnostics})
        self.assertFalse(ScheduleEntry.objects.exists())

    def test_section_generation_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(reverse("timetabling:section-generator"), self.data())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ScheduleGenerationRun.objects.exists())

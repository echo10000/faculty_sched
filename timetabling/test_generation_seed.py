"""Development-only generator fixtures, including a deliberate readiness failure."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase, override_settings

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from accounts.models import AdminProfile
from core.models import College, Department, SystemSetting
from faculty.models import AcademicRank, EmploymentCategory, Faculty
from resources.models import Building, RoomType
from scheduling.models import Room
from workloads.models import (
    FacultyAvailability,
    FacultySubjectAssignment,
    FacultyTermCapacity,
    SubjectOffering,
    WorkloadPolicy,
)

from .generation_inputs import GenerationOverrides, prepare_generation_input
from .locking import scheduling_lock
from .models import (
    AssignmentMeetingRequirement,
    ClassSection,
    OfferingRequirement,
    Schedule,
    ScheduleEntry,
    ScheduleGenerationRun,
    SchedulingConfiguration,
)
from .solver.engine import solve


@override_settings(DEBUG=True)
class GenerationSeedTests(TestCase):
    seed_models = (
        College, Department, AcademicYear, Semester, AcademicTerm, SystemSetting,
        EmploymentCategory, AcademicRank, RoomType, Building, Faculty, Subject,
        Room, WorkloadPolicy, FacultyTermCapacity, SubjectOffering,
        FacultySubjectAssignment, FacultyAvailability, ClassSection,
        OfferingRequirement, Schedule, ScheduleEntry,
        SchedulingConfiguration, AssignmentMeetingRequirement,
        ScheduleGenerationRun,
    )

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser(
            username="seed_test_admin", email="admin@example.invalid", password="admin-secret"
        )
        cls.existing_user = User.objects.create_user(
            username="existing_seed_user", password="preserve-this-password"
        )
        cls.existing_college = College.objects.create(code="EXISTING", name="Existing College")
        cls.existing_department = Department.objects.create(
            college=cls.existing_college, code="EXISTING", name="Existing Department"
        )
        cls.existing_profile = AdminProfile.objects.create(
            user=cls.existing_user,
            role=AdminProfile.Role.STAFF,
            department=cls.existing_department,
            is_enabled=False,
        )

    def seed(self, *args):
        call_command("seed_timetables", *args, stdout=StringIO())

    def counts(self):
        return {model: model.objects.count() for model in self.seed_models}

    def prepare(self, name):
        schedule = Schedule.objects.get(name=name)
        with transaction.atomic():
            scheduling_lock()
            return prepare_generation_input(
                user=self.admin,
                schedule_id=schedule.pk,
                strategy=ScheduleGenerationRun.Strategy.FILL_GAPS,
                overrides=GenerationOverrides(),
            )

    def test_production_denial_precedes_any_mutation(self):
        initial = self.counts()
        with override_settings(DEBUG=False):
            for args in ((), ("--with-infeasible-generator-example",)):
                with self.subTest(args=args), self.assertRaises(CommandError):
                    self.seed(*args)
                self.assertEqual(self.counts(), initial)

    def test_default_seed_is_idempotent_preserves_existing_data_and_solves(self):
        password = get_user_model().objects.get(pk=self.existing_user.pk).password
        profile = AdminProfile.objects.get(pk=self.existing_profile.pk)
        original_profile = (
            profile.role, profile.college_id, profile.department_id, profile.is_enabled
        )

        self.seed()
        first_counts = self.counts()
        self.assertEqual(SchedulingConfiguration.objects.count(), 2)
        self.assertEqual(AssignmentMeetingRequirement.objects.count(), 4)
        self.assertEqual(Schedule.objects.filter(name="Example manual timetable").count(), 2)
        self.assertEqual(ScheduleEntry.objects.count(), 4)
        workspace = Schedule.objects.get(name="Example generator workspace")
        self.assertEqual(workspace.department.code, "DEMO-D1")
        self.assertFalse(workspace.entries.exists())
        original_entries = tuple(
            ScheduleEntry.objects.order_by("pk").values_list(
                "pk", "schedule_id", "assignment_id", "room_id", "day_of_week",
                "start_time", "end_time", "is_locked", "generation_run_id"
            )
        )

        self.seed()
        self.assertEqual(self.counts(), first_counts)
        self.assertEqual(
            get_user_model().objects.get(pk=self.existing_user.pk).password, password
        )
        profile.refresh_from_db()
        self.assertEqual(
            (profile.role, profile.college_id, profile.department_id, profile.is_enabled),
            original_profile,
        )
        self.assertEqual(
            tuple(ScheduleEntry.objects.order_by("pk").values_list(
                "pk", "schedule_id", "assignment_id", "room_id", "day_of_week",
                "start_time", "end_time", "is_locked", "generation_run_id"
            )),
            original_entries,
        )
        self.assertFalse(ScheduleGenerationRun.objects.exists())
        self.assertFalse(ScheduleEntry.objects.exclude(generation_run=None).exists())

        prepared = self.prepare("Example generator workspace")
        self.assertFalse([issue for issue in prepared.issues if issue.severity == "ERROR"])
        self.assertIsNotNone(prepared.solver_input)
        result = solve(prepared.solver_input)
        self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})
        self.assertGreater(len(result.proposals), 0)

    def test_optional_zero_candidate_example_is_separate_and_idempotent(self):
        self.seed()
        baseline = self.counts()
        self.seed("--with-infeasible-generator-example")
        after_optional = self.counts()
        expected_increments = {
            AcademicTerm, SubjectOffering, FacultySubjectAssignment, ClassSection,
            OfferingRequirement, SchedulingConfiguration,
            AssignmentMeetingRequirement, Schedule,
        }
        for model in self.seed_models:
            self.assertEqual(
                after_optional[model] - baseline[model],
                1 if model in expected_increments else 0,
                model.__name__,
            )
        self.seed("--with-infeasible-generator-example")
        self.assertEqual(self.counts(), after_optional)
        self.seed()
        self.assertEqual(self.counts(), after_optional)

        impossible = self.prepare("Example infeasible generator workspace")
        self.assertIn("ZERO_CANDIDATES", {issue.code for issue in impossible.issues})
        self.assertIsNone(impossible.solver_input)
        feasible = self.prepare("Example generator workspace")
        self.assertFalse([issue for issue in feasible.issues if issue.severity == "ERROR"])
        self.assertIsNotNone(feasible.solver_input)

        default_term = AcademicTerm.objects.get(code="DEMO-TERM")
        impossible_term = AcademicTerm.objects.get(code="DEMO-INFEASIBLE")
        self.assertLess(default_term.end_date, impossible_term.start_date)
        self.assertFalse(ScheduleGenerationRun.objects.exists())

    def test_repeat_seed_preserves_edited_configuration_and_meeting_split(self):
        self.seed()
        configuration = SchedulingConfiguration.objects.get(department__code="DEMO-D1")
        configuration.room_fit_weight = 0
        configuration.save()
        requirement = AssignmentMeetingRequirement.objects.get(
            assignment__faculty__employee_id="DEMO-F1", meeting_type="lecture"
        )
        requirement.meetings_per_week = 2
        requirement.duration_minutes = 60
        requirement.save()
        counts = self.counts()

        self.seed()
        configuration.refresh_from_db()
        requirement.refresh_from_db()
        self.assertEqual(self.counts(), counts)
        self.assertEqual(configuration.room_fit_weight, 0)
        self.assertEqual((requirement.meetings_per_week, requirement.duration_minutes), (2, 60))

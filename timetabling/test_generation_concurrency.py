"""Database-backed races for generation terminal actions and source writers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from core.models import College, Department
from faculty.models import EmploymentCategory, Faculty
from scheduling.models import Room
from workloads.models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering

from .generation import InvalidRunTransition, accept_generation, discard_generation, request_generation
from .generation_inputs import GenerationOverrides
from .models import (
    AssignmentMeetingRequirement, ClassSection, OfferingRequirement,
    Schedule, ScheduleEntry, ScheduleGenerationRun, SchedulingConfiguration,
)


class GenerationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        college = College.objects.create(code="RACE", name="Race college")
        department = Department.objects.create(code="RACE", name="Race department", college=college)
        self.user = get_user_model().objects.create_superuser(
            username="race-admin", password="test-only-password",
            email="race@example.invalid",
        )
        year = AcademicYear.objects.create(
            label="Race year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        semester = Semester.objects.create(code="RACE", name="Race semester")
        self.term = AcademicTerm.objects.create(
            academic_year=year, semester=semester, code="RACE",
            start_date=date(2026, 1, 1), end_date=date(2026, 5, 31),
        )
        subject = Subject.objects.create(
            code="RACE", title="Race subject", owning_department=department,
            units=Decimal("3"), lecture_units=Decimal("2"),
            laboratory_units=Decimal("1"),
        )
        category = EmploymentCategory.objects.create(code="race", name="Race category")
        self.faculty = Faculty.objects.create(
            employee_id="RACE", first_name="Race", last_name="Faculty",
            home_department=department, employment_category=category,
        )
        offering = SubjectOffering.objects.create(
            subject=subject, academic_term=self.term, department=department,
            lecture_units=2, laboratory_units=1, lecture_hours=2,
            laboratory_hours=3,
        )
        section = ClassSection.objects.create(
            academic_term=self.term, department=department, code="RACE",
            expected_size=20,
        )
        OfferingRequirement.objects.create(subject_offering=offering, section=section)
        assignment = FacultySubjectAssignment.objects.create(
            faculty=self.faculty, subject_offering=offering,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=assignment, meeting_type="lecture",
            meetings_per_week=2, duration_minutes=60,
        )
        AssignmentMeetingRequirement.objects.create(
            assignment=assignment, meeting_type="laboratory",
            meetings_per_week=1, duration_minutes=180,
        )
        Room.objects.create(
            code="RACE", name="Race room", capacity=30,
            owner_department=department,
        )
        self.schedule = Schedule.objects.create(
            academic_term=self.term, department=department, name="Race schedule",
        )
        SchedulingConfiguration.objects.create(
            academic_term=self.term, department=department,
            allowed_weekdays=[1, 2, 3, 4, 5], earliest_start=time(8),
            latest_end=time(17), slot_increment_minutes=30,
            solver_time_limit_seconds=10, random_seed=17, worker_count=1,
        )

    def ready(self):
        run = request_generation(
            user=self.user, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
        self.assertEqual(run.status, "PROPOSAL_READY", run.diagnostics)
        return run

    def test_writer_committed_after_capture_causes_stale_acceptance(self):
        run = self.ready()
        start = Barrier(2)
        committed = Barrier(2)

        def writer():
            close_old_connections()
            try:
                start.wait(timeout=10)
                FacultyAvailability.objects.create(
                    faculty_id=self.faculty.pk, academic_term_id=self.term.pk,
                    day_of_week=1, start_time=time(8), end_time=time(9),
                    availability_type="unavailable",
                )
                committed.wait(timeout=10)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            start.wait(timeout=10)
            committed.wait(timeout=10)
            future.result(timeout=10)
        result = accept_generation(user=self.user, run_id=run.pk)
        self.assertEqual(result.status, "STALE")
        self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())

    def test_accept_and_discard_race_has_one_terminal_winner(self):
        run = self.ready()
        start = Barrier(3)

        def act(action):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                start.wait(timeout=10)
                try:
                    return action(user=user, run_id=run.pk).status
                except InvalidRunTransition:
                    return "LOST"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = pool.submit(act, accept_generation)
            discarded = pool.submit(act, discard_generation)
            start.wait(timeout=10)
            outcomes = {accepted.result(timeout=30), discarded.result(timeout=30)}
        self.assertTrue(outcomes in ({"ACCEPTED", "LOST"}, {"DISCARDED", "LOST"}))
        run.refresh_from_db()
        self.assertIn(run.status, ("ACCEPTED", "DISCARDED"))
        if run.status == "ACCEPTED":
            self.assertEqual(ScheduleEntry.objects.filter(generation_run=run).count(),
                             run.proposed_meeting_count)
        else:
            self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())

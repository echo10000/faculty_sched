from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from unittest.mock import patch

from academics.models import Curriculum, Student, Subject
from accounts.models import AdminProfile
from core.models import College, Department, Program
from faculty.models import Faculty, FacultyQualification
from scheduling.models import Assignment, Block, Room, Term, TimeSlot
from scheduling.autoscheduler import generate_schedule_suggestions, prepare_scheduling_inputs


@override_settings(ROOT_URLCONF="config.legacy_test_urls")
class AssignmentValidationTests(TestCase):
    def setUp(self):
        college = College.objects.create(name="College of Computing", code="CC")
        self.department = Department.objects.create(college=college, name="Computing", code="COMP")
        program = Program.objects.create(department=self.department, name="Computer Science", code="BSCS")
        curriculum = Curriculum.objects.create(program=program, version_year=2026)
        self.term = Term.objects.create(academic_year="2026-2027", term_name="1st", is_active=True)
        self.block_one = Block.objects.create(curriculum=curriculum, term=self.term, section_code="A", year_level=1)
        self.block_two = Block.objects.create(curriculum=curriculum, term=self.term, section_code="B", year_level=1)
        self.subject_one = Subject.objects.create(code="CS101", title="Programming", units=Decimal("3.0"))
        self.subject_two = Subject.objects.create(code="CS102", title="Databases", units=Decimal("3.0"))
        self.faculty_one = self.make_faculty("FAC-001", "Ada")
        self.faculty_two = self.make_faculty("FAC-002", "Grace")
        self.unqualified_faculty = self.make_faculty("FAC-003", "Linus")
        FacultyQualification.objects.create(faculty=self.faculty_one, subject=self.subject_one)
        FacultyQualification.objects.create(faculty=self.faculty_two, subject=self.subject_one)
        FacultyQualification.objects.create(faculty=self.faculty_two, subject=self.subject_two)
        self.room_one = Room.objects.create(name="Room 101", room_type="lecture", capacity=40)
        self.room_two = Room.objects.create(name="Room 102", room_type="lecture", capacity=40)
        self.slot_one = TimeSlot.objects.create(day_of_week="MON", start_time=time(9), end_time=time(10, 30))
        self.slot_overlap = TimeSlot.objects.create(day_of_week="MON", start_time=time(10), end_time=time(11, 30))
        self.slot_clear = TimeSlot.objects.create(day_of_week="TUE", start_time=time(9), end_time=time(10, 30))

    def make_faculty(self, employee_id, first_name):
        return Faculty.objects.create(
            employee_id=employee_id,
            first_name=first_name,
            last_name="Faculty",
            home_department=self.department,
            employment_type="full_time",
        )

    def make_assignment(self, **overrides):
        values = {
            "faculty": self.faculty_one,
            "subject": self.subject_one,
            "block": self.block_one,
            "room": self.room_one,
            "term": self.term,
            "time_slot": self.slot_one,
            "units_credited": Decimal("3.0"),
        }
        values.update(overrides)
        return Assignment.objects.create(**values)

    def test_successful_assignment(self):
        assignment = self.make_assignment()
        self.assertIsNotNone(assignment.pk)

    def test_rejects_faculty_double_booking(self):
        self.make_assignment()
        with self.assertRaisesMessage(ValidationError, "already assigned"):
            self.make_assignment(block=self.block_two, room=self.room_two, time_slot=self.slot_overlap)

    def test_rejects_room_double_booking(self):
        self.make_assignment()
        with self.assertRaisesMessage(ValidationError, "already booked"):
            self.make_assignment(faculty=self.faculty_two, block=self.block_two, time_slot=self.slot_overlap)

    def test_rejects_block_double_booking(self):
        self.make_assignment()
        with self.assertRaisesMessage(ValidationError, "already has"):
            self.make_assignment(
                faculty=self.faculty_two,
                subject=self.subject_two,
                room=self.room_two,
                time_slot=self.slot_overlap,
            )

    def test_rejects_unqualified_faculty(self):
        with self.assertRaisesMessage(ValidationError, "not qualified"):
            self.make_assignment(faculty=self.unqualified_faculty, time_slot=self.slot_clear)

    def test_database_constraint_rejects_overlap_when_validation_is_bypassed(self):
        self.make_assignment()
        candidate = Assignment(
            faculty=self.faculty_one,
            subject=self.subject_one,
            block=self.block_two,
            room=self.room_two,
            term=self.term,
            time_slot=self.slot_overlap,
            units_credited=Decimal("3.0"),
        )
        with patch.object(Assignment, "full_clean", return_value=None):
            with self.assertRaises(IntegrityError):
                candidate.save()

    def test_dashboard_lists_capacity_conflict(self):
        self.make_assignment()
        for number in range(41):
            Student.objects.create(
                student_number=f"2026-{number:05d}",
                first_name="Student",
                last_name=str(number),
                program=self.block_one.curriculum.program,
                curriculum=self.block_one.curriculum,
                block=self.block_one,
                year_level=1,
            )
        user = User.objects.create_user(username="dept-admin", password="password")
        AdminProfile.objects.create(
            user=user,
            role=AdminProfile.Role.STAFF,
            department=self.department,
        )
        self.client.force_login(user)
        response = self.client.get("/scheduling/")
        self.assertContains(response, "Room 101")
        self.assertContains(response, "41 enrolled students")

    def test_dashboard_filters_to_pending_approval_assignments(self):
        assignment = self.make_assignment()
        Assignment.objects.filter(pk=assignment.pk).update(status=Assignment.Status.PENDING_APPROVAL)
        user = User.objects.create_user(username="pending-admin", password="password")
        AdminProfile.objects.create(
            user=user,
            role=AdminProfile.Role.STAFF,
            department=self.department,
        )
        self.client.force_login(user)
        response = self.client.get("/scheduling/?status=pending_approval")
        items = list(response.context["assignment_status_items"])
        self.assertEqual([item.pk for item in items], [assignment.pk])
        self.assertContains(response, "Awaiting review")

    def make_admin_user(self, username, role):
        user = User.objects.create_user(username=username, password="password")
        AdminProfile.objects.create(
            user=user,
            role=role,
            college=self.department.college if role == AdminProfile.Role.STAFF else None,
            department=None if role in (AdminProfile.Role.STAFF, AdminProfile.Role.SUPER_ADMIN) else self.department,
        )
        return user

    @override_settings(ROOT_URLCONF="config.urls")
    def test_draft_assignments_submit_for_approval(self):
        assignment = self.make_assignment()
        user = self.make_admin_user('retired-flow-user', AdminProfile.Role.STAFF)
        self.client.force_login(user)
        response = self.client.post(f'/scheduling/assignments/submit/', {'term_id': self.term.pk, 'block_id': self.block_one.pk})
        self.assertEqual(response.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.Status.DRAFT)
        self.assertFalse(assignment.status_logs.exists())

    @override_settings(ROOT_URLCONF="config.urls")
    def test_pending_assignments_are_approved_by_dean(self):
        assignment = self.make_assignment()
        user = self.make_admin_user('retired-flow-user', AdminProfile.Role.STAFF)
        self.client.force_login(user)
        response = self.client.post(f'/scheduling/assignments/approve/', {'term_id': self.term.pk, 'block_id': self.block_one.pk})
        self.assertEqual(response.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.Status.DRAFT)
        self.assertFalse(assignment.status_logs.exists())

    @override_settings(ROOT_URLCONF="config.urls")
    def test_non_dean_cannot_approve_assignments(self):
        assignment = self.make_assignment()
        user = self.make_admin_user('retired-flow-user', AdminProfile.Role.STAFF)
        self.client.force_login(user)
        response = self.client.post(f'/scheduling/assignments/approve/', {'term_id': self.term.pk, 'block_id': self.block_one.pk})
        self.assertEqual(response.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.Status.DRAFT)
        self.assertFalse(assignment.status_logs.exists())

    @override_settings(ROOT_URLCONF="config.urls")
    def test_non_dean_cannot_unlock_an_approved_assignment(self):
        assignment = self.make_assignment()
        user = self.make_admin_user('retired-flow-user', AdminProfile.Role.STAFF)
        self.client.force_login(user)
        response = self.client.post(f'/scheduling/assignments/{assignment.pk}/unlock/', {'term_id': self.term.pk, 'block_id': self.block_one.pk})
        self.assertEqual(response.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.Status.DRAFT)
        self.assertFalse(assignment.status_logs.exists())

    @override_settings(ROOT_URLCONF="config.urls")
    def test_dean_unlock_returns_assignment_to_draft_and_logs_reason(self):
        assignment = self.make_assignment()
        user = self.make_admin_user('retired-flow-user', AdminProfile.Role.STAFF)
        self.client.force_login(user)
        response = self.client.post(f'/scheduling/assignments/{assignment.pk}/unlock/', {'term_id': self.term.pk, 'block_id': self.block_one.pk})
        self.assertEqual(response.status_code, 404)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.Status.DRAFT)
        self.assertFalse(assignment.status_logs.exists())


@override_settings(ROOT_URLCONF="config.legacy_test_urls")
class SchedulingInputPreparationTests(TestCase):
    def setUp(self):
        call_command("seed_demo_data")
        self.term = Term.objects.get(is_active=True)
        TimeSlot.objects.create(day_of_week="MON", start_time=time(9), end_time=time(10, 30))
        TimeSlot.objects.create(day_of_week="WED", start_time=time(13), end_time=time(14, 30))

    def test_seed_data_produces_unfilled_demands_with_plain_options(self):
        inputs = prepare_scheduling_inputs(self.term)
        eligible_demand = next(
            demand
            for demand in inputs.demands
            if demand.faculty_ids and demand.room_ids and demand.time_slot_ids
        )
        self.assertTrue(inputs.time_slots)
        self.assertIsInstance(eligible_demand.block_id, int)
        self.assertTrue(eligible_demand.faculty_ids)
        self.assertTrue(eligible_demand.room_ids)
        self.assertEqual(eligible_demand.time_slot_ids, tuple(slot.id for slot in inputs.time_slots))

        block = Block.objects.get(pk=eligible_demand.block_id)
        Assignment.objects.create(
            faculty_id=eligible_demand.faculty_ids[0],
            subject_id=eligible_demand.subject_id,
            block=block,
            room_id=eligible_demand.room_ids[0],
            term=self.term,
            time_slot_id=eligible_demand.time_slot_ids[0],
            units_credited=Decimal(eligible_demand.units),
        )
        refreshed_inputs = prepare_scheduling_inputs(self.term)
        self.assertNotIn(
            (eligible_demand.block_id, eligible_demand.subject_id),
            {(demand.block_id, demand.subject_id) for demand in refreshed_inputs.demands},
        )

    def test_solver_returns_non_conflicting_proposals_for_two_blocks(self):
        block_ids = list(Block.objects.filter(term=self.term).values_list("id", flat=True)[:2])
        result = generate_schedule_suggestions(self.term, block_ids=block_ids)
        self.assertTrue(result["proposals"])
        slot_by_id = {slot.id: slot for slot in TimeSlot.objects.all()}
        for left_index, left in enumerate(result["proposals"]):
            for right in result["proposals"][left_index + 1:]:
                left_slot = slot_by_id[left["time_slot_id"]]
                right_slot = slot_by_id[right["time_slot_id"]]
                overlap = (
                    left_slot.day_of_week == right_slot.day_of_week
                    and left_slot.start_time < right_slot.end_time
                    and left_slot.end_time > right_slot.start_time
                )
                if overlap:
                    self.assertNotEqual(left["faculty_id"], right["faculty_id"])
                    self.assertNotEqual(left["room_id"], right["room_id"])
                    self.assertNotEqual(left["block_id"], right["block_id"])

    def test_batch_commit_saves_valid_rows_when_another_row_is_invalid(self):
        demand = next(
            demand
            for demand in prepare_scheduling_inputs(self.term).demands
            if demand.faculty_ids and demand.room_ids and demand.time_slot_ids
        )
        block = Block.objects.get(pk=demand.block_id)
        user = User.objects.create_user(username="scheduler", password="password")
        AdminProfile.objects.create(
            user=user,
            role=AdminProfile.Role.STAFF,
            department=block.curriculum.program.department,
        )
        self.client.force_login(user)
        payload = {
            "term_id": self.term.id,
            "row_count": 2,
            "accept_0": "on",
            "block_0": demand.block_id,
            "subject_0": demand.subject_id,
            "faculty_0": demand.faculty_ids[0],
            "room_0": demand.room_ids[0],
            "time_slot_0": demand.time_slot_ids[0],
            "accept_1": "on",
            "block_1": demand.block_id,
            "subject_1": demand.subject_id,
            "faculty_1": "",
            "room_1": demand.room_ids[0],
            "time_slot_1": demand.time_slot_ids[0],
        }
        response = self.client.post("/scheduling/autoschedule/commit/", payload, follow=True)
        self.assertEqual(Assignment.objects.filter(block=block, subject_id=demand.subject_id).count(), 1)
        self.assertContains(response, "Created 1 assignment")
        self.assertContains(response, "Row 2")

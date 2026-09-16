from datetime import date, time

from academics.models import AcademicTerm
from scheduling.models import Room
from workloads.models import FacultySubjectAssignment

from .conflicts import validate_candidate_schedule
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
from .mutations import mutation_lock
from .occupancy import authoritative_occupancy
from .signatures import dependency_signature
from .tests import TimetableFixture


class GenerationValidationTests(TimetableFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_room = Room.objects.create(
            name="Other room",
            code="R1-OTHER",
            capacity=60,
            category=cls.room_type,
            owner_department=cls.department,
        )
        cls.foreign_schedule = Schedule.objects.create(
            name="Protected foreign schedule",
            department=cls.external,
            academic_term=cls.term,
        )

    def candidate(self, **changes):
        values = {
            "schedule": self.schedule,
            "assignment": self.assignment,
            "room": self.room,
            "day_of_week": 1,
            "start_time": time(9),
            "end_time": time(10),
            "meeting_type": "lecture",
        }
        values.update(changes)
        return ScheduleEntry(**values)

    def one_of_two_required_meetings(self):
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )
        return self.candidate()

    def foreign_peer(self):
        section = ClassSection.objects.create(
            academic_term=self.term,
            department=self.external,
            code="PRIVATE",
        )
        offering = self.offerings[self.external.pk]
        OfferingRequirement.objects.create(subject_offering=offering, section=section)
        assignment = FacultySubjectAssignment.objects.create(
            faculty=self.records[self.external.pk]["faculty-management"],
            subject_offering=offering,
        )
        return ScheduleEntry.objects.create(
            schedule=self.foreign_schedule,
            assignment=assignment,
            room=self.room,
            day_of_week=1,
            start_time=time(9),
            end_time=time(10),
            meeting_type="lecture",
        )

    def configuration(self):
        return SchedulingConfiguration.objects.create(
            academic_term=self.term,
            department=self.department,
            allowed_weekdays=[1, 2, 3, 4, 5],
            earliest_start=time(8),
            latest_end=time(17),
            slot_increment_minutes=30,
            solver_time_limit_seconds=10,
            random_seed=17,
            worker_count=1,
            faculty_preference_weight=1,
            faculty_gap_weight=1,
            section_gap_weight=1,
            meeting_distribution_weight=1,
            room_fit_weight=1,
        )

    def test_candidate_schedule_detects_conflicts_between_unsaved_meetings(self):
        first = self.candidate(start_time=time(9), end_time=time(10))
        second = self.candidate(
            room=self.other_room,
            start_time=time(9, 30),
            end_time=time(10, 30),
        )

        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[first, second],
            user=self.chair,
        )

        self.assertIn("FACULTY_OVERLAP", {item.code for item in conflicts})
        self.assertEqual(
            sum(item.code == "FACULTY_OVERLAP" for item in conflicts),
            1,
        )

    def test_invalid_unsaved_meeting_does_not_break_other_candidate_validation(self):
        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[
                self.candidate(end_time=None),
                self.candidate(room=self.other_room, start_time=time(11), end_time=time(12)),
            ],
        )

        self.assertIn("INVALID_TIME_RANGE", {item.code for item in conflicts})

    def test_candidate_schedule_redacts_foreign_peer_identity(self):
        foreign_entry = self.foreign_peer()

        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[self.candidate()],
            user=self.chair,
        )

        protected = [item for item in conflicts if item.code == "ROOM_OVERLAP"]
        self.assertTrue(protected)
        self.assertNotIn(self.foreign_schedule.name, str(protected))
        self.assertNotIn(self.offerings[self.external.pk].subject.code, str(protected))
        self.assertTrue(all(item.other_entry_id is None for item in protected))
        self.assertNotIn(
            str(foreign_entry.pk),
            {str(item.other_entry_id) for item in protected},
        )

    def test_exact_requirement_replaces_aggregate_hours_warning(self):
        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[self.one_of_two_required_meetings()],
        )

        self.assertIn("MEETING_REQUIREMENT_COUNT", {item.code for item in conflicts})
        self.assertNotIn("MEETING_HOURS_WARNING", {item.code for item in conflicts})

    def test_exact_requirement_rejects_wrong_individual_duration(self):
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )

        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[
                self.candidate(start_time=time(9), end_time=time(10, 30)),
                self.candidate(start_time=time(11), end_time=time(12)),
            ],
        )

        codes = {item.code for item in conflicts}
        self.assertIn("MEETING_REQUIREMENT_DURATION", codes)
        self.assertNotIn("MEETING_REQUIREMENT_COUNT", codes)

    def test_replaced_selected_entry_is_not_loaded_as_peer_occupancy(self):
        replaced = self.candidate()
        replaced.save()

        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[self.candidate()],
        )

        overlap_codes = {
            "FACULTY_OVERLAP",
            "ROOM_OVERLAP",
            "SECTION_OVERLAP",
        }
        self.assertFalse(overlap_codes & {item.code for item in conflicts})

    def test_calendar_intersection_must_contain_the_meeting_weekday(self):
        sunday_only = AcademicTerm.objects.create(
            academic_year=self.term.academic_year,
            semester=self.term.semester,
            code="SUN",
            start_date=date(2026, 5, 31),
            end_date=date(2026, 5, 31),
        )
        peer_schedule = Schedule.objects.create(
            name="Sunday intersection",
            department=self.department,
            academic_term=sunday_only,
        )
        peer = self.candidate()
        peer.save()
        ScheduleEntry.objects.filter(pk=peer.pk).update(schedule=peer_schedule)

        conflicts = validate_candidate_schedule(
            self.schedule,
            retained_entries=[],
            proposed_entries=[self.candidate()],
        )

        self.assertNotIn("ROOM_OVERLAP", {item.code for item in conflicts})

    def test_authoritative_occupancy_orders_overlapping_calendars_and_excludes_ids(self):
        selected = self.candidate(day_of_week=2, start_time=time(11), end_time=time(12))
        selected.save()
        overlap_term = AcademicTerm.objects.create(
            academic_year=self.term.academic_year,
            semester=self.term.semester,
            code="OVER",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 30),
        )
        overlap_schedule = Schedule.objects.create(
            name="Overlapping calendar",
            department=self.department,
            academic_term=overlap_term,
        )
        overlapping = self.candidate(day_of_week=1, start_time=time(8), end_time=time(9))
        overlapping.save()
        ScheduleEntry.objects.filter(pk=overlapping.pk).update(schedule=overlap_schedule)
        later_schedule = Schedule.objects.create(
            name="Later calendar",
            department=self.department,
            academic_term=self.later,
        )
        later = self.candidate(day_of_week=1, start_time=time(7), end_time=time(8))
        later.save()
        ScheduleEntry.objects.filter(pk=later.pk).update(schedule=later_schedule)

        occupancy = authoritative_occupancy(
            self.schedule,
            excluded_entry_ids=(selected.pk,),
        )

        self.assertEqual([entry.pk for entry in occupancy], [overlapping.pk])

    def test_scheduling_input_uses_visible_peer_occupancy_and_redacts_foreign(self):
        from .datasets import scheduling_input

        selected = self.candidate(day_of_week=2)
        selected.save()
        peer_schedule = Schedule.objects.create(
            name="Visible peer",
            department=self.department,
            academic_term=self.term,
        )
        visible_peer = self.candidate(
            schedule=peer_schedule,
            room=self.other_room,
            day_of_week=3,
        )
        visible_peer.save()
        foreign_peer = self.foreign_peer()

        data = scheduling_input(self.chair, self.schedule.pk)

        occupancy_ids = {entry["id"] for entry in data["existing_entries"]}
        self.assertEqual(occupancy_ids, {selected.pk, visible_peer.pk})
        self.assertNotIn(foreign_peer.pk, occupancy_ids)

    def test_dependency_signature_tracks_configuration_and_meeting_requirements(self):
        before = dependency_signature(self.schedule)
        configuration = self.configuration()
        after_configuration = dependency_signature(self.schedule)
        AssignmentMeetingRequirement.objects.create(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=2,
            duration_minutes=60,
        )
        after_requirement = dependency_signature(self.schedule)

        self.assertNotEqual(before, after_configuration)
        self.assertNotEqual(after_configuration, after_requirement)
        configuration.room_fit_weight = 7
        configuration.save()
        self.assertNotEqual(after_requirement, dependency_signature(self.schedule))

    def test_dependency_signature_tracks_entry_lock_and_provenance_not_run_state(self):
        entry = self.candidate()
        entry.save()
        before_lock_change = dependency_signature(self.schedule)
        ScheduleEntry.objects.filter(pk=entry.pk).update(is_locked=False)
        after_lock_change = dependency_signature(self.schedule)
        run = ScheduleGenerationRun.objects.create(
            schedule=self.schedule,
            academic_term=self.term,
            department=self.department,
            requested_by=self.chair,
            strategy=ScheduleGenerationRun.Strategy.FILL_GAPS,
        )
        before_provenance = dependency_signature(self.schedule)
        ScheduleEntry.objects.filter(pk=entry.pk).update(generation_run=run)
        after_provenance = dependency_signature(self.schedule)
        ScheduleGenerationRun.objects.filter(pk=run.pk).update(
            status=ScheduleGenerationRun.Status.RUNNING,
        )

        self.assertNotEqual(before_lock_change, after_lock_change)
        self.assertEqual(after_lock_change, before_provenance)
        self.assertNotEqual(before_provenance, after_provenance)
        self.assertEqual(after_provenance, dependency_signature(self.schedule))

    def test_mutation_lock_remains_compatibility_alias(self):
        self.assertIs(mutation_lock, scheduling_lock)

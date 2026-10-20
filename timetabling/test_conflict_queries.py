"""Peer requirement reads scale with offerings, without changing conflict rules."""
from collections import Counter
from datetime import date, time

from django.db import connection
from django.test.utils import CaptureQueriesContext

from academics.models import AcademicTerm
from workloads.models import FacultySubjectAssignment, SubjectOffering
from .conflicts import Conflict, detect_entry_conflicts, get_schedule_conflicts, validate_candidate_schedule
from .models import ClassSection, OfferingRequirement, Schedule, ScheduleEntry
from .tests import TimetableFixture


class PeerRequirementQueryTests(TimetableFixture):
    def candidate(self, **changes):
        values = dict(schedule=self.schedule, assignment=self.assignment, room=self.room,
                      day_of_week=1, start_time=time(9), end_time=time(10), meeting_type="lecture")
        values.update(changes)
        return ScheduleEntry(**values)

    def additional_assignment(self, code, *, term=None, department=None, requirement=True):
        term = term or self.term
        department = department or self.department
        offering = SubjectOffering.objects.create(
            subject=self.records[department.pk]["subjects"], academic_term=term,
            department=department, code=code, lecture_units=2, laboratory_units=0,
            lecture_hours=2, laboratory_hours=0,
        )
        if requirement:
            section = ClassSection.objects.create(academic_term=term, department=department, code=code)
            OfferingRequirement.objects.create(subject_offering=offering, section=section)
        return FacultySubjectAssignment.objects.create(
            faculty=self.records[department.pk]["faculty-management"], subject_offering=offering,
        )

    def captured(self, operation):
        with CaptureQueriesContext(connection) as queries:
            conflicts = operation()
        peer_reads = [item["sql"] for item in queries.captured_queries
                      if 'FROM "timetabling_offeringrequirement"' in item["sql"]
                      and " JOIN " not in item["sql"]]
        return conflicts, peer_reads, len(queries)

    def validate(self, entries):
        return validate_candidate_schedule(self.schedule, retained_entries=(), proposed_entries=entries)

    def overlap_counts(self, conflicts):
        return Counter(item.code for item in conflicts if item.code.endswith("_OVERLAP"))

    def test_candidates_sharing_an_offering_reuse_peer_requirement(self):
        entries = [self.candidate() for _ in range(4)]
        conflicts, reads, _ = self.captured(lambda: self.validate(entries))
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.overlap_counts(conflicts), {
            "FACULTY_OVERLAP": 6, "ROOM_OVERLAP": 6, "SECTION_OVERLAP": 6,
        })
        self.assertEqual(Counter(item.severity for item in conflicts), {"ERROR": 18, "WARNING": 6})
        self.assertIn(Conflict(
            "SECTION_OVERLAP", "ERROR",
            f"Section {self.section.code} is already assigned to {self.offering.subject.code} "
            f"({self.schedule.name}) on Monday 09:00–10:00.",
            "Choose another time or correct the resource configuration.",
        ), conflicts)

    def test_distinct_offerings_with_same_catalog_subject_keep_distinct_sections(self):
        assignments = [self.assignment] + [self.additional_assignment(f"DISTINCT-{i}") for i in range(3)]
        conflicts, reads, _ = self.captured(lambda: self.validate([
            self.candidate(assignment=assignment) for assignment in assignments]))
        self.assertEqual(len(reads), 4)
        self.assertEqual(self.overlap_counts(conflicts), {"FACULTY_OVERLAP": 6, "ROOM_OVERLAP": 6})

    def test_missing_peer_requirements_are_cached_including_none(self):
        missing = self.additional_assignment("MISSING", requirement=False)
        entries = [self.candidate() for _ in range(4)] + [self.candidate(assignment=missing)]
        conflicts, reads, _ = self.captured(lambda: self.validate(entries))
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.overlap_counts(conflicts), {
            "FACULTY_OVERLAP": 10, "ROOM_OVERLAP": 10, "SECTION_OVERLAP": 6,
        })
        self.assertEqual(sum(item.code == "SECTION_REQUIRED" and item.severity == "ERROR"
                             for item in conflicts), 1)

    def test_missing_own_requirement_does_not_mask_faculty_or_room_overlap(self):
        self.requirement.delete()
        peer = self.candidate()
        conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(
            self.candidate(), peers=[peer] * 8))
        self.assertEqual(len(reads), 1)
        self.assertEqual(Counter(item.code for item in conflicts), {
            "SECTION_REQUIRED": 1, "FACULTY_OVERLAP": 8, "ROOM_OVERLAP": 8,
        })
        self.assertTrue(all(item.severity == "ERROR" for item in conflicts))

    def test_missing_requirement_created_between_operations_is_visible(self):
        other = self.additional_assignment("LATER-REQUIREMENT", requirement=False)
        peer = self.candidate(assignment=other)
        before = detect_entry_conflicts(self.candidate(), peers=[peer])
        self.assertNotIn("SECTION_OVERLAP", {item.code for item in before})
        OfferingRequirement.objects.create(subject_offering=other.subject_offering, section=self.section)
        after, reads, _ = self.captured(lambda: detect_entry_conflicts(self.candidate(), peers=[peer]))
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.overlap_counts(after), {
            "FACULTY_OVERLAP": 1, "ROOM_OVERLAP": 1, "SECTION_OVERLAP": 1,
        })

    def test_changed_requirement_is_reloaded_for_the_next_operation(self):
        other = self.additional_assignment("CHANGED")
        peer = self.candidate(assignment=other)
        self.assertNotIn("SECTION_OVERLAP", {item.code for item in detect_entry_conflicts(
            self.candidate(), peers=[peer])})
        requirement = OfferingRequirement.objects.get(subject_offering=other.subject_offering)
        requirement.section = self.section
        requirement.save()
        self.assertIn("SECTION_OVERLAP", {item.code for item in detect_entry_conflicts(
            self.candidate(), peers=[peer])})

    def test_standalone_generator_of_repeated_peers_keeps_all_conflicts(self):
        peer = self.candidate()
        conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(
            self.candidate(), peers=(peer for _ in range(16))))
        self.assertEqual(len(reads), 1)
        self.assertEqual(Counter(item.code for item in conflicts), {
            "ROOM_CAPACITY_WARNING": 1, "FACULTY_OVERLAP": 16,
            "ROOM_OVERLAP": 16, "SECTION_OVERLAP": 16,
        })

    def test_standalone_default_occupancy_preserves_visible_peer_identity(self):
        peer = self.candidate()
        peer.save()
        conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(self.candidate(), user=self.staff))
        self.assertEqual(len(reads), 1)
        overlaps = [item for item in conflicts if item.code.endswith("_OVERLAP")]
        self.assertEqual({item.code for item in overlaps}, {"FACULTY_OVERLAP", "ROOM_OVERLAP", "SECTION_OVERLAP"})
        self.assertTrue(all(item.other_entry_id == peer.pk and item.severity == "ERROR" for item in overlaps))

    def test_whole_schedule_keeps_deduplication_and_advisory_warnings(self):
        for _ in range(2):
            self.candidate().save()
        conflicts, reads, _ = self.captured(lambda: get_schedule_conflicts(self.schedule, user=self.staff))
        self.assertEqual(len(reads), 1)
        self.assertEqual(Counter(item.code for item in conflicts), {
            "ROOM_CAPACITY_WARNING": 2, "FACULTY_OVERLAP": 1, "ROOM_OVERLAP": 1,
            "SECTION_OVERLAP": 1, "MEETING_HOURS_WARNING": 1,
        })
        for item in conflicts:
            self.assertEqual(item.severity, "ERROR" if item.code.endswith("_OVERLAP") else "WARNING")

    def test_protected_sibling_and_external_college_peers_stay_redacted(self):
        peers = []
        for department, code in ((self.sibling, "PRIVATE-SIBLING"), (self.external, "PRIVATE-EXTERNAL")):
            assignment = self.additional_assignment(code, department=department)
            schedule = Schedule.objects.create(name=code, department=department, academic_term=self.term)
            peers.append(ScheduleEntry.objects.create(
                schedule=schedule, assignment=assignment, room=self.room, day_of_week=1,
                start_time=time(9), end_time=time(10), meeting_type="lecture"))
        for user, visible in ((self.staff, set()), (self.college_staff, {peers[0].pk}),
                              (self.admin, {peer.pk for peer in peers})):
            with self.subTest(user=user.username):
                conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(self.candidate(), user=user))
                self.assertEqual(len(reads), 2)
                overlaps = [item for item in conflicts if item.code == "ROOM_OVERLAP"]
                self.assertEqual(len(overlaps), 2)
                self.assertEqual({item.other_entry_id for item in overlaps if item.other_entry_id is not None}, visible)
                for peer, item in zip(peers, overlaps, strict=True):
                    self.assertEqual(item.severity, "ERROR")
                    self.assertEqual(item.remedy, "Choose another time or correct the resource configuration.")
                    if peer.pk not in visible:
                        self.assertIn("another protected meeting", item.message)
                        self.assertNotIn(peer.schedule.name, item.message)
                        self.assertNotIn(peer.assignment.subject_offering.subject.code, item.message)
                        self.assertIsNone(item.other_entry_id)

    def test_cross_term_overlaps_remain_enforced_but_disjoint_dates_do_not(self):
        overlap = AcademicTerm.objects.create(academic_year=self.term.academic_year, semester=self.term.semester,
            code="OVERLAP", start_date=date(2026, 5, 1), end_date=date(2026, 7, 31))
        for term, code in ((overlap, "SHARED-DATES"), (self.later, "DISJOINT-DATES")):
            assignment = self.additional_assignment(code, term=term)
            schedule = Schedule.objects.create(name=code, department=self.department, academic_term=term)
            ScheduleEntry.objects.create(schedule=schedule, assignment=assignment, room=self.room,
                day_of_week=1, start_time=time(9), end_time=time(10), meeting_type="lecture")
        conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(self.candidate(), user=self.staff))
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.overlap_counts(conflicts), {"FACULTY_OVERLAP": 1, "ROOM_OVERLAP": 1})
        self.assertNotIn("DISJOINT-DATES", str(conflicts))

    def test_invalid_candidate_does_not_dereference_peer_assignments(self):
        peer = self.candidate(assignment_id=None)
        conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(
            self.candidate(end_time=None), peers=[peer]))
        self.assertEqual(reads, [])
        self.assertEqual(conflicts, [Conflict("INVALID_TIME_RANGE", "ERROR",
            "Choose a valid weekday and a start time earlier than the end time.",
            "Choose another time or correct the resource configuration.")])

    def test_nonoverlapping_peer_does_not_require_an_assignment_lookup(self):
        peer = self.candidate(assignment_id=None, day_of_week=2)
        conflicts, reads, _ = self.captured(lambda: detect_entry_conflicts(self.candidate(), peers=[peer]))
        self.assertEqual(reads, [])
        self.assertEqual({item.code: item.severity for item in conflicts}, {"ROOM_CAPACITY_WARNING": "WARNING"})

    def test_32_distinct_candidates_have_linear_peer_requirement_reads(self):
        assignments = [self.assignment] + [self.additional_assignment(f"SCALE-{i}") for i in range(31)]
        entries = [self.candidate(assignment=assignment) for assignment in assignments]
        _, small_reads, small_total = self.captured(lambda: self.validate(entries[:4]))
        conflicts, large_reads, large_total = self.captured(lambda: self.validate(entries))
        self.assertEqual(len(small_reads), 4)
        self.assertEqual(len(large_reads), 32)
        self.assertLessEqual(large_total, small_total + 12 * (32 - 4))
        self.assertEqual(self.overlap_counts(conflicts), {"FACULTY_OVERLAP": 496, "ROOM_OVERLAP": 496})

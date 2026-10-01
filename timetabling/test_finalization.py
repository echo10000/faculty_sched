from datetime import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from django.urls import reverse

from accounts.models import AdminProfile
from .models import ActiveSchedule, Schedule, ScheduleApprovalSnapshot, ScheduleEntry
from .mutations import validate_schedule
from .tests import TimetableFixture


class StaffFinalizationTests(TimetableFixture):
    def setUp(self):
        super().setUp()
        self.candidate().save()
        ScheduleEntry.objects.filter(schedule=self.schedule).update(end_time=time(11))
        ScheduleEntry.objects.create(schedule=self.schedule, assignment=self.assignment,
            room=self.room, day_of_week=2, start_time=time(9), end_time=time(12), meeting_type='laboratory')
        self.schedule.refresh_from_db()

    def validate(self, schedule=None):
        schedule = schedule or self.schedule
        report = validate_schedule(user=self.staff, schedule_id=schedule.pk)
        self.assertEqual(report['errors'], 0)
        schedule.refresh_from_db()
        return sorted({c.code for c in report['conflicts'] if c.severity == 'WARNING'})

    def publish(self, schedule=None, codes=None):
        from .workflow import finalize_schedule
        schedule = schedule or self.schedule
        return finalize_schedule(user=self.staff, schedule_id=schedule.pk,
            revision_token=schedule.revision_token, acknowledged_warnings=codes or ())

    def test_staff_can_publish_own_draft_and_revision_keeps_previous_official(self):
        from .workflow import revise_approved_schedule
        Schedule.objects.filter(pk=self.schedule.pk).update(created_by=self.staff)
        codes = self.validate()
        published = self.publish(codes=codes)
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=published)
        self.assertEqual(snapshot.approved_by, self.staff)
        self.assertEqual(snapshot.payload['finalization']['finalized_by_id'], self.staff.pk)
        clone = revise_approved_schedule(user=self.staff, schedule_id=published.pk, revision_token=published.revision_token)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, published.pk)
        self.assertEqual(clone.status, 'draft')
        codes = self.validate(clone)
        self.publish(clone, codes)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, clone.pk)
        self.assertEqual(ScheduleApprovalSnapshot.objects.get(schedule=published).payload, snapshot.payload)

    def test_publication_rejects_unvalidated_and_changed_dependencies(self):
        with self.assertRaisesMessage(ValidationError, 'Validate'):
            self.publish()
        codes = self.validate()
        self.room.capacity += 1
        self.room.save()
        with self.assertRaisesMessage(ValidationError, 'changed'):
            self.publish(codes=codes)
        self.assertFalse(ActiveSchedule.objects.exists())

    def test_incomplete_hours_are_blocking_and_actionable(self):
        ScheduleEntry.objects.filter(schedule=self.schedule, meeting_type='laboratory').delete()
        codes = self.validate()
        with self.assertRaisesMessage(ValidationError, 'hours'):
            self.publish(codes=codes)
        self.assertFalse(ActiveSchedule.objects.exists())

    def test_scope_is_checked_by_publication_service(self):
        from .workflow import finalize_schedule
        outside = Schedule.objects.create(name='Outside', department=self.external, academic_term=self.term)
        with self.assertRaises(Http404):
            finalize_schedule(user=self.staff, schedule_id=outside.pk, revision_token=outside.revision_token)

    def test_faculty_portal_is_own_published_only_and_denies_staff_downloads(self):
        codes = self.validate()
        self.publish(codes=codes)
        user = get_user_model().objects.create_user(username='faculty-reader', password='test-password')
        self.faculty.user = user
        self.faculty.save()
        AdminProfile.objects.create(user=user, role='faculty')
        user.user_permissions.add(*Permission.objects.all())
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('faculty-portal:schedule')).status_code, 200)
        self.assertEqual(self.client.get(reverse('faculty-portal:download', args=['schedule', 'csv'])).status_code, 200)
        self.assertEqual(self.client.get(reverse('timetabling:schedules-detail', args=[self.schedule.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('reporting:index')).status_code, 403)
        for format in ('csv', 'pdf', 'xlsx'):
            self.assertEqual(self.client.get(reverse('reporting:export', args=['official', format]),
                {'academic_term': self.term.pk, 'faculty': self.faculty.pk}).status_code, 403)
        from .workflow import finalize_schedule
        with self.assertRaises(PermissionDenied):
            finalize_schedule(user=user, schedule_id=self.schedule.pk, revision_token=self.schedule.revision_token)

    def test_staff_faculty_link_retains_administrative_access(self):
        self.faculty.user = self.staff
        self.faculty.save()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('faculty-portal:schedule')).status_code, 200)
        self.assertEqual(self.client.get(reverse('timetabling:schedules')).status_code, 200)

    def test_faculty_login_existing_link_and_session_revocation(self):
        user = get_user_model().objects.create_user(username='linked-reader', password='test-password')
        self.faculty.user = user
        self.faculty.save()
        # Existing Faculty.user links work without requiring another profile.
        response = self.client.post(reverse('accounts:login'),
            {'username': user.username, 'password': 'test-password'})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(self.client.get('/dashboard/'), reverse('faculty-portal:schedule'))
        profile = AdminProfile.objects.create(user=user, role='faculty')
        profile.is_enabled = False
        profile.save()
        self.assertEqual(self.client.get(reverse('faculty-portal:schedule')).status_code, 403)
        self.assertEqual(self.client.get(reverse('faculty-portal:download', args=['schedule', 'csv'])).status_code, 403)
        self.client.logout()
        response = self.client.post(reverse('accounts:login'),
            {'username': user.username, 'password': 'test-password'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_another_faculty_cannot_read_published_rows_or_unpublished_term(self):
        self.publish(codes=self.validate())
        other = self.records[self.external.pk]['faculty-management']
        user = get_user_model().objects.create_user(username='other-reader')
        other.user = user
        other.save()
        AdminProfile.objects.create(user=user, role='faculty')
        self.client.force_login(user)
        response = self.client.get(reverse('faculty-portal:schedule'), {'faculty': self.faculty.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['entries'], [])
        self.assertIsNone(response.context['summary'])
        self.assertEqual(self.client.get(reverse('faculty-portal:download', args=['workload', 'csv']),
            {'term': self.term.pk, 'faculty': self.faculty.pk}).status_code, 404)

    def test_admin_can_link_teaching_identity_without_changing_staff_role(self):
        from audit.models import AuditLog
        self.client.force_login(self.admin)
        url = reverse('admin:auth_user_change', args=[self.staff.pk])
        response = self.client.post(url, {'username': self.staff.username, 'is_active': 'on',
            'faculty_record': self.faculty.pk, '_save': 'Save'})
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        self.faculty.refresh_from_db()
        self.assertEqual(self.faculty.user_id, self.staff.pk)
        self.assertEqual(AdminProfile.objects.get(user=self.staff).role, 'staff')
        self.assertTrue(AuditLog.objects.filter(action='account.faculty_linked', actor=self.admin).exists())
        AdminProfile.objects.filter(user=self.staff).update(role='faculty', department=None)
        response = self.client.post(url, {'username': self.staff.username, 'is_active': 'on',
            'faculty_record': '', '_save': 'Save'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('faculty_record', response.context['adminform'].form.errors)
        self.faculty.refresh_from_db()
        self.assertEqual(self.faculty.user_id, self.staff.pk)

    def test_retired_roles_are_not_selectable_and_faculty_requires_link(self):
        self.assertEqual(set(AdminProfile.Role.values), {'super_admin', 'staff', 'faculty'})
        user = get_user_model().objects.create_user(username='unlinked-reader')
        with self.assertRaisesMessage(ValidationError, 'Link this account'):
            AdminProfile.objects.create(user=user, role='faculty')

    def test_scheduling_configuration_and_required_meetings_block_publication(self):
        from .models import SchedulingConfiguration, AssignmentMeetingRequirement
        SchedulingConfiguration.objects.create(department=self.department, academic_term=self.term,
            allowed_weekdays=[1], earliest_start=time(8), latest_end=time(18), slot_increment_minutes=30,
            solver_time_limit_seconds=10, random_seed=1, worker_count=1)
        codes = self.validate()
        with self.assertRaisesMessage(ValidationError, 'SCHEDULING_RULE'):
            self.publish(codes=codes)
        SchedulingConfiguration.objects.all().delete()
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment, meeting_type='lecture',
            meetings_per_week=2, duration_minutes=60)
        from .signatures import dependency_signature
        Schedule.objects.filter(pk=self.schedule.pk).update(validated_signature=dependency_signature(self.schedule))
        self.schedule.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.publish()
        self.assertFalse(ActiveSchedule.objects.exists())

    def test_workload_enforcement_blocks_publication(self):
        from workloads.models import FacultyTermCapacity
        FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term,
            maximum_load=1, enforce_maximum=True)
        codes = self.validate()
        with self.assertRaisesMessage(ValidationError, 'WORKLOAD_RULE'):
            self.publish(codes=codes)
        self.assertFalse(ActiveSchedule.objects.exists())

    def test_conflicts_outside_scope_are_blocking_without_revealing_peer(self):
        from .models import ClassSection, OfferingRequirement
        from workloads.models import FacultySubjectAssignment
        peer = Schedule.objects.create(name='Protected private name', academic_term=self.term, department=self.external)
        offering = self.offerings[self.external.pk]
        section = ClassSection.objects.create(code='EXTERNAL', academic_term=self.term, department=self.external, expected_size=1)
        OfferingRequirement.objects.create(subject_offering=offering, section=section)
        assignment = FacultySubjectAssignment.objects.create(faculty=self.records[self.external.pk]['faculty-management'], subject_offering=offering)
        ScheduleEntry.objects.create(schedule=peer, assignment=assignment, room=self.room,
            day_of_week=1, start_time=time(9), end_time=time(11), meeting_type='lecture')
        # Record current digest even though canonical validation reports the peer.
        from .signatures import dependency_signature
        Schedule.objects.filter(pk=self.schedule.pk).update(validated_signature=dependency_signature(self.schedule))
        self.schedule.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, 'ROOM_OVERLAP') as caught:
            self.publish()
        self.assertNotIn('Protected private name', str(caught.exception))
        self.assertFalse(ActiveSchedule.objects.exists())

    def test_faculty_content_remains_frozen_while_assignments_change(self):
        from .workflow import revise_approved_schedule
        codes = self.validate()
        published = self.publish(codes=codes)
        self.faculty.user = self.staff
        self.faculty.save()
        clone = revise_approved_schedule(user=self.staff, schedule_id=published.pk, revision_token=published.revision_token)
        ScheduleEntry.objects.filter(schedule=clone, meeting_type='lecture').update(start_time=time(13), end_time=time(15))
        type(self.assignment).objects.filter(pk=self.assignment.pk).update(share='0.5')
        from faculty.portal import teaching_records
        records = teaching_records(self.staff)
        self.assertEqual(records['entries'][0]['start_time'], '09:00:00')
        from decimal import Decimal
        self.assertEqual(Decimal(records['summary']['teaching_units']), Decimal(3))

    def test_historical_approval_faculty_load_uses_frozen_assignments_and_labels_policy(self):
        from .workflow import submit_schedule, approve_schedule
        from faculty.portal import teaching_records
        from decimal import Decimal
        codes = self.validate()
        submit_schedule(user=self.chair, schedule_id=self.schedule.pk,
            revision_token=self.schedule.revision_token, acknowledged_warnings=codes)
        self.schedule.refresh_from_db()
        approve_schedule(user=self.dean, schedule_id=self.schedule.pk,
            revision_token=self.schedule.revision_token, acknowledged_warnings=codes)
        original = ScheduleApprovalSnapshot.objects.get(schedule=self.schedule).payload
        self.assertNotIn('workload_summaries', original)
        self.faculty.user = self.staff
        self.faculty.save()
        type(self.assignment).objects.filter(pk=self.assignment.pk).update(share='0.5')
        records = teaching_records(self.staff)
        self.assertTrue(records['historical_policy'])
        self.assertEqual(records['summary']['teaching_units'], Decimal(3))
        self.client.force_login(self.staff)
        response = self.client.get(reverse('faculty-portal:download', args=['workload', 'csv']))
        self.assertContains(response, 'Published assignments; current workload policy')
        self.assertEqual(ScheduleApprovalSnapshot.objects.get(schedule=self.schedule).payload, original)

    def test_finalize_route_post_csrf_and_stale_revision(self):
        from django.test import Client
        self.client.force_login(self.staff)
        url = reverse('timetabling:schedule-finalize', args=[self.schedule.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.staff)
        self.assertEqual(secured.post(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'revision_token': 'stale'}).status_code, 409)

    def test_unassigned_active_class_blocks_publication(self):
        from workloads.models import SubjectOffering
        SubjectOffering.objects.create(subject=self.offering.subject, department=self.department,
            academic_term=self.term, code='UNASSIGNED', lecture_units=1, laboratory_units=0,
            lecture_hours=1, laboratory_hours=0)
        codes = self.validate()
        with self.assertRaisesMessage(ValidationError, 'ASSIGNMENT_INCOMPLETE'):
            self.publish(codes=codes)

    def test_retained_submission_can_revalidate_and_finalize_without_reviewer(self):
        Schedule.objects.filter(pk=self.schedule.pk).update(status='under_review',
            submitted_by=self.staff, submitted_signature='pre-upgrade-signature',
            validated_signature='pre-upgrade-signature')
        self.schedule.refresh_from_db()
        warnings = self.validate()
        self.publish(codes=warnings)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, 'approved')
        self.assertEqual(self.schedule.submitted_by, self.staff)
        self.assertEqual(self.schedule.submitted_signature, 'pre-upgrade-signature')

    def test_failed_publication_keeps_previous_selection_and_history(self):
        from unittest.mock import patch
        from .workflow import revise_approved_schedule
        from .models import OfficialResourceBooking, ScheduleWorkflowEvent
        published = self.publish(codes=self.validate())
        clone = revise_approved_schedule(user=self.staff, schedule_id=published.pk, revision_token=published.revision_token)
        codes = self.validate(clone)
        bookings = list(OfficialResourceBooking.objects.values_list('pk', flat=True))
        with patch('timetabling.workflow.record_event', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.publish(clone, codes)
        self.assertEqual(ActiveSchedule.objects.get().schedule_id, published.pk)
        self.assertEqual(list(OfficialResourceBooking.objects.values_list('pk', flat=True)), bookings)
        self.assertFalse(ScheduleApprovalSnapshot.objects.filter(schedule=clone).exists())
        self.assertFalse(ScheduleWorkflowEvent.objects.filter(schedule=clone, action='finalized').exists())

    def test_faculty_download_formats_and_other_identity_parameters(self):
        self.publish(codes=self.validate())
        person = self.faculty
        person.user = self.staff
        person.save()
        self.client.force_login(self.staff)
        for kind in ('schedule', 'workload'):
            for format in ('pdf', 'xlsx', 'csv'):
                response = self.client.get(reverse('faculty-portal:download', args=[kind, format]),
                    {'faculty': self.records[self.external.pk]['faculty-management'].pk})
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])
                if format == 'csv':
                    self.assertNotIn('Person3', response.content.decode())
        self.assertEqual(self.client.get(reverse('faculty-portal:schedule'), {'term': self.later.pk}).status_code, 404)
        self.assertEqual(self.client.get('/faculty/dashboard/').status_code, 404)
        self.assertEqual(self.client.get('/scheduling/').status_code, 404)


class ConcurrentStaffPublicationTests(TransactionTestCase):
    def setUp(self):
        fixture = type('PublicationFixture', (TimetableFixture,), {})
        fixture.setUpTestData()
        for name, value in vars(fixture).items():
            if not name.startswith('__'):
                setattr(self, name, value)
        ScheduleEntry.objects.create(schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=1, start_time=time(9), end_time=time(11), meeting_type='lecture')
        ScheduleEntry.objects.create(schedule=self.schedule, assignment=self.assignment, room=self.room,
            day_of_week=2, start_time=time(9), end_time=time(12), meeting_type='laboratory')

    def test_simultaneous_publication_only_one_commits(self):
        from .workflow import finalize_schedule
        warnings = [c.code for c in validate_schedule(user=self.staff, schedule_id=self.schedule.pk)['conflicts'] if c.severity == 'WARNING']
        self.schedule.refresh_from_db()
        barrier = Barrier(2)
        def publish(_):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.staff.pk)
                barrier.wait(timeout=15)
                try:
                    finalize_schedule(user=user, schedule_id=self.schedule.pk, revision_token=self.schedule.revision_token, acknowledged_warnings=warnings)
                    return 'published'
                except ValidationError:
                    return 'rejected'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, [0, 1]))
        self.assertCountEqual(results, ['published', 'rejected'])
        self.assertEqual(ActiveSchedule.objects.count(), 1)
        self.assertEqual(ScheduleApprovalSnapshot.objects.count(), 1)

    def test_replacement_drafts_cannot_both_replace_same_official_version(self):
        from .workflow import finalize_schedule, revise_approved_schedule
        warnings = [c.code for c in validate_schedule(user=self.staff, schedule_id=self.schedule.pk)['conflicts'] if c.severity == 'WARNING']
        self.schedule.refresh_from_db()
        official = finalize_schedule(user=self.staff, schedule_id=self.schedule.pk, revision_token=self.schedule.revision_token, acknowledged_warnings=warnings)
        drafts = [revise_approved_schedule(user=self.staff, schedule_id=official.pk, revision_token=official.revision_token) for _ in range(2)]
        for draft in drafts:
            validate_schedule(user=self.staff, schedule_id=draft.pk)
            draft.refresh_from_db()
        barrier = Barrier(2)
        def publish(index):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.staff.pk)
                barrier.wait(timeout=15)
                try:
                    finalize_schedule(user=user, schedule_id=drafts[index].pk, revision_token=drafts[index].revision_token, acknowledged_warnings=warnings)
                    return 'published'
                except ValidationError:
                    return 'stale'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(publish, (0, 1)))
        self.assertCountEqual(outcomes, ['published', 'stale'])
        self.assertEqual(Schedule.objects.filter(pk__in=[d.pk for d in drafts], status='approved').count(), 1)

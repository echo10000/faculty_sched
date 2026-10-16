"""Timetable result interfaces preserve canonical validation, scope and state."""
from datetime import time
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse

from accounts.models import AdminProfile
from audit.models import AuditLog
from resources.test_frontend_design import Markup
from workloads.models import FacultySubjectAssignment, SubjectOffering
from .conflicts import Conflict, get_schedule_conflicts
from .models import AssignmentMeetingRequirement, Schedule, ScheduleApprovalSnapshot, ScheduleEntry, ScheduleGenerationRun
from .presentation import group_findings
from .tests import TimetableFixture


class FindingGroupingTests(SimpleTestCase):
    def test_all_severities_messages_and_remedies_survive_grouping(self):
        original = [Conflict('TYPE_A','WARNING','Warning text','Warning remedy').as_dict(),
                    Conflict('TYPE_B','ERROR','Error text','Error remedy').as_dict(),
                    Conflict('TYPE_C','INFO','Information text','Information remedy').as_dict(),
                    Conflict('TYPE_A','WARNING','Another warning','Another remedy').as_dict()]
        groups=group_findings(original)
        self.assertEqual([g['severity'] for g in groups], ['ERROR','WARNING','INFO'])
        self.assertEqual(sum(len(g['findings']) for g in groups), 4)
        html=render_to_string('timetabling/_conflict_groups.html', {'conflict_groups':groups})
        for row in original:
            self.assertIn(row['message'],html)
            self.assertIn(row['remedy'],html)
            self.assertIn(row['code'],html)
        self.assertEqual(original[0]['severity'],'WARNING')


class TimetableInterfaceTests(TimetableFixture):
    def setUp(self):
        self.client.force_login(self.staff)
        self.filters={'faculty':self.faculty.pk,'room':self.room.pk,'section':self.section.pk,
                      'day_of_week':1,'q':self.offering.subject.code}

    def page(self, name='timetable', **query):
        return self.client.get(reverse('timetabling:'+name,args=[self.schedule.pk]),query)

    def add_run(self, status='PROPOSAL_READY'):
        return ScheduleGenerationRun.objects.create(schedule=self.schedule,academic_term=self.term,
            department=self.department,requested_by=self.staff,strategy='FILL_GAPS',status=status,
            proposed_meeting_count=1,proposed_meetings=[{'assignment_id':self.assignment.pk,
            'room_id':self.room.pk,'day_of_week':1,'start_time':'10:00','end_time':'11:00',
            'meeting_type':'lecture','occurrence_index':1}])

    def test_weekly_meetings_are_readable_sorted_and_day_links_resolve(self):
        later=self.candidate(); later.start_time=time(13); later.end_time=time(14); later.save()
        earlier=self.candidate(); earlier.save()
        page=self.page()
        self.assertEqual(page.status_code,200)
        self.assertEqual([e.pk for e in page.context['week'][0]['entries']],[earlier.pk,later.pk])
        for value in [self.offering.subject.title,self.section.code,str(self.faculty),self.room.code,'Lecture','09:00','10:00']:
            self.assertContains(page,value)
        markup=Markup(page.content.decode())
        for number in range(1,8):
            self.assertEqual(markup.by_id(f'week-day-{number}')['tabindex'],'-1')
            count = len(page.context['week'][number-1]['entries'])
            self.assertIn(('a',{'href':f'#week-day-{number}','aria-label':f'{page.context["week"][number-1]["day"]}: {count} meeting{"" if count == 1 else "s"}'}),markup.elements)
        self.assertContains(page,'empty space do not establish room or instructor availability')

    def test_all_supported_filters_and_schedule_tabs_retain_valid_selection(self):
        self.candidate().save()
        page=self.page(**self.filters)
        self.assertEqual(len(page.context['entries']),1)
        markup=Markup(page.content.decode())
        for route in ('schedules-detail','conflicts','unscheduled','schedule-review','schedule-history'):
            path=reverse('timetabling:'+route,args=[self.schedule.pk])
            link=next(attrs['href'] for tag,attrs in markup.elements if tag=='a' and urlsplit(attrs.get('href','')).path==path)
            self.assertEqual(parse_qs(urlsplit(link).query),{k:[str(v)] for k,v in self.filters.items()})
            self.assertEqual(self.client.get(link).status_code,200)
        changed=self.page(q='no-such-subject')
        self.assertEqual(len(changed.context['entries']),0)
        self.assertContains(changed,'No meetings match this selection')

    def test_forged_filters_never_expose_or_reemit_external_resources(self):
        outside=self.records[self.external.pk]['faculty-management']
        page=self.page(faculty=outside.pk)
        self.assertEqual(page.status_code,200)
        self.assertTrue(page.context['filter_form'].errors)
        self.assertEqual(page.context['entries'],[])
        self.assertEqual(page.context['meeting_filters'],{})
        self.assertNotContains(page,str(outside))

    def test_no_meetings_partial_coverage_and_proposals_are_distinct(self):
        offering=SubjectOffering.objects.create(subject=self.offering.subject,academic_term=self.term,
            department=self.department,code='OTHER',lecture_units=1,laboratory_units=0,lecture_hours=1,laboratory_hours=0)
        no_meetings=FacultySubjectAssignment.objects.create(faculty=self.faculty,subject_offering=offering)
        self.candidate().save()
        self.add_run()
        page=self.page('unscheduled')
        self.assertEqual(list(page.context['unscheduled_assignments']),[no_meetings])
        self.assertEqual(len(page.context['coverage_rows']),1)
        row=page.context['coverage_rows'][0]
        self.assertEqual(row['assignment'],self.assignment)
        self.assertEqual(row['meeting_count'],1)
        self.assertEqual([f['code'] for f in row['findings']],['MEETING_HOURS_WARNING','MEETING_HOURS_WARNING'])
        self.assertContains(page,'Some meetings saved')
        self.assertContains(page,'Generated proposals are separate')
        self.assertContains(page,'Unaccepted generated proposals are not saved draft meetings')
        self.assertEqual(ScheduleEntry.objects.count(),1)

    def test_complete_coverage_is_not_reported_as_partial(self):
        entry=self.candidate(); entry.end_time=time(11); entry.save()
        ScheduleEntry.objects.create(schedule=self.schedule,assignment=self.assignment,room=self.room,
            day_of_week=2,start_time=time(9),end_time=time(12),meeting_type='laboratory')
        page=self.page('unscheduled')
        self.assertEqual(page.context['coverage_rows'],[])
        self.assertEqual(page.context['summary']['without_meetings'],0)
        self.assertContains(page,'No assignments with saved meetings have coverage discrepancies')

    def test_exact_requirements_reuse_canonical_counts_and_durations(self):
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,meeting_type='lecture',meetings_per_week=2,duration_minutes=60)
        AssignmentMeetingRequirement.objects.create(assignment=self.assignment,meeting_type='laboratory',meetings_per_week=1,duration_minutes=180)
        entry=self.candidate(); entry.end_time=time(10,30); entry.save()
        canonical=[c.as_dict() for c in get_schedule_conflicts(self.schedule,user=self.staff)
                   if c.code.startswith('MEETING_REQUIREMENT')]
        page=self.page('unscheduled')
        visible=[{k:v for k,v in row.items() if k!='meetings'} for row in page.context['coverage_rows'][0]['findings']]
        self.assertEqual(visible,canonical)
        self.assertIn('MEETING_REQUIREMENT_DURATION',[r['code'] for r in visible])

    def test_conflicts_keep_canonical_output_and_edit_links(self):
        entry=self.candidate(); entry.save()
        ScheduleEntry.objects.create(schedule=self.schedule,assignment=self.assignment,room=self.room,
            day_of_week=1,start_time=time(9,30),end_time=time(10,30),meeting_type='lecture')
        expected=get_schedule_conflicts(self.schedule,user=self.staff)
        page=self.page('conflicts',**self.filters)
        self.assertEqual([c.as_dict() for c in page.context['report']['conflicts']],[c.as_dict() for c in expected])
        for c in expected:
            self.assertContains(page,c.message)
            self.assertContains(page,c.remedy)
        self.assertContains(page,'Blocking errors')
        self.assertContains(page,reverse('timetabling:entries-edit',args=[self.schedule.pk,entry.pk]))
        self.assertContains(page,'Findings cover the whole version')

    def test_proposal_is_separate_and_filters_return_to_saved_version(self):
        run=self.add_run()
        before=(ScheduleEntry.objects.count(),AuditLog.objects.count())
        page=self.client.get(reverse('timetabling:generation-run-detail',args=[run.pk]),self.filters)
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Acceptance is not publication')
        self.assertContains(page,'Generated proposal Â· awaiting acceptance')
        markup=Markup(page.content.decode())
        path=reverse('timetabling:timetable',args=[self.schedule.pk])
        link=next(a['href'] for t,a in markup.elements if t=='a' and urlsplit(a.get('href','')).path==path)
        self.assertEqual(parse_qs(urlsplit(link).query),{k:[str(v)] for k,v in self.filters.items()})
        self.assertEqual(before,(ScheduleEntry.objects.count(),AuditLog.objects.count()))

    def test_accepted_proposal_is_labeled_captured_not_live_meetings(self):
        run=self.add_run('ACCEPTED')
        page=self.client.get(reverse('timetabling:generation-run-detail',args=[run.pk]))
        self.assertContains(page,'Proposal accepted into draft')
        self.assertContains(page,'may differ from later edits')
        self.assertNotContains(page,'>Accept proposal<')
        self.assertEqual(ScheduleEntry.objects.count(),0)

    def test_published_schedule_disables_editing_and_proposal_acceptance(self):
        run=self.add_run()
        Schedule.objects.filter(pk=self.schedule.pk).update(status='approved')
        page=self.page()
        self.assertContains(page,'Published history')
        self.assertNotContains(page,'>Edit meeting<')
        self.assertNotContains(page,'>Add class meeting<')
        proposal=self.client.get(reverse('timetabling:generation-run-detail',args=[run.pk]))
        self.assertNotContains(proposal,'>Accept proposal<')
        self.assertContains(proposal,'Discard proposal')
        self.assertEqual(self.client.get(reverse('timetabling:entries-add',args=[self.schedule.pk])).status_code,403)

    def test_delete_and_validate_redirects_preserve_filters(self):
        entry=self.candidate(); entry.save()
        from urllib.parse import urlencode
        url=reverse('timetabling:entries-delete',args=[self.schedule.pk,entry.pk])+'?'+urlencode(self.filters)
        confirmation=self.client.get(url)
        self.assertEqual(parse_qs(urlsplit(confirmation.context['list_url']).query),{k:[str(v)] for k,v in self.filters.items()})
        response=self.client.post(url)
        self.assertEqual(response.status_code,302)
        self.assertEqual(parse_qs(urlsplit(response.url).query),{k:[str(v)] for k,v in self.filters.items()})

    def test_external_schedule_and_proposal_remain_inaccessible(self):
        outside=Schedule.objects.create(name='External private timetable',academic_term=self.term,department=self.external)
        run=ScheduleGenerationRun.objects.create(schedule=outside,academic_term=self.term,department=self.external,
            requested_by=self.admin,strategy='FILL_GAPS',status='PROPOSAL_READY')
        for route in ('timetable','conflicts','unscheduled','schedule-review'):
            self.assertEqual(self.client.get(reverse('timetabling:'+route,args=[outside.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('timetabling:generation-run-detail',args=[run.pk])).status_code,404)

    def test_gets_do_not_modify_saved_meetings_state_or_audit(self):
        self.candidate().save()
        before=(list(ScheduleEntry.objects.values()),self.schedule.status,AuditLog.objects.count())
        for mode in ('timetable','schedules-detail','conflicts','unscheduled','schedule-review'):
            self.assertEqual(self.page(mode).status_code,200)
        self.schedule.refresh_from_db()
        self.assertEqual(before,(list(ScheduleEntry.objects.values()),self.schedule.status,AuditLog.objects.count()))

    def test_publication_and_faculty_cards_use_frozen_metadata_without_writing(self):
        from copy import deepcopy
        from .mutations import validate_schedule
        from .workflow import finalize_schedule
        entry=self.candidate(); entry.end_time=time(11); entry.save()
        ScheduleEntry.objects.create(schedule=self.schedule,assignment=self.assignment,room=self.room,
            day_of_week=2,start_time=time(9),end_time=time(12),meeting_type='laboratory')
        report=validate_schedule(user=self.staff,schedule_id=self.schedule.pk)
        self.schedule.refresh_from_db()
        finalize_schedule(user=self.staff,schedule_id=self.schedule.pk,revision_token=self.schedule.revision_token,
                          acknowledged_warnings={c.code for c in report['conflicts'] if c.severity=='WARNING'})
        snapshot=ScheduleApprovalSnapshot.objects.get(schedule=self.schedule)
        saved=deepcopy(snapshot.payload)
        review=self.page('schedule-review')
        self.assertContains(review,'Frozen publication record')
        self.assertContains(review,'Monday')
        self.assertNotContains(review,'>Finalize and Publish</button>')
        reader=get_user_model().objects.create_user(username='interface-faculty-reader')
        self.faculty.user=reader; self.faculty.save()
        AdminProfile.objects.create(user=reader,role='faculty')
        self.offering.subject.title='Changed live subject label'
        self.offering.subject.save()
        self.client.force_login(reader)
        page=self.client.get(reverse('faculty-portal:schedule'),{'term':self.term.pk})
        self.assertEqual(page.status_code,200)
        self.assertNotContains(page,'Changed live subject label')
        self.assertContains(page,self.schedule.name)
        self.assertContains(page,'v1')
        self.assertContains(page,saved['schedule']['department_name'])
        self.assertContains(page,'#official-day-1')
        self.assertNotContains(page,'>Edit meeting<')
        snapshot.refresh_from_db()
        self.assertEqual(saved,snapshot.payload)
        self.assertEqual(self.client.get(reverse('timetabling:timetable',args=[self.schedule.pk])).status_code,403)

    def test_discard_redirect_and_history_return_keep_filters(self):
        from urllib.parse import urlencode
        run=self.add_run()
        url=reverse('timetabling:generation-run-discard',args=[run.pk])+'?'+urlencode(self.filters)
        response=self.client.post(url)
        self.assertEqual(response.status_code,302)
        self.assertEqual(parse_qs(urlsplit(response.url).query),{k:[str(v)] for k,v in self.filters.items()})
        run.refresh_from_db(); self.assertEqual(run.status,'DISCARDED')
        self.assertEqual(ScheduleEntry.objects.count(),0)
        history=self.page('schedule-history',**self.filters)
        links=[a['href'] for t,a in Markup(history.content.decode()).elements if t=='a'
               and urlsplit(a.get('href','')).path==reverse('timetabling:schedule-review',args=[self.schedule.pk])]
        self.assertTrue(links)
        for link in links:
            self.assertEqual(parse_qs(urlsplit(link).query),{k:[str(v)] for k,v in self.filters.items()})

    def test_generation_history_keeps_selected_version_filters_and_scope(self):
        run = self.add_run()
        other = Schedule.objects.create(name='Another version', academic_term=self.term, department=self.department)
        other_run = ScheduleGenerationRun.objects.create(schedule=other, academic_term=self.term,
            department=self.department, requested_by=self.staff, strategy='FILL_GAPS', status='PROPOSAL_READY')
        path = reverse('timetabling:generation-runs')
        page = self.client.get(path, {'schedule': self.schedule.pk, **self.filters})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(list(page.context['page_obj']), [run])
        markup = Markup(page.content.decode())
        for target in (reverse('timetabling:timetable', args=[self.schedule.pk]),
                       reverse('timetabling:generation-run-detail', args=[run.pk])):
            link = next(a['href'] for t, a in markup.elements if t == 'a' and urlsplit(a.get('href', '')).path == target)
            self.assertEqual(parse_qs(urlsplit(link).query), {k: [str(v)] for k, v in self.filters.items()})
        unfiltered = self.client.get(path)
        self.assertIn(other_run, unfiltered.context['page_obj'])
        outside = Schedule.objects.create(name='Private external version', academic_term=self.term, department=self.external)
        self.assertEqual(self.client.get(path, {'schedule': outside.pk}).status_code, 404)

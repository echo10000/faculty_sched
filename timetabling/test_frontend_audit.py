from html.parser import HTMLParser
from urllib.parse import urlsplit
from django.urls import resolve, reverse
from .tests import TimetableFixture

class PageMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links, self.forms = [], []
        self.form = None
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href', '').startswith('/'):
            self.links.append(attrs['href'])
        if tag == 'form':
            self.form = {'method': attrs.get('method', 'get').lower(), 'action': attrs.get('action'), 'csrf': False, 'names': []}
            self.forms.append(self.form)
        if tag == 'input' and self.form is not None:
            self.form['names'].append(attrs.get('name'))
            if attrs.get('name') == 'csrfmiddlewaretoken': self.form['csrf'] = True
        if tag == 'select' and self.form is not None:
            self.form['names'].append(attrs.get('name'))
    def handle_endtag(self, tag):
        if tag == 'form': self.form = None

class FrontendIntegrationAuditTests(TimetableFixture):
    def test_role_pages_links_forms_and_active_navigation(self):
        for user in (self.admin, self.dean, self.chair, self.staff):
            self.client.force_login(user)
            home = self.client.get(reverse('home'))
            roots = [reverse('home')] + [reverse(link['route']) for link in home.context['navigation_links']]
            if user != self.staff:
                roots += [reverse('faculty-management:detail', args=[self.faculty.pk]), reverse('workloads:faculty', args=[self.faculty.pk])]
                roots += [reverse('timetabling:'+name,args=[self.schedule.pk]) for name in ('schedules-detail','timetable','conflicts','unscheduled','schedule-review','schedule-history')]
            visited = set()
            for depth in range(2):
                following = set()
                for url in sorted(set(roots) - visited):
                    if url.startswith('/admin/') or '/export/' in url: continue
                    visited.add(url)
                    with self.subTest(role=user.username, url=url):
                        response = self.client.get(url)
                        self.assertEqual(response.status_code,200, f'{user.username}: {url}')
                        markup = PageMarkup(response.content.decode())
                        for form in markup.forms:
                            if form['method'] == 'post': self.assertTrue(form['csrf'], url)
                            if form['action'] and form['action'].startswith('/'): resolve(urlsplit(form['action']).path)
                        if response.context and response.context.get('navigation_links'):
                            self.assertLessEqual(sum(link['active'] for link in response.context['navigation_links']),1,url)
                        following.update(markup.links)
                roots = following

    def test_prepare_term_form_does_not_submit_stale_schedule(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse('timetabling:prepare'), {'academic_term':self.term.pk})
        forms = PageMarkup(page.content.decode()).forms
        term_form = next(form for form in forms if 'academic_term' in form['names'] and 'schedule' not in form['names'])
        self.assertIsNotNone(term_form)
        changed = self.client.get(reverse('timetabling:prepare'), {'academic_term':self.later.pk})
        self.assertEqual(changed.status_code,200)
        self.assertEqual(changed.context['term'],self.later)

    def test_faculty_navigation_preserves_selected_term(self):
        self.client.force_login(self.chair)
        url=reverse('faculty-management:detail',args=[self.faculty.pk])
        page=self.client.get(url,{'academic_term':self.term.pk})
        self.assertEqual(page.context['term'],self.term)
        self.assertContains(page, reverse('workloads:faculty',args=[self.faculty.pk])+'?academic_term='+str(self.term.pk))
    def test_official_list_uses_approval_snapshot_metadata(self):
        from .conflicts import get_schedule_conflicts
        from .workflow import submit_schedule, approve_schedule
        from .models import ScheduleApprovalSnapshot
        from django.utils.timezone import localtime
        self.candidate().save()
        warnings = sorted({item.code for item in get_schedule_conflicts(self.schedule, user=self.chair) if item.severity == 'WARNING'})
        submit_schedule(user=self.chair, schedule_id=self.schedule.pk, revision_token=self.schedule.revision_token, acknowledged_warnings=warnings)
        approve_schedule(user=self.dean, schedule_id=self.schedule.pk, revision_token=self.schedule.revision_token, acknowledged_warnings=warnings)
        snapshot = ScheduleApprovalSnapshot.objects.get(schedule=self.schedule)
        self.client.force_login(self.chair)
        page = self.client.get(reverse('timetabling:official-schedules'))
        self.assertContains(page, '<td data-label="Published">'+localtime(snapshot.approved_at).strftime('%Y-%m-%d %H:%M')+'<br>'+self.dean.username+'</td>', html=True)

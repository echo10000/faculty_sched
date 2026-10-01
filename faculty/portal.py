"""Read-only teaching records from selected official timetabling snapshots."""
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from accounts.permissions import can_sign_in, faculty_for
from audit.services import record_event
from reporting.exports import render_csv, render_pdf, render_xlsx
from timetabling.models import ActiveSchedule
from timetabling.intervals import DAYS
from workloads.calculation import calculate_workload


def teaching_records(user, term_id=None):
    person = faculty_for(user) if can_sign_in(user) else None
    if person is None:
        raise PermissionDenied('An active linked Faculty record is required.')
    selections = ActiveSchedule.objects.filter(
        schedule__approval_snapshot__payload__entries__contains=[{'faculty_id': person.pk}],
    ).select_related('academic_term', 'schedule__approval_snapshot').order_by('-academic_term__start_date', 'pk')
    # Filter frozen identities, not editable faculty assignments or latest versions.
    published = []
    for selection in selections:
        payload = selection.schedule.approval_snapshot.payload
        entries = [dict(row) for row in payload.get('entries', []) if row.get('faculty_id') == person.pk]
        for row in entries:
            row['day_label'] = dict(DAYS).get(row['day_of_week'], row['day_of_week'])
        if entries:
            published.append((selection, payload, entries))
    terms = list({item[0].academic_term_id: item[0].academic_term for item in published}.values())
    if term_id is not None:
        try:
            selected = next(t for t in terms if t.pk == int(term_id))
        except (ValueError, TypeError, StopIteration):
            raise Http404('No published teaching records for this term.')
    else:
        selected = terms[0] if terms else None
    entries, summaries, assignments = [], [], {}
    for selection, payload, rows in published:
        if selection.academic_term_id != getattr(selected, 'pk', None):
            continue
        entries.extend(rows)
        summary = payload.get('workload_summaries', {}).get(str(person.pk))
        if summary:
            summaries.append(summary)
        for row in rows:
            offering = SimpleNamespace(**{key: Decimal(row[key]) for key in ('lecture_units', 'laboratory_units', 'lecture_hours', 'laboratory_hours')})
            assignments[row['assignment_id']] = SimpleNamespace(subject_offering=offering, share=Decimal(row['assignment_share']))
    summary = summaries[0] if len(summaries) == 1 else None
    if selected and summary is None:
        # Historical approvals lack a frozen load summary. Compute ONLY frozen
        # published assignments using the active workload service and current policy.
        result = calculate_workload(person, selected, assignments_override=list(assignments.values()))
        summary = {key: result[key] for key in ('lecture_units', 'laboratory_units', 'teaching_units', 'teaching_hours', 'assigned_load', 'status')}
    return {'person': person, 'term': selected, 'terms': terms,
            'entries': sorted(entries, key=lambda r: (r['day_of_week'], r['start_time'])),
            'summary': summary, 'historical_policy': bool(selected and len(summaries) != 1)}


@login_required
@never_cache
def schedule(request, kind='schedule'):
    context = teaching_records(request.user, request.GET.get('term'))
    return render(request, 'faculty/teaching_portal.html', {**context, 'kind': kind})


@login_required
@never_cache
def download(request, kind, format):
    context = teaching_records(request.user, request.GET.get('term'))
    if kind not in ('schedule', 'workload') or format not in ('csv', 'pdf', 'xlsx'):
        raise Http404
    report = {'title': 'My Teaching Schedule' if kind == 'schedule' else 'My Teaching Load',
              'source_label': 'Selected official schedule', 'meta': [('Faculty', str(context['person'])), ('Term', str(context['term'] or 'No published schedule'))],
              'filename_base': f'my-teaching-{kind}', 'orientation': 'landscape'}
    if kind == 'schedule':
        report['headers'] = ['Day', 'Start', 'End', 'Subject', 'Title', 'Section', 'Room', 'Meeting']
        report['rows'] = [[r[k] for k in ('day_label', 'start_time', 'end_time', 'subject_code', 'subject_title', 'section_code', 'room_code', 'meeting_type')] for r in context['entries']]
    else:
        report['headers'] = ['Lecture units', 'Laboratory units', 'Teaching units', 'Weekly teaching hours', 'Weighted load', 'Load status']
        report['rows'] = [[context['summary'][key] for key in ('lecture_units', 'laboratory_units', 'teaching_units', 'teaching_hours', 'assigned_load', 'status')]] if context['summary'] else []
        if context['historical_policy']:
            report['source_label'] += '; historical published assignments with current workload policy'
        # CSV has no report metadata, so preserve calculation provenance in a column.
        if format == 'csv':
            report['headers'].append('Calculation basis')
            for row in report['rows']:
                row.append('Published assignments; current workload policy' if context['historical_policy']
                    else 'Saved publication summary')
    renderer, mime = {'csv': (render_csv, 'text/csv; charset=utf-8'), 'pdf': (render_pdf, 'application/pdf'), 'xlsx': (render_xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}[format]
    response = HttpResponse(renderer(report), content_type=mime)
    response['Content-Disposition'] = f'attachment; filename="my-teaching-{kind}.{format}"'
    record_event('faculty.teaching_downloaded', actor=request.user, obj=context['person'],
        details={'kind': kind, 'format': format, 'academic_term_id': getattr(context['term'], 'pk', None)})
    return response

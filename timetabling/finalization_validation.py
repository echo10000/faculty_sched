"""Publication checks layered on the active timetable/workload validators."""
from dataclasses import replace
from decimal import Decimal

from django.core.exceptions import ValidationError

from workloads.calculation import calculate_workload, resolve_policy
from workloads.models import SubjectOffering
from .conflicts import Conflict, get_schedule_conflicts
from .intervals import duration_minutes
from .models import SchedulingConfiguration


def publication_conflicts(schedule, user, *, excluded_schedule_ids=()):
    findings = get_schedule_conflicts(schedule, user=user, excluded_schedule_ids=excluded_schedule_ids)
    findings = [replace(c, severity='ERROR') if c.code == 'MEETING_HOURS_WARNING' else c for c in findings]
    entries = list(schedule.entries.select_related('assignment__faculty__home_department__college', 'assignment__subject_offering'))
    if not entries:
        findings.append(Conflict('EMPTY_SCHEDULE', 'ERROR', 'This version has no meetings.', 'Prepare classes and add or generate their meetings.'))
    offerings = SubjectOffering.objects.filter(academic_term=schedule.academic_term, department=schedule.department, is_active=True).prefetch_related('teaching_assignments')
    for offering in offerings:
        assignments = list(offering.teaching_assignments.all())
        if sum((a.share for a in assignments), Decimal(0)) != 1:
            findings.append(Conflict('ASSIGNMENT_INCOMPLETE', 'ERROR', f'{offering.code}: faculty assignment shares must total 100%.', 'Confirm faculty assignments for this class.'))
        for assignment in assignments:
            for kind in ('lecture', 'laboratory'):
                actual = sum((duration_minutes(e.start_time, e.end_time) / 60 for e in entries
                    if e.assignment_id == assignment.pk and e.meeting_type == kind), Decimal(0))
                required = getattr(offering, f'{kind}_hours') * assignment.share
                if actual != required:
                    findings.append(Conflict('TEACHING_HOURS_INCOMPLETE', 'ERROR', f'{offering.code}: {actual} of {required} weekly {kind} hours recorded.', 'Adjust meetings to cover the confirmed teaching hours.'))
    people = {e.assignment.faculty_id: e.assignment.faculty for e in entries}
    for person in people.values():
        try:
            policy = resolve_policy(person, schedule.academic_term)
            report = calculate_workload(person, schedule.academic_term, policy_override=policy)
            if policy['enforce_maximum'] and (policy['maximum_load'] is None or report['assigned_load'] is None):
                raise ValidationError('Configure maximum load and component weights before publication.')
            if policy['enforce_maximum'] and report['assigned_load'] > policy['maximum_load']:
                raise ValidationError('Teaching load exceeds the enforced maximum; adjust faculty assignments.')
        except ValidationError as error:
            findings.append(Conflict('WORKLOAD_RULE', 'ERROR', f'{person}: {"; ".join(error.messages)}', 'Review faculty capacity and workload policy.'))
    configuration = SchedulingConfiguration.objects.filter(academic_term=schedule.academic_term, department=schedule.department).first()
    if configuration:
        first = configuration.earliest_start.hour * 60 + configuration.earliest_start.minute
        for entry in entries:
            minutes = [t.hour * 60 + t.minute for t in (entry.start_time, entry.end_time)]
            if (entry.day_of_week not in configuration.allowed_weekdays
                or entry.start_time < configuration.earliest_start or entry.end_time > configuration.latest_end
                or any((m-first) % configuration.slot_increment_minutes for m in minutes)
                or any(t.second or t.microsecond for t in (entry.start_time, entry.end_time))):
                findings.append(Conflict('SCHEDULING_RULE', 'ERROR', 'A meeting is outside the allowed days, hours or time grid.', 'Adjust the meeting to the department scheduling configuration.', entry.pk))
    return list(dict.fromkeys(findings))

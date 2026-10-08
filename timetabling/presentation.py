"""Read-only presentation of existing validation findings and meeting filters."""
from urllib.parse import urlencode

from django.urls import reverse

from .forms import TimetableFilter
from .queries import entry_queryset, scoped


def lunch_violations(user, configuration):
    """Visible existing overlaps only; no mutations or protected counts are exposed."""
    if not user.has_perm("timetabling.view_scheduleentry"):
        return None
    if not configuration or not configuration.lunch_break_enabled:
        return {"count": 0, "entries": []}
    entries = scoped(user, entry_queryset()).filter(
        schedule__academic_term_id=configuration.academic_term_id,
        schedule__department_id=configuration.department_id,
        start_time__lt=configuration.lunch_end, end_time__gt=configuration.lunch_start,
    ).order_by("schedule__name", "day_of_week", "start_time", "pk")
    return {"count": entries.count(), "entries": list(entries[:10])}


def meeting_filters(form):
    if not form.is_valid():
        return {}
    result = {}
    for name in ("faculty", "room", "section", "day_of_week", "q"):
        value = form.cleaned_data.get(name)
        if value:
            result[name] = getattr(value, "pk", value)
    return result


def navigation_filters(request):
    return meeting_filters(TimetableFilter(request.GET, user=request.user))


def present_conflicts(user, conflicts, filters=None):
    """Resolve only visible meetings; never change canonical conflict results."""
    conflicts = list(conflicts)
    ids = {pk for item in conflicts for pk in (item.entry_id, item.other_entry_id) if pk}
    entries = {}
    if ids and user.has_perm("timetabling.view_scheduleentry") and user.has_perm("timetabling.view_schedule"):
        entries = {entry.pk: entry for entry in scoped(user, entry_queryset()).filter(pk__in=ids)}
    result = []
    for item in conflicts:
        row = item.as_dict()
        row["meetings"] = []
        for pk in dict.fromkeys((item.entry_id, item.other_entry_id)):
            entry = entries.get(pk)
            if not entry:
                continue
            editable = (entry.schedule.status in ("draft", "validated", "needs_revision")
                        and user.has_perm("timetabling.change_scheduleentry"))
            url = (reverse("timetabling:entries-edit", args=[entry.schedule_id, entry.pk]) if editable
                   else reverse("timetabling:schedules-detail", args=[entry.schedule_id]))
            if filters:
                url += "?" + urlencode(filters)
            if not editable:
                url += f"#meeting-{entry.pk}"
            row["meetings"].append({
                "label": f"{entry.offering.subject.code} · {entry.faculty} · {entry.get_day_of_week_display()} "
                         f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M} · {entry.room.code}",
                "url": url, "action": "Edit meeting" if editable else "View meeting",
            })
        result.append(row)
    return result

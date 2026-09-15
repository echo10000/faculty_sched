from .intervals import DAYS
from .queries import entry_queryset, scoped
from .models import OfferingRequirement


def filtered_entries(user, schedule, form):
    if not form.is_valid():
        return []
    qs = scoped(user, entry_queryset()).filter(schedule=schedule)
    data = form.cleaned_data
    for field, lookup in [("academic_term", "schedule__academic_term"), ("department", "schedule__department"), ("faculty", "assignment__faculty"), ("room", "room"), ("day_of_week", "day_of_week")]:
        if data.get(field):
            qs = qs.filter(**{lookup: data[field]})
    if data.get("section"):
        qs = qs.filter(assignment__subject_offering__scheduling_requirement__section=data["section"])
    if data.get("q"):
        from django.db.models import Q
        qs = qs.filter(Q(assignment__subject_offering__subject__code__icontains=data["q"]) | Q(assignment__subject_offering__subject__title__icontains=data["q"]))
    entries = list(qs)
    requirements = {r.subject_offering_id: r for r in OfferingRequirement.objects.select_related("section")}
    for entry in entries:
        entry.section_label = requirements[entry.offering.pk].section.code if entry.offering.pk in requirements else "Not configured"
    return entries


def week_columns(entries):
    return [{"day": label, "entries": [entry for entry in entries if entry.day_of_week == number]} for number, label in DAYS]

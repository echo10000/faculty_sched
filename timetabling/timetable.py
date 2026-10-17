from .intervals import DAYS
from .queries import entry_queryset, scoped
from .models import OfferingRequirement, Schedule, ScheduleApprovalSnapshot


NOT_RECORDED = "Not recorded at publication"


def publication_payload(schedule):
    """Read only this version's snapshot; an absent legacy snapshot is unknown."""
    if schedule.status != Schedule.Status.APPROVED:
        return None
    try:
        payload = schedule.approval_snapshot.payload
    except ScheduleApprovalSnapshot.DoesNotExist:
        return {}
    return payload if isinstance(payload, dict) else {}


def schedule_labels(schedule):
    payload = publication_payload(schedule)
    if payload is None:
        return {"name": schedule.name, "family_name": schedule.family.name,
                "academic_term_code": str(schedule.academic_term), "department_name": str(schedule.department)}
    metadata = payload.get("schedule", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    return {key: metadata.get(key) or NOT_RECORDED for key in
            ("name", "family_name", "academic_term_code", "department_name")}


def publication_rows(schedule):
    payload = publication_payload(schedule)
    rows = payload.get("entries", []) if payload is not None else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def frozen_filter_labels(form, schedule):
    """Keep authorized choice identities, replacing their published labels only."""
    if publication_payload(schedule) is None:
        return
    rows = publication_rows(schedule)
    labels = schedule_labels(schedule)
    mappings = {"academic_term": {schedule.academic_term_id: labels["academic_term_code"]},
                "department": {schedule.department_id: labels["department_name"]}}
    for field, id_key, label_key in (("faculty", "faculty_id", "faculty_name"),
                                      ("room", "room_id", "room_code"),
                                      ("section", "section_id", "section_code")):
        mappings[field] = {row.get(id_key): row.get(label_key) or NOT_RECORDED for row in rows}
    for name, mapping in mappings.items():
        field = form.fields[name]
        original = field.label_from_instance
        field.label_from_instance = lambda obj, labels=mapping, fallback=original: (
            labels[obj.pk] if obj.pk in labels else f"{fallback(obj)} (current record)")


def filtered_entries(user, schedule, form):
    if not form.is_valid():
        return []
    qs = scoped(user, entry_queryset()).filter(schedule=schedule)
    data = form.cleaned_data
    payload = publication_payload(schedule)
    if payload is not None:
        # Scope and current record visibility remain the authorization boundary.
        # Selection/search then use immutable published identities and labels.
        rows = {row.get("entry_id"): row for row in publication_rows(schedule)}
        entries = []
        for entry in qs:
            row = rows.get(entry.pk, {})
            metadata = payload.get("schedule", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            identities = {"academic_term": metadata.get("academic_term_id"),
                          "department": metadata.get("department_id"),
                          "faculty": row.get("faculty_id"), "room": row.get("room_id"),
                          "section": row.get("section_id")}
            if any(data.get(name) and data[name].pk != identity for name, identity in identities.items()):
                continue
            if data.get("day_of_week") and data["day_of_week"] != row.get("day_of_week"):
                continue
            query = (data.get("q") or "").casefold()
            if query and not any(query in str(row.get(key, "")).casefold() for key in ("subject_code", "subject_title")):
                continue
            for target, key in (("subject_code_label", "subject_code"), ("subject_title_label", "subject_title"),
                                ("faculty_label", "faculty_name"), ("room_label", "room_code"),
                                ("section_label", "section_code")):
                setattr(entry, target, row.get(key) or NOT_RECORDED)
            entry.subject_label = f"{entry.subject_code_label} - {entry.subject_title_label}"
            entries.append(entry)
        return entries
    for field, lookup in [("academic_term", "schedule__academic_term"), ("department", "schedule__department"), ("faculty", "assignment__faculty"), ("room", "room"), ("day_of_week", "day_of_week")]:
        if data.get(field):
            qs = qs.filter(**{lookup: data[field]})
    if data.get("section"):
        qs = qs.filter(assignment__subject_offering__scheduling_requirement__section=data["section"])
    if data.get("q"):
        from django.db.models import Q
        qs = qs.filter(Q(assignment__subject_offering__subject__code__icontains=data["q"]) | Q(assignment__subject_offering__subject__title__icontains=data["q"]))
    entries = list(qs)
    requirements = {r.subject_offering_id: r for r in scoped(user, OfferingRequirement.objects.filter(
        subject_offering_id__in=[entry.offering.pk for entry in entries],
    )).select_related("section")}
    for entry in entries:
        entry.section_label = requirements[entry.offering.pk].section.code if entry.offering.pk in requirements else "Not configured"
        entry.subject_code_label = entry.offering.subject.code
        entry.subject_title_label = entry.offering.subject.title
        entry.subject_label = str(entry.offering.subject)
        entry.faculty_label = str(entry.faculty)
        entry.room_label = entry.room.code or entry.room.name
    return entries


def week_columns(entries):
    return [{"number": number, "day": label, "entries": sorted(
        (entry for entry in entries if entry.day_of_week == number),
        key=lambda entry: (entry.start_time, entry.end_time, getattr(entry, "pk", 0) or 0),
    )} for number, label in DAYS]

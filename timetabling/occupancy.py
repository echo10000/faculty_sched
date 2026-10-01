from collections.abc import Collection
from django.db.models import Q

from .models import Schedule, ScheduleEntry
from .queries import entry_queryset


def authoritative_occupancy(
    schedule: Schedule,
    excluded_entry_ids: Collection[int] = (),
    excluded_schedule_ids: Collection[int] = (),
) -> tuple[ScheduleEntry, ...]:
    return tuple(
        entry_queryset()
        .filter(
            schedule__academic_term__start_date__lte=schedule.academic_term.end_date,
            schedule__academic_term__end_date__gte=schedule.academic_term.start_date,
        )
        # Alternative versions of one logical schedule may overlap. Existing
        # independent workspaces retain the Phase 4 peer-validation behavior.
        .filter(Q(schedule_id=schedule.pk) | ~Q(schedule__family_id=schedule.family_id))
        # Retired published versions remain historical records, not bookings.
        .exclude(Q(schedule__status='approved') & Q(schedule__active_selections__isnull=True))
        .exclude(pk__in=tuple(excluded_entry_ids))
        .exclude(schedule_id__in=tuple(excluded_schedule_ids))
        .order_by("day_of_week", "start_time", "pk")
    )

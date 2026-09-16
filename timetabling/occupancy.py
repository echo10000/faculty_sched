from collections.abc import Collection

from .models import Schedule, ScheduleEntry
from .queries import entry_queryset


def authoritative_occupancy(
    schedule: Schedule,
    excluded_entry_ids: Collection[int] = (),
) -> tuple[ScheduleEntry, ...]:
    return tuple(
        entry_queryset()
        .filter(
            schedule__academic_term__start_date__lte=schedule.academic_term.end_date,
            schedule__academic_term__end_date__gte=schedule.academic_term.start_date,
        )
        .exclude(pk__in=tuple(excluded_entry_ids))
        .order_by("day_of_week", "start_time", "pk")
    )

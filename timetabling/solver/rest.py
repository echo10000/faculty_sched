"""Exact class boundaries for faculty rest, independent of daily idle-slot totals."""
from bisect import bisect_right
from collections import Counter, defaultdict


MINUTE_MICROSECONDS = 60 * 1000000


def boundary_microsecond(policy, slot):
    return (policy.earliest_minute + slot * policy.slot_increment_minutes) * MINUTE_MICROSECONDS


def fixed_boundaries(policy, fixed):
    return (
        fixed.actual_start_microsecond if fixed.actual_start_microsecond is not None
        else boundary_microsecond(policy, fixed.start_slot),
        fixed.actual_end_microsecond if fixed.actual_end_microsecond is not None
        else boundary_microsecond(policy, fixed.end_slot),
    )


def faculty_rest_events(data):
    """Aggregate room/placement alternatives into starts and ends, without pair expansion.

    A selected start violates rest iff a selected end is in (start-rest, start].
    With hard non-overlap, that is exactly the preceding class boundary, regardless
    of section. Protected peers use their actual timestamps, not occupancy rounding.
    Only faculty involved in this generation are scored; unrelated peers aren't.
    """
    faculty = {item.faculty_id for item in data.demands}
    faculty.update(item.faculty_id for item in data.fixed_meetings if item.counts_for_distribution)
    starts, ends = defaultdict(list), defaultdict(list)
    fixed_starts, fixed_ends = Counter(), set()
    for index, item in enumerate(data.candidates):
        group = (item.faculty_id, item.day_of_week)
        starts[(*group, boundary_microsecond(data.policy, item.start_slot))].append(index)
        ends[(*group, boundary_microsecond(data.policy, item.end_slot))].append(index)
    for item in data.fixed_meetings:
        if item.faculty_id not in faculty:
            continue
        start, end = fixed_boundaries(data.policy, item)
        group = (item.faculty_id, item.day_of_week)
        fixed_starts[(*group, start)] += 1
        fixed_ends.add((*group, end))
    return starts, ends, fixed_starts, fixed_ends


def recent_end_keys(starts, ends, minutes):
    grouped = defaultdict(list)
    for faculty, day, end in ends:
        grouped[(faculty, day)].append(end)
    grouped = {group: sorted(times) for group, times in grouped.items()}
    interval = minutes * MINUTE_MICROSECONDS
    for key in sorted(starts):
        faculty, day, start = key
        times = grouped.get((faculty, day), [])
        nearby = times[bisect_right(times, start - interval):bisect_right(times, start)]
        yield key, tuple((faculty, day, end) for end in nearby)


def faculty_rest_penalty(data, selected):
    if not data.policy.faculty_rest_minutes:
        return 0
    # Score only selected placements plus fixed meetings using the same boundaries.
    from dataclasses import replace
    starts, ends, fixed_starts, fixed_ends = faculty_rest_events(replace(data, candidates=selected))
    return sum((len(starts.get(key, ())) + fixed_starts[key])
               for key, nearby in recent_end_keys(starts.keys() | fixed_starts.keys(),
                   ends.keys() | fixed_ends, data.policy.faculty_rest_minutes) if nearby)

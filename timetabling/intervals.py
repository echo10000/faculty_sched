"""Half-open weekly intervals shared by manual validation and future generators."""
from datetime import timedelta
from decimal import Decimal

DAYS = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"), (5, "Friday"), (6, "Saturday"), (7, "Sunday")]


def overlaps(start, end, other_start, other_end):
    return start < other_end and other_start < end


def duration_minutes(start, end):
    def seconds(value):
        return Decimal(value.hour * 3600 + value.minute * 60 + value.second) + Decimal(value.microsecond) / 1000000
    return (seconds(end) - seconds(start)) / 60


def terms_share_weekday(first, second, weekday):
    start, end = max(first.start_date, second.start_date), min(first.end_date, second.end_date)
    return start + timedelta(days=(weekday - start.isoweekday()) % 7) <= end

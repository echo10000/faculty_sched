"""Shared half-open lunch interval rule for candidates and canonical validation."""
from .intervals import overlaps


def lunch_overlaps(start, end, lunch_start, lunch_end):
    return (lunch_start is not None and lunch_end is not None
            and overlaps(start, end, lunch_start, lunch_end))


def policy_lunch_overlaps(policy, start_slot, end_slot):
    return lunch_overlaps(
        policy.earliest_minute + start_slot * policy.slot_increment_minutes,
        policy.earliest_minute + end_slot * policy.slot_increment_minutes,
        policy.lunch_start_minute, policy.lunch_end_minute,
    )

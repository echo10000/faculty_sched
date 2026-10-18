"""Shared room checks for generation inputs, candidates and read-only explanations.

These checks describe suitability only. Occupancy and interval checks remain in
the existing candidate builder and conflict validator.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RoomRequirement:
    type_id: int | None
    code: str | None
    name: str | None
    mandatory: bool


def resolve_room_requirement(offering, requirement, active_room_types):
    explicit = requirement.room_type if requirement and requirement.room_type_id else None
    code = explicit.code if explicit else offering.subject.required_room_type
    resolved = explicit or active_room_types.get(code)
    return RoomRequirement(
        resolved.pk if resolved else None, code,
        resolved.name if resolved else code,
        requirement.room_type_mandatory if explicit else bool(code),
    )


def room_resource_failures(room):
    """Additional generator checks after available_resources applies active ownership."""
    failures = []
    if room.category_id and not room.category.is_active:
        failures.append("Room specialization is inactive")
    if room.building_id and not room.building.is_active:
        failures.append("Building is inactive")
    return failures


def room_fit_failures(*, room_type_id, capacity, required_room_type_id,
                      room_type_mandatory, expected_size, capacity_is_hard):
    """Return the same hard type/capacity exclusions used by candidate expansion."""
    failures = []
    if room_type_mandatory and room_type_id != required_room_type_id:
        failures.append("type")
    if capacity_is_hard and expected_size is not None and capacity < expected_size:
        failures.append("capacity")
    return failures

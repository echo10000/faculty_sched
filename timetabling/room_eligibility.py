"""Read-only suitability explanations using the generator's shared room rules."""
from resources.models import RoomType
from resources.selectors import available_resources, scope_resources
from scheduling.models import Room

from .room_rules import resolve_room_requirement, room_resource_failures, room_fit_failures


PREVIEW_PERMISSIONS = (
    "academics.view_academicterm", "academics.view_subject",
    "workloads.view_subjectoffering", "timetabling.view_offeringrequirement",
    "timetabling.view_classsection", "scheduling.view_room",
)


def can_preview_rooms(user):
    return all(user.has_perm(permission) for permission in PREVIEW_PERMISSIONS)


def room_eligibility_preview(user, offering, requirement):
    """Inputs are authorized scoped records selected by the view, never browser claims."""
    active_types = {item.code: item for item in RoomType.objects.filter(is_active=True)}
    need = resolve_room_requirement(offering, requirement, active_types)
    section = requirement.section if requirement else None
    expected_size = section.expected_size if section else None
    hard_capacity = bool(requirement and requirement.capacity_is_hard)
    blockers = []
    if not requirement:
        blockers.append("Link this class to a section in class scheduling requirements before comparing rooms.")
    else:
        if not section.is_active:
            blockers.append("The class section is inactive. Review its active status.")
        if (section.academic_term_id, section.department_id) != (offering.academic_term_id, offering.department_id):
            blockers.append("The section must match the class term and department.")
        if section.program_id and section.program.department_id != section.department_id:
            blockers.append("The section program must belong to its department.")
        if requirement.room_type_id and not requirement.room_type.is_active:
            blockers.append("The selected room specialization is inactive. Choose an active room type.")
    if need.mandatory and need.type_id is None:
        blockers.append(f"Mandatory room specialization '{need.name}' has no active configured room type. Ask a room-type administrator to review it.")

    available_ids = set(available_resources(user, Room).values_list("pk", flat=True))
    rooms = scope_resources(user, Room.objects.all()).select_related(
        "category", "building", "owner_department__college", "owner_college").order_by("name", "pk")
    rows = []
    for room in rooms:
        failures = room_resource_failures(room)
        if room.pk not in available_ids:
            if not room.is_active:
                failures.append("Room is inactive")
            else:
                failures.append("Owning department or college is inactive")
        failures.extend(blockers)
        fit_failures = room_fit_failures(
            room_type_id=room.category_id, capacity=room.capacity,
            required_room_type_id=need.type_id, room_type_mandatory=need.mandatory,
            expected_size=expected_size, capacity_is_hard=hard_capacity,
        )
        warnings, reasons = [], []
        if not need.code:
            reasons.append("No room specialization required")
        elif need.type_id is not None:
            if "type" in fit_failures:
                failures.append(f"Mandatory specialization: {need.name}; this room does not match")
            elif room.category_id != need.type_id:
                warnings.append(f"Preferred specialization: {need.name}; this room does not match")
            else:
                reasons.append(f"Matches {'mandatory' if need.mandatory else 'preferred'} specialization: {need.name}")
        if "capacity" in fit_failures:
            failures.append(f"Mandatory capacity: {expected_size} students; room holds {room.capacity}")
        elif expected_size is None:
            warnings.append("Class size not recorded; capacity cannot be verified")
        elif room.capacity < expected_size:
            warnings.append(f"Capacity warning: {expected_size} students; room holds {room.capacity}. Capacity is not mandatory")
        else:
            reasons.append(f"Capacity fits {expected_size} students ({room.capacity} seats)")
        if not failures:
            reasons.append("Active room, type, building and ownership checks passed in your authorized scope")
        rows.append({"room": room, "eligible": not failures, "failures": failures,
                     "warnings": warnings, "reasons": reasons})
    rows.sort(key=lambda row: (not row["eligible"], bool(row["warnings"]), row["room"].name))
    return {"offering": offering, "requirement": requirement, "section": section,
            "need": need, "expected_size": expected_size, "hard_capacity": hard_capacity,
            "blockers": blockers, "rooms": rows,
            "eligible_count": sum(row["eligible"] for row in rows)}

from django.core.exceptions import ValidationError

from .models import Assignment


def _overlapping_assignments(queryset, term, time_slot, exclude_assignment_id):
    conflicts = queryset.filter(
        term=term,
        day_of_week=time_slot.day_of_week,
        start_time__lt=time_slot.end_time,
        end_time__gt=time_slot.start_time,
    )
    if exclude_assignment_id is not None:
        conflicts = conflicts.exclude(pk=exclude_assignment_id)
    return conflicts.select_related("subject", "block", "room", "faculty").order_by("start_time")


def validate_assignment(
    faculty,
    subject,
    block,
    room,
    term,
    time_slot,
    units_credited,
    exclude_assignment_id=None,
):
    """Raise a specific ValidationError when an assignment is not schedulable."""
    del units_credited  # Explicit input for the service contract; checked by field validation.

    faculty_conflict = _overlapping_assignments(
        Assignment.objects.filter(faculty=faculty), term, time_slot, exclude_assignment_id
    ).first()
    if faculty_conflict:
        raise ValidationError(
            f"Faculty {faculty} is already assigned to {faculty_conflict.subject.code} "
            f"on {time_slot.day_of_week} {faculty_conflict.start_time:%H:%M}-"
            f"{faculty_conflict.end_time:%H:%M} for block {faculty_conflict.block}."
        )

    room_conflict = _overlapping_assignments(
        Assignment.objects.filter(room=room), term, time_slot, exclude_assignment_id
    ).first()
    if room_conflict:
        raise ValidationError(
            f"Room {room.name} is already booked for {room_conflict.subject.code} "
            f"on {time_slot.day_of_week} {room_conflict.start_time:%H:%M}-"
            f"{room_conflict.end_time:%H:%M} for block {room_conflict.block}."
        )

    block_conflict = _overlapping_assignments(
        Assignment.objects.filter(block=block), term, time_slot, exclude_assignment_id
    ).first()
    if block_conflict:
        raise ValidationError(
            f"Block {block} already has {block_conflict.subject.code} on "
            f"{time_slot.day_of_week} {block_conflict.start_time:%H:%M}-"
            f"{block_conflict.end_time:%H:%M}."
        )

    if not faculty.qualifications.filter(subject=subject).exists():
        raise ValidationError(f"Faculty {faculty} is not qualified to teach {subject.code}.")

    if subject.required_room_type and room.room_type != subject.required_room_type:
        raise ValidationError(
            f"Subject {subject.code} requires a {subject.get_required_room_type_display()} room."
        )

    program_department = block.curriculum.program.department
    if room.restricted_to_department_id and room.restricted_to_department_id != program_department.id:
        raise ValidationError(
            f"Room {room.name} is restricted to department {room.restricted_to_department}."
        )

from django.db import migrations


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Faculty = apps.get_model("faculty", "Faculty")
    EmploymentCategory = apps.get_model("faculty", "EmploymentCategory")
    Room = apps.get_model("scheduling", "Room")
    RoomType = apps.get_model("resources", "RoomType")
    for value in Faculty.objects.using(alias).exclude(employment_type="").values_list("employment_type", flat=True).distinct():
        category, _ = EmploymentCategory.objects.using(alias).get_or_create(code=value, defaults={"name": value.replace("_", " ").title()})
        Faculty.objects.using(alias).filter(employment_type=value, employment_category__isnull=True).update(employment_category=category)
    for value in Room.objects.using(alias).exclude(room_type="").values_list("room_type", flat=True).distinct():
        category, _ = RoomType.objects.using(alias).get_or_create(code=value, defaults={"name": value.replace("_", " ").title()})
        Room.objects.using(alias).filter(room_type=value, category__isnull=True).update(category=category)
    for room in Room.objects.using(alias).all().iterator():
        updates = {}
        if not room.code:
            updates["code"] = f"LEGACY-{room.pk}"
        if room.restricted_to_department_id and not room.owner_college_id and not room.owner_department_id:
            updates["owner_department_id"] = room.restricted_to_department_id
        if updates:
            Room.objects.using(alias).filter(pk=room.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [
        ("resources", "0001_initial"),
        ("faculty", "0002_alter_faculty_options_faculty_contact_number_and_more"),
        ("scheduling", "0006_alter_room_options_room_building_room_category_and_more"),
    ]
    # Reverse keeps useful backfilled data; it never deletes user master records.
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0004_assignment_approved_at_assignment_approved_by_and_more"),
    ]

    operations = [
        # Existing logs were created exclusively by the approved-to-draft unlock path.
        migrations.AddField(
            model_name="assignmentstatuslog",
            name="old_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending_approval", "Pending approval"),
                    ("approved", "Approved"),
                ],
                default="approved",
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="assignmentstatuslog",
            name="new_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending_approval", "Pending approval"),
                    ("approved", "Approved"),
                ],
                default="draft",
                max_length=20,
            ),
            preserve_default=False,
        ),
    ]

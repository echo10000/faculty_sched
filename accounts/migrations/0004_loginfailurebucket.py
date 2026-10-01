from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_adminprofile_college_adminprofile_created_at_and_more")]

    operations = [
        migrations.CreateModel(
            name="LoginFailureBucket",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("failures", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
            options={"default_permissions": ()},
        ),
    ]

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0002_alter_college_options_college_created_by_and_more")]

    operations = [
        migrations.AlterModelOptions(
            name="college",
            options={
                "ordering": ["name"],
                "permissions": [
                    ("view_dashboard", "Can access the dashboard"),
                    ("export_report", "Can export reports"),
                ],
            },
        ),
    ]

from django.db import migrations


# Phase 5 uses this statement-level lock to serialize source capture and
# acceptance with writes to scheduling dependencies. These workload tables
# participate in the same snapshot and must acquire the same lock.
TRIGGER_NAME = "timetabling_scheduling_dependency_lock"
TABLES = (
    "workloads_workloadpolicy",
    "workloads_facultytermcapacity",
    "faculty_facultyqualification",
)

CREATE_SQL = "\n".join(
    f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
FOR EACH STATEMENT EXECUTE FUNCTION timetabling_acquire_scheduling_lock();
"""
    for table_name in TABLES
)
REVERSE_SQL = "\n".join(
    f"DROP TRIGGER {TRIGGER_NAME} ON {table_name};" for table_name in TABLES
)


class Migration(migrations.Migration):
    dependencies = [
        ("workloads", "0003_phase6_workload_recommendation_run"),
        ("timetabling", "0003_scheduling_dependency_lock_triggers"),
    ]

    operations = [migrations.RunSQL(CREATE_SQL, REVERSE_SQL)]

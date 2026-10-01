from django.db import migrations


TRIGGER_NAME = "timetabling_scheduling_dependency_lock"
TABLES = (
    "academics_academicyear",
    "academics_academicterm",
    "academics_semester",
    "academics_subject",
    "core_college",
    "core_department",
    "faculty_faculty",
    "resources_roomtype",
    "resources_building",
    "scheduling_room",
    "workloads_facultyavailability",
    "workloads_subjectoffering",
    "workloads_facultysubjectassignment",
    "timetabling_classsection",
    "timetabling_offeringrequirement",
    "timetabling_roomunavailability",
    "timetabling_schedule",
    "timetabling_scheduleentry",
    "timetabling_schedulingconfiguration",
    "timetabling_assignmentmeetingrequirement",
)

CREATE_SQL = """
CREATE OR REPLACE FUNCTION timetabling_acquire_scheduling_lock()
RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(74190304);
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""" + "\n".join(
    f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
FOR EACH STATEMENT EXECUTE FUNCTION timetabling_acquire_scheduling_lock();
"""
    for table_name in TABLES
)

REVERSE_SQL = "\n".join(
    f"DROP TRIGGER {TRIGGER_NAME} ON {table_name};" for table_name in TABLES
) + "\nDROP FUNCTION timetabling_acquire_scheduling_lock();"


class Migration(migrations.Migration):
    dependencies = [("timetabling", "0002_phase5_generation")]

    operations = [migrations.RunSQL(CREATE_SQL, REVERSE_SQL)]

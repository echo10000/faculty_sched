"""Backfill version identity without inventing any review or official history."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_schedule_families(apps, schema_editor):
    Schedule = apps.get_model("timetabling", "Schedule")
    ScheduleFamily = apps.get_model("timetabling", "ScheduleFamily")
    database = schema_editor.connection.alias
    for schedule in Schedule.objects.using(database).order_by("pk").iterator():
        if schedule.family_id:
            continue
        family = ScheduleFamily.objects.using(database).create(
            academic_term_id=schedule.academic_term_id,
            department_id=schedule.department_id,
            name=schedule.name,
            created_by_id=schedule.created_by_id,
            created_at=schedule.created_at,
        )
        Schedule.objects.using(database).filter(pk=schedule.pk).update(
            family_id=family.pk,
            version_number=1,
            revision_token=uuid.uuid4().hex,
        )


INTEGRITY_SQL = """
CREATE FUNCTION timetabling_prevent_approved_schedule_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'approved' THEN
        RAISE EXCEPTION 'Approved schedule versions are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_approved_schedule_immutable
BEFORE UPDATE OR DELETE ON timetabling_schedule
FOR EACH ROW EXECUTE FUNCTION timetabling_prevent_approved_schedule_mutation();

CREATE FUNCTION timetabling_prevent_approved_entry_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' AND EXISTS (
        SELECT 1 FROM timetabling_schedule WHERE id = OLD.schedule_id AND status = 'approved'
    ) THEN
        RAISE EXCEPTION 'Approved schedule entries are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP <> 'DELETE' AND EXISTS (
        SELECT 1 FROM timetabling_schedule WHERE id = NEW.schedule_id AND status = 'approved'
    ) THEN
        RAISE EXCEPTION 'Approved schedule entries are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_approved_entry_immutable
BEFORE INSERT OR UPDATE OR DELETE ON timetabling_scheduleentry
FOR EACH ROW EXECUTE FUNCTION timetabling_prevent_approved_entry_mutation();

CREATE FUNCTION timetabling_prevent_workflow_history_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Schedule review history is immutable' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER timetabling_workflow_history_immutable
BEFORE UPDATE OR DELETE ON timetabling_scheduleworkflowevent
FOR EACH ROW EXECUTE FUNCTION timetabling_prevent_workflow_history_mutation();
CREATE TRIGGER timetabling_approval_snapshot_immutable
BEFORE UPDATE OR DELETE ON timetabling_scheduleapprovalsnapshot
FOR EACH ROW EXECUTE FUNCTION timetabling_prevent_workflow_history_mutation();

CREATE FUNCTION timetabling_check_active_schedule()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Active official selection cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM timetabling_schedule AS s
        WHERE s.id = NEW.schedule_id
          AND s.academic_term_id = NEW.academic_term_id
          AND s.department_id = NEW.department_id
          AND s.status = 'approved'
    ) THEN
        RAISE EXCEPTION 'Active official selection requires an approved schedule in the same term and department'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_active_schedule_valid
BEFORE INSERT OR UPDATE OR DELETE ON timetabling_activeschedule
FOR EACH ROW EXECUTE FUNCTION timetabling_check_active_schedule();

CREATE FUNCTION timetabling_check_official_booking()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_faculty bigint;
    expected_room bigint;
    expected_section bigint;
    expected_start time;
    expected_end time;
    expected_day smallint;
    term_start date;
    term_end date;
    version_status varchar;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'Official bookings cannot be edited' USING ERRCODE = '23514';
    END IF;
    SELECT a.faculty_id, e.room_id, req.section_id, e.start_time, e.end_time,
           e.day_of_week, term.start_date, term.end_date, schedule.status
      INTO expected_faculty, expected_room, expected_section, expected_start,
           expected_end, expected_day, term_start, term_end, version_status
      FROM timetabling_scheduleentry AS e
      JOIN timetabling_schedule AS schedule ON schedule.id = e.schedule_id
      JOIN academics_academicterm AS term ON term.id = schedule.academic_term_id
      JOIN workloads_facultysubjectassignment AS a ON a.id = e.assignment_id
      JOIN timetabling_offeringrequirement AS req
        ON req.subject_offering_id = a.subject_offering_id
     WHERE e.id = NEW.schedule_entry_id;
    IF NOT FOUND OR version_status <> 'approved'
       OR NEW.faculty_id IS DISTINCT FROM expected_faculty
       OR NEW.room_id IS DISTINCT FROM expected_room
       OR NEW.section_id IS DISTINCT FROM expected_section
       OR NEW.start_time IS DISTINCT FROM expected_start
       OR NEW.end_time IS DISTINCT FROM expected_end
       OR NEW.booking_date < term_start OR NEW.booking_date > term_end
       OR EXTRACT(ISODOW FROM NEW.booking_date)::smallint <> expected_day THEN
        RAISE EXCEPTION 'Official booking does not match its approved timetable entry'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_official_booking_valid
BEFORE INSERT OR UPDATE ON timetabling_officialresourcebooking
FOR EACH ROW EXECUTE FUNCTION timetabling_check_official_booking();

CREATE FUNCTION timetabling_protect_official_booking_sources()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    changed boolean;
BEGIN
    IF TG_TABLE_NAME = 'timetabling_offeringrequirement' THEN
        IF TG_OP = 'DELETE' THEN
            changed := TRUE;
        ELSE
            changed := NEW.subject_offering_id IS DISTINCT FROM OLD.subject_offering_id
                OR NEW.section_id IS DISTINCT FROM OLD.section_id;
        END IF;
        IF changed THEN
            IF EXISTS (
                SELECT 1 FROM timetabling_officialresourcebooking AS booking
                JOIN timetabling_scheduleentry AS entry ON entry.id = booking.schedule_entry_id
                JOIN workloads_facultysubjectassignment AS assignment ON assignment.id = entry.assignment_id
                WHERE assignment.subject_offering_id = OLD.subject_offering_id
            ) THEN
                RAISE EXCEPTION 'Official booking source cannot be changed' USING ERRCODE = '23514';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'workloads_facultysubjectassignment' THEN
        IF TG_OP = 'DELETE' THEN
            changed := TRUE;
        ELSE
            changed := NEW.faculty_id IS DISTINCT FROM OLD.faculty_id
                OR NEW.subject_offering_id IS DISTINCT FROM OLD.subject_offering_id;
        END IF;
        IF changed THEN
            IF EXISTS (
                SELECT 1 FROM timetabling_officialresourcebooking AS booking
                JOIN timetabling_scheduleentry AS entry ON entry.id = booking.schedule_entry_id
                WHERE entry.assignment_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'Official booking source cannot be changed' USING ERRCODE = '23514';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'academics_academicterm' THEN
        IF NEW.start_date IS DISTINCT FROM OLD.start_date
           OR NEW.end_date IS DISTINCT FROM OLD.end_date THEN
            IF EXISTS (
                SELECT 1 FROM timetabling_officialresourcebooking AS booking
                JOIN timetabling_scheduleentry AS entry ON entry.id = booking.schedule_entry_id
                JOIN timetabling_schedule AS schedule ON schedule.id = entry.schedule_id
                WHERE schedule.academic_term_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'Official booking term dates cannot be changed' USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_official_requirement_source_guard
BEFORE UPDATE OF subject_offering_id, section_id OR DELETE ON timetabling_offeringrequirement
FOR EACH ROW EXECUTE FUNCTION timetabling_protect_official_booking_sources();
CREATE TRIGGER timetabling_official_assignment_source_guard
BEFORE UPDATE OF faculty_id, subject_offering_id OR DELETE ON workloads_facultysubjectassignment
FOR EACH ROW EXECUTE FUNCTION timetabling_protect_official_booking_sources();
CREATE TRIGGER timetabling_official_term_dates_guard
BEFORE UPDATE OF start_date, end_date ON academics_academicterm
FOR EACH ROW EXECUTE FUNCTION timetabling_protect_official_booking_sources();
"""

REVERSE_SQL = """
DROP TRIGGER timetabling_official_term_dates_guard ON academics_academicterm;
DROP TRIGGER timetabling_official_assignment_source_guard ON workloads_facultysubjectassignment;
DROP TRIGGER timetabling_official_requirement_source_guard ON timetabling_offeringrequirement;
DROP FUNCTION timetabling_protect_official_booking_sources();
DROP TRIGGER timetabling_official_booking_valid ON timetabling_officialresourcebooking;
DROP FUNCTION timetabling_check_official_booking();
DROP TRIGGER timetabling_active_schedule_valid ON timetabling_activeschedule;
DROP FUNCTION timetabling_check_active_schedule();
DROP TRIGGER timetabling_approval_snapshot_immutable ON timetabling_scheduleapprovalsnapshot;
DROP TRIGGER timetabling_workflow_history_immutable ON timetabling_scheduleworkflowevent;
DROP FUNCTION timetabling_prevent_workflow_history_mutation();
DROP TRIGGER timetabling_approved_entry_immutable ON timetabling_scheduleentry;
DROP FUNCTION timetabling_prevent_approved_entry_mutation();
DROP TRIGGER timetabling_approved_schedule_immutable ON timetabling_schedule;
DROP FUNCTION timetabling_prevent_approved_schedule_mutation();
"""


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("timetabling", "0004_activeschedule_officialresourcebooking_and_more")]

    operations = [
        migrations.RunPython(backfill_schedule_families, reverse_code=migrations.RunPython.noop, atomic=True),
        migrations.AlterField(
            model_name="schedule",
            name="family",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="versions",
                to="timetabling.schedulefamily",
            ),
        ),
        migrations.RunSQL(INTEGRITY_SQL, REVERSE_SQL),
    ]

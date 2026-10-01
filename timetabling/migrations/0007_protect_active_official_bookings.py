"""Keep bookings for the selected official schedule until it is replaced."""

from django.db import migrations


CREATE_SQL = """
CREATE FUNCTION timetabling_prevent_active_booking_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Lock the selection row so a concurrent replacement and deletion cannot
    -- each proceed using a stale view of which schedule is active.
    PERFORM 1
      FROM timetabling_activeschedule AS active
      JOIN timetabling_scheduleentry AS entry
        ON entry.schedule_id = active.schedule_id
     WHERE entry.id = OLD.schedule_entry_id
     FOR UPDATE OF active;
    IF FOUND THEN
        RAISE EXCEPTION 'Active official bookings cannot be deleted'
            USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER timetabling_active_booking_delete_guard
BEFORE DELETE ON timetabling_officialresourcebooking
FOR EACH ROW EXECUTE FUNCTION timetabling_prevent_active_booking_delete();

CREATE FUNCTION timetabling_check_requirement_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM workloads_subjectoffering AS offering
          JOIN timetabling_classsection AS section
            ON section.id = NEW.section_id
         WHERE offering.id = NEW.subject_offering_id
           AND offering.academic_term_id = section.academic_term_id
           AND offering.department_id = section.department_id
    ) THEN
        RAISE EXCEPTION 'Offering requirement section must match offering term and department'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_requirement_scope_guard
BEFORE INSERT OR UPDATE OF subject_offering_id, section_id
ON timetabling_offeringrequirement
FOR EACH ROW EXECUTE FUNCTION timetabling_check_requirement_scope();

CREATE FUNCTION timetabling_check_section_scope_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.academic_term_id IS DISTINCT FROM OLD.academic_term_id
       OR NEW.department_id IS DISTINCT FROM OLD.department_id THEN
        IF EXISTS (
            SELECT 1
              FROM timetabling_offeringrequirement AS requirement
              JOIN workloads_subjectoffering AS offering
                ON offering.id = requirement.subject_offering_id
             WHERE requirement.section_id = OLD.id
               AND (offering.academic_term_id <> NEW.academic_term_id
                    OR offering.department_id <> NEW.department_id)
        ) THEN
            RAISE EXCEPTION 'Section identity must match its offering requirements'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM timetabling_officialresourcebooking
             WHERE section_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'Official booking section identity cannot be changed'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_section_scope_guard
BEFORE UPDATE OF academic_term_id, department_id ON timetabling_classsection
FOR EACH ROW EXECUTE FUNCTION timetabling_check_section_scope_change();

CREATE FUNCTION timetabling_check_offering_scope_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.academic_term_id IS DISTINCT FROM OLD.academic_term_id
       OR NEW.department_id IS DISTINCT FROM OLD.department_id THEN
        IF EXISTS (
            SELECT 1
              FROM timetabling_offeringrequirement AS requirement
              JOIN timetabling_classsection AS section
                ON section.id = requirement.section_id
             WHERE requirement.subject_offering_id = OLD.id
               AND (section.academic_term_id <> NEW.academic_term_id
                    OR section.department_id <> NEW.department_id)
        ) THEN
            RAISE EXCEPTION 'Offering identity must match its section requirement'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER timetabling_offering_scope_guard
BEFORE UPDATE OF academic_term_id, department_id ON workloads_subjectoffering
FOR EACH ROW EXECUTE FUNCTION timetabling_check_offering_scope_change();
"""

REVERSE_SQL = """
DROP TRIGGER timetabling_offering_scope_guard ON workloads_subjectoffering;
DROP FUNCTION timetabling_check_offering_scope_change();
DROP TRIGGER timetabling_section_scope_guard ON timetabling_classsection;
DROP FUNCTION timetabling_check_section_scope_change();
DROP TRIGGER timetabling_requirement_scope_guard ON timetabling_offeringrequirement;
DROP FUNCTION timetabling_check_requirement_scope();
DROP TRIGGER timetabling_active_booking_delete_guard ON timetabling_officialresourcebooking;
DROP FUNCTION timetabling_prevent_active_booking_delete();
"""


class Migration(migrations.Migration):
    dependencies = [("timetabling", "0006_schedule_submitted_active_schedule")]

    operations = [migrations.RunSQL(CREATE_SQL, REVERSE_SQL)]

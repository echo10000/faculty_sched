# PostgreSQL-only constraints.  See scheduling.models.Assignment for why the
# TimeSlot values are mirrored onto Assignment by the trigger below.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0002_timeslot_assignment"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE EXTENSION IF NOT EXISTS btree_gist;

                ALTER TABLE scheduling_timeslot
                    ADD CONSTRAINT scheduling_timeslot_valid_interval
                    CHECK (end_time > start_time);

                CREATE FUNCTION scheduling_sync_assignment_slot()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    SELECT day_of_week, start_time, end_time
                    INTO NEW.day_of_week, NEW.start_time, NEW.end_time
                    FROM scheduling_timeslot
                    WHERE id = NEW.time_slot_id;

                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'TimeSlot % does not exist', NEW.time_slot_id;
                    END IF;
                    RETURN NEW;
                END;
                $$;

                CREATE TRIGGER scheduling_assignment_sync_slot
                BEFORE INSERT OR UPDATE OF time_slot_id ON scheduling_assignment
                FOR EACH ROW EXECUTE FUNCTION scheduling_sync_assignment_slot();

                -- A TimeSlot is immutable while it is in use.  That prevents
                -- later edits from making the indexed Assignment snapshot stale.
                CREATE FUNCTION scheduling_protect_used_time_slot()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF (NEW.day_of_week, NEW.start_time, NEW.end_time)
                       IS DISTINCT FROM (OLD.day_of_week, OLD.start_time, OLD.end_time)
                       AND EXISTS (
                           SELECT 1 FROM scheduling_assignment
                           WHERE time_slot_id = OLD.id
                       ) THEN
                        RAISE EXCEPTION 'A time slot cannot be changed after it is assigned';
                    END IF;
                    RETURN NEW;
                END;
                $$;

                CREATE TRIGGER scheduling_timeslot_protect_used_slot
                BEFORE UPDATE OF day_of_week, start_time, end_time ON scheduling_timeslot
                FOR EACH ROW EXECUTE FUNCTION scheduling_protect_used_time_slot();

                ALTER TABLE scheduling_assignment
                    ADD CONSTRAINT exclude_assignment_faculty_overlap
                    EXCLUDE USING gist (
                        faculty_id WITH =,
                        term_id WITH =,
                        day_of_week WITH =,
                        tsrange(
                            DATE '2000-01-01' + start_time,
                            DATE '2000-01-01' + end_time,
                            '[)'
                        ) WITH &&
                    ) DEFERRABLE INITIALLY IMMEDIATE;

                ALTER TABLE scheduling_assignment
                    ADD CONSTRAINT exclude_assignment_room_overlap
                    EXCLUDE USING gist (
                        room_id WITH =,
                        term_id WITH =,
                        day_of_week WITH =,
                        tsrange(
                            DATE '2000-01-01' + start_time,
                            DATE '2000-01-01' + end_time,
                            '[)'
                        ) WITH &&
                    ) DEFERRABLE INITIALLY IMMEDIATE;

                ALTER TABLE scheduling_assignment
                    ADD CONSTRAINT exclude_assignment_block_overlap
                    EXCLUDE USING gist (
                        block_id WITH =,
                        term_id WITH =,
                        day_of_week WITH =,
                        tsrange(
                            DATE '2000-01-01' + start_time,
                            DATE '2000-01-01' + end_time,
                            '[)'
                        ) WITH &&
                    ) DEFERRABLE INITIALLY IMMEDIATE;
            """,
            reverse_sql="""
                ALTER TABLE scheduling_assignment
                    DROP CONSTRAINT IF EXISTS exclude_assignment_block_overlap;
                ALTER TABLE scheduling_assignment
                    DROP CONSTRAINT IF EXISTS exclude_assignment_room_overlap;
                ALTER TABLE scheduling_assignment
                    DROP CONSTRAINT IF EXISTS exclude_assignment_faculty_overlap;
                ALTER TABLE scheduling_timeslot
                    DROP CONSTRAINT IF EXISTS scheduling_timeslot_valid_interval;
                DROP TRIGGER IF EXISTS scheduling_timeslot_protect_used_slot ON scheduling_timeslot;
                DROP FUNCTION IF EXISTS scheduling_protect_used_time_slot();
                DROP TRIGGER IF EXISTS scheduling_assignment_sync_slot ON scheduling_assignment;
                DROP FUNCTION IF EXISTS scheduling_sync_assignment_slot();
            """,
        ),
    ]

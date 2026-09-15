from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("academics", "0004_academicyear_semester_academicterm_and_more")]
    operations = [migrations.RunSQL(
        """
        CREATE FUNCTION academics_validate_term_dates() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE year_start date; year_end date;
        BEGIN
            SELECT start_date, end_date INTO year_start, year_end
            FROM academics_academicyear WHERE id = NEW.academic_year_id FOR SHARE;
            IF NEW.start_date < year_start OR NEW.end_date > year_end THEN
                RAISE EXCEPTION 'Term dates must lie within academic year' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER term_date_integrity BEFORE INSERT OR UPDATE ON academics_academicterm
        FOR EACH ROW EXECUTE FUNCTION academics_validate_term_dates();
        CREATE FUNCTION academics_validate_year_dates() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM academics_academicterm WHERE academic_year_id = NEW.id
                       AND (start_date < NEW.start_date OR end_date > NEW.end_date)) THEN
                RAISE EXCEPTION 'Year must contain existing terms' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER year_date_integrity BEFORE UPDATE ON academics_academicyear
        FOR EACH ROW EXECUTE FUNCTION academics_validate_year_dates();
        """,
        """
        DROP TRIGGER year_date_integrity ON academics_academicyear;
        DROP FUNCTION academics_validate_year_dates();
        DROP TRIGGER term_date_integrity ON academics_academicterm;
        DROP FUNCTION academics_validate_term_dates();
        """,
    )]

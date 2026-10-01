from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(
        """
        CREATE FUNCTION audit_prevent_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Audit records are immutable' USING ERRCODE = '23514'; END;
        $$;
        CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_auditlog
        FOR EACH ROW EXECUTE FUNCTION audit_prevent_mutation();
        """,
        "DROP TRIGGER audit_immutable ON audit_auditlog; DROP FUNCTION audit_prevent_mutation();",
    )]

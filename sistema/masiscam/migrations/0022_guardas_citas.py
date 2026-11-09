"""Explicit versioned library citations, without relaxing sent-row guards."""
from django.db import migrations


def condition():
    return """(NEW.clase='REFERENCIA' AND (NEW.referencia_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM masiscam_referenciatecnica r WHERE r.id=NEW.referencia_id AND r.documento_id=NEW.documento_id)))
        OR (NEW.clase<>'REFERENCIA' AND NEW.referencia_id IS NOT NULL)"""


def install(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for op in ('INSERT','UPDATE'):
        name='masiscam_t3_cita_'+op.lower()
        if vendor=='postgresql':
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition()}) THEN RAISE EXCEPTION 'tecnica: cita incoherente' USING ERRCODE='23514'; END IF;
                RETURN NEW; END $$""")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {op} ON masiscam_evidenciatecnica FOR EACH ROW EXECUTE FUNCTION {name}()')
        elif vendor=='sqlite':
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {op} ON masiscam_evidenciatecnica WHEN ({condition()}) BEGIN SELECT RAISE(ABORT,'tecnica: cita incoherente'); END")
        else:
            raise RuntimeError('Vendor no soportado para QA técnica.')


def uninstall(apps,schema_editor):
    for op in ('INSERT','UPDATE'):
        name='masiscam_t3_cita_'+op.lower()
        if schema_editor.connection.vendor=='postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_evidenciatecnica')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        else:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies=[('masiscam','0021_referencias_citadas')]
    operations=[migrations.RunPython(install,uninstall)]

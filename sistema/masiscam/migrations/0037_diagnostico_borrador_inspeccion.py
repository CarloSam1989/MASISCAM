"""Allow draft diagnostics linked to the active inspection."""
from django.db import migrations


INSERT_BASE = (
    "NEW.estado<>'EN_PROCESO' OR NEW.enviado_en IS NOT NULL OR NEW.revision<1 OR "
    "(NEW.sustituye_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_diagnostico s "
    "WHERE s.id=NEW.sustituye_id AND s.expediente_id=NEW.expediente_id AND s.revision<NEW.revision))"
)
INSPECTION_NOT_SENT = (
    "NOT EXISTS (SELECT 1 FROM masiscam_inspeccion i WHERE i.id=NEW.inspeccion_id "
    "AND i.expediente_id=NEW.expediente_id AND i.enviado_en IS NOT NULL)"
)
INSPECTION_NOT_CURRENT = (
    "NOT EXISTS (SELECT 1 FROM masiscam_inspeccion i JOIN masiscam_etapaexpediente e "
    "ON e.expediente_id=i.expediente_id AND e.codigo='INSPECCION' AND e.inspeccion_id=i.id "
    "WHERE i.id=NEW.inspeccion_id AND i.expediente_id=NEW.expediente_id)"
)


def _replace_trigger(schema_editor, condition):
    vendor = schema_editor.connection.vendor
    if vendor not in {'postgresql', 'sqlite'}:
        raise RuntimeError('La integridad técnica requiere PostgreSQL o SQLite QA.')
    name = 'masiscam_t3_diagnostico_insert'
    if vendor == 'postgresql':
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_diagnostico')
        schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF ({condition}) THEN RAISE EXCEPTION 'tecnica: informacion protegida' USING ERRCODE='23514'; END IF;
            RETURN NEW; END $$""")
        schema_editor.execute(f'CREATE TRIGGER {name} BEFORE INSERT ON masiscam_diagnostico FOR EACH ROW EXECUTE FUNCTION {name}()')
    else:
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')
        schema_editor.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON masiscam_diagnostico WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'tecnica: informacion protegida'); END")


def install(apps, schema_editor):
    _replace_trigger(schema_editor, f'({INSERT_BASE}) OR ({INSPECTION_NOT_CURRENT})')


def uninstall(apps, schema_editor):
    _replace_trigger(schema_editor, f'({INSERT_BASE}) OR ({INSPECTION_NOT_SENT})')


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0036_guardas_fotos_existentes')]
    operations = [migrations.RunPython(install, uninstall)]

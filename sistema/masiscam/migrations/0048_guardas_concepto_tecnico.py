"""Protege los conceptos técnicos cotizables: una vez congelados (diagnóstico enviado o solicitud emitida)
no se insertan, modifican ni eliminan. Las correcciones se hacen en una nueva revisión del diagnóstico, que
copia los conceptos vigentes. Migración aditiva: no reescribe datos."""
from django.db import migrations

FROZEN = ("EXISTS (SELECT 1 FROM masiscam_diagnostico d WHERE d.id={row}.diagnostico_id AND d.enviado_en IS NOT NULL) "
          "OR EXISTS (SELECT 1 FROM masiscam_solicitudrepuestos s WHERE s.diagnostico_id={row}.diagnostico_id)")


def definitions(vendor):
    distinct = 'IS DISTINCT FROM' if vendor == 'postgresql' else 'IS NOT'
    return [
        ('insert', 'INSERT', FROZEN.format(row='NEW')),
        ('update', 'UPDATE', FROZEN.format(row='OLD') + ' OR ' + FROZEN.format(row='NEW') + f' OR OLD.diagnostico_id {distinct} NEW.diagnostico_id'),
        ('delete', 'DELETE', FROZEN.format(row='OLD')),
    ]


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor not in {'postgresql', 'sqlite'}:
        raise RuntimeError('Las guardas de conceptos requieren PostgreSQL o SQLite QA.')
    for suffix, operation, condition in definitions(vendor):
        name = f'masiscam_t3_conceptotecnico_{suffix}'
        if vendor == 'postgresql':
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition}) THEN RAISE EXCEPTION 'tecnica: informacion protegida' USING ERRCODE='23514'; END IF;
                RETURN {'OLD' if operation == 'DELETE' else 'NEW'}; END $$""")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {operation} ON masiscam_conceptotecnico FOR EACH ROW EXECUTE FUNCTION {name}()')
        else:
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_conceptotecnico WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'tecnica: informacion protegida'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    for suffix, _operation, _condition in reversed(definitions(vendor)):
        name = f'masiscam_t3_conceptotecnico_{suffix}'
        if vendor == 'postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_conceptotecnico')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        else:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0047_concepto_tecnico')]
    operations = [migrations.RunPython(install, uninstall)]

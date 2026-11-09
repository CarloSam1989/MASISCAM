"""Additive guards; existing phase-3 triggers and historical records are retained."""
from django.db import migrations


def rules():
    result = [(table, operation, '1=1') for table in ('mapaplano', 'posicionplano') for operation in ('UPDATE', 'DELETE')]
    result.append(('mapaplano', 'INSERT', "NOT EXISTS (SELECT 1 FROM masiscam_referenciatecnica r JOIN masiscam_documento d ON d.id=r.documento_id WHERE r.id=NEW.referencia_id AND r.estado='PUBLICADA' AND r.tipo='PLANO' AND d.hash_sha256=NEW.hash_documento AND length(NEW.hash_documento)=64)"))
    result.append(('posicionplano', 'INSERT', "NOT EXISTS (SELECT 1 FROM masiscam_mapaplano m JOIN masiscam_partereferenciatecnica p ON p.referencia_id=m.referencia_id WHERE m.id=NEW.mapa_id AND p.id=NEW.parte_id AND p.posicion=NEW.numero)"))
    for operation in ('INSERT', 'UPDATE'):
        result.append(('piezarequerida', operation, "NEW.posicion_plano_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_posicionplano p WHERE p.id=NEW.posicion_plano_id AND p.parte_id=NEW.parte_id)"))
        result.append(('evidenciatecnica', operation, "NEW.pieza_id IS NOT NULL AND (NEW.inspeccion_id IS NOT NULL OR NOT EXISTS (SELECT 1 FROM masiscam_piezarequerida p WHERE p.id=NEW.pieza_id AND p.diagnostico_id=NEW.diagnostico_id))"))
    return result


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    for table, operation, condition in rules():
        name = f'masiscam_plano_{table}_{operation.lower()}'
        if vendor == 'postgresql':
            schema_editor.execute(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF ({condition}) THEN RAISE EXCEPTION 'plano: informacion protegida' USING ERRCODE='23514'; END IF; RETURN {'OLD' if operation == 'DELETE' else 'NEW'}; END $$")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        elif vendor == 'sqlite':
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'plano: informacion protegida'); END")
        else:
            raise RuntimeError('Las guardas de planos requieren PostgreSQL o SQLite QA.')


def uninstall(apps, schema_editor):
    for table, operation, _ in reversed(rules()):
        name = f'masiscam_plano_{table}_{operation.lower()}'
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}' + (f' ON masiscam_{table}' if schema_editor.connection.vendor == 'postgresql' else ''))
        if schema_editor.connection.vendor == 'postgresql':
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0031_plano_interactivo')]
    operations = [migrations.RunPython(install, uninstall)]

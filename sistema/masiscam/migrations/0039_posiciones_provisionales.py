from django.db import migrations, models
import importlib


def replace_guard(schema_editor, condition):
    if schema_editor.connection.vendor != 'postgresql':
        raise RuntimeError('Las posiciones provisionales requieren PostgreSQL.')
    schema_editor.execute(f"""CREATE OR REPLACE FUNCTION masiscam_plano_mapaplano_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF ({condition}) THEN RAISE EXCEPTION 'plano: informacion protegida' USING ERRCODE='23514'; END IF;
        RETURN NEW; END $$""")


def install(apps, schema_editor):
    replace_guard(schema_editor, """NOT EXISTS (
        SELECT 1 FROM masiscam_referenciatecnica r JOIN masiscam_documento d ON d.id=r.documento_id
        WHERE r.id=NEW.referencia_id AND r.tipo='PLANO' AND d.hash_sha256=NEW.hash_documento
          AND length(NEW.hash_documento)=64 AND (
            (r.estado='PUBLICADA' AND NOT NEW.provisional) OR
            (r.estado='BORRADOR' AND NEW.provisional AND r.expediente_trabajo_id IS NOT NULL)))""")


def uninstall(apps, schema_editor):
    rules = importlib.import_module('masiscam.migrations.0032_guardas_plano_interactivo').rules()
    condition = next(c for t, op, c in rules if t == 'mapaplano' and op == 'INSERT')
    replace_guard(schema_editor, condition)


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0038_planos_trabajo')]
    operations = [migrations.AddField(model_name='mapaplano', name='provisional',
        field=models.BooleanField(default=False)), migrations.RunPython(install, uninstall)]

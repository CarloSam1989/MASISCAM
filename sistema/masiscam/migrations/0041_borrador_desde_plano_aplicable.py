"""Reutilizar el documento aprobado en un borrador del equipo aplicable."""
from django.db import migrations


def guard(schema_editor, derivacion):
    if schema_editor.connection.vendor != 'postgresql':
        raise RuntimeError('Los planos de trabajo requieren PostgreSQL.')
    alternativa = """OR EXISTS (
        SELECT 1 FROM masiscam_referenciatecnica fuente
        WHERE fuente.id=NEW.sustituye_id AND fuente.empresa_id=NEW.empresa_id
          AND fuente.documento_id=NEW.documento_id AND fuente.tipo='PLANO'
          AND (fuente.expediente_trabajo_id=x.id OR
            (fuente.estado='PUBLICADA' AND EXISTS (
              SELECT 1 FROM masiscam_aplicabilidadreferenciaequipo a
              WHERE a.referencia_id=fuente.id AND a.equipo_id=e.id AND a.activa))))""" if derivacion else ''
    schema_editor.execute(f"""CREATE OR REPLACE FUNCTION masiscam_plano_trabajo_scope()
      RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='UPDATE' AND (NEW.expediente_trabajo_id IS DISTINCT FROM OLD.expediente_trabajo_id OR
          (OLD.expediente_trabajo_id IS NOT NULL AND NEW.documento_id<>OLD.documento_id)) THEN
          RAISE EXCEPTION 'Origen del plano de trabajo inmutable' USING ERRCODE='23514';
        END IF;
        IF NEW.expediente_trabajo_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM masiscam_expedientemantenimiento x JOIN masiscam_registroequipo r ON r.id=x.registro_id
            JOIN masiscam_equipo e ON e.id=r.equipo_id JOIN masiscam_documento d ON d.id=NEW.documento_id
            JOIN masiscam_proyecto proyecto_documento ON proyecto_documento.id=d.proyecto_id
          WHERE x.id=NEW.expediente_trabajo_id AND x.empresa_id=NEW.empresa_id
            AND proyecto_documento.empresa_id=NEW.empresa_id AND d.tipo_mime='application/pdf' AND NEW.tipo='PLANO'
            AND ((d.equipo_id=e.id AND d.proyecto_id=e.proyecto_id) {alternativa})
        ) THEN RAISE EXCEPTION 'Plano fuera del expediente' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$""")


def install(apps, schema_editor):
    guard(schema_editor, True)


def uninstall(apps, schema_editor):
    guard(schema_editor, False)


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0040_tres_roles_operativos')]
    operations = [migrations.RunPython(install, uninstall)]

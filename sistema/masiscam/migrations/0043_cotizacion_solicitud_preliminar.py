from importlib import import_module
from django.db import migrations


def instalar(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        raise RuntimeError('La cotizacion preliminar requiere PostgreSQL.')
    anterior=import_module('masiscam.migrations.0024_guardas_operacion')
    condition=next(c for t,o,c in anterior.definitions('postgresql') if t=='propuestacomercial' and o=='INSERT')
    informe="NOT EXISTS (SELECT 1 FROM masiscam_emisioninformetecnico i WHERE i.id=NEW.informe_id AND i.diagnostico_id=NEW.diagnostico_id AND i.estado='GENERADA' AND i.documento_id IS NOT NULL)"
    pendiente="""(d.estado='EN_PROCESO' AND NEW.snapshot->'cotizacion'->>'moneda'='USD'
      AND EXISTS (SELECT 1 FROM masiscam_solicitudrepuestos s
        WHERE s.diagnostico_id=d.id AND s.expediente_id=NEW.expediente_id
          AND s.snapshot->>'preliminar'='true'))"""
    condition=condition.replace(informe,f"""((NEW.informe_id IS NOT NULL AND {informe})
      OR (NEW.informe_id IS NULL AND NOT EXISTS (SELECT 1 FROM masiscam_diagnostico d
        WHERE d.id=NEW.diagnostico_id AND NEW.snapshot->>'revision_conjunta_requerida'='true'
          AND ((d.enviado_en IS NOT NULL AND d.estado IN ('PENDIENTE_APROBACION','APROBADO','FINALIZADO')) OR {pendiente}))))""")
    schema_editor.execute(f"""CREATE OR REPLACE FUNCTION masiscam_o4_propuestacomercial_insert()
      RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF ({condition}) THEN RAISE EXCEPTION 'operacion: informacion protegida' USING ERRCODE='23514'; END IF;
      RETURN NEW; END $$""")


def desinstalar(apps,schema_editor):
    import_module('masiscam.migrations.0042_revision_administrativa_conjunta').propuesta_guard(schema_editor,True)


class Migration(migrations.Migration):
    dependencies=[('masiscam','0042_revision_administrativa_conjunta')]
    operations=[migrations.RunPython(instalar,desinstalar)]

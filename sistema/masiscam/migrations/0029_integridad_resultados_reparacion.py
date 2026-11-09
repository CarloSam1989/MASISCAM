from django.db import migrations


CONDITION="""EXISTS (
    SELECT 1 FROM masiscam_resultadoreparacion r
    WHERE r.actividad_id=NEW.actividad_id
      AND NOT EXISTS (SELECT 1 FROM masiscam_resultadoreparacion n WHERE n.actividad_id=r.actividad_id AND n.version>r.version)
      AND NOT EXISTS (SELECT 1 FROM masiscam_revisionreparacion v WHERE v.resultado_id=r.id AND v.decision='OBSERVADO'))"""
NAME='masiscam_resultado_solo_correccion_observada'


def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute(f"CREATE FUNCTION {NAME}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF ({CONDITION}) THEN RAISE EXCEPTION 'resultado enviado o aprobado: correccion no autorizada' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        schema_editor.execute(f'CREATE TRIGGER {NAME} BEFORE INSERT ON masiscam_resultadoreparacion FOR EACH ROW EXECUTE FUNCTION {NAME}()')
    elif schema_editor.connection.vendor=='sqlite':
        schema_editor.execute(f"CREATE TRIGGER {NAME} BEFORE INSERT ON masiscam_resultadoreparacion WHEN ({CONDITION}) BEGIN SELECT RAISE(ABORT,'resultado enviado o aprobado: correccion no autorizada'); END")
    else: raise RuntimeError('Motor no soportado por las guardas MASISCAM.')


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {NAME} ON masiscam_resultadoreparacion')
        schema_editor.execute(f'DROP FUNCTION IF EXISTS {NAME}()')
    else: schema_editor.execute(f'DROP TRIGGER IF EXISTS {NAME}')


class Migration(migrations.Migration):
    dependencies=[('masiscam','0028_guardas_cierre')]
    operations=[migrations.RunPython(install,uninstall)]

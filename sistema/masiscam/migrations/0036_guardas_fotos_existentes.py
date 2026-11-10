"""Additive reference guards; no updates to historical rows or files."""
import importlib
from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        raise RuntimeError('Esta corrección se valida exclusivamente en PostgreSQL.')
    old = importlib.import_module('masiscam.migrations.0018_confirmacion_guardas')
    condition = old.rules('postgresql')[0][2]
    for clase in ('GENERAL', 'PLACA'):
        original = "EXISTS (SELECT 1 FROM masiscam_fotorecepcionclasificada c JOIN masiscam_evidenciarecepcion e ON e.id=c.evidencia_id WHERE e.recepcion_id=NEW.recepcion_id AND c.clase='%s')" % clase
        extra = "EXISTS (SELECT 1 FROM masiscam_fotorecepcionexistente f WHERE f.recepcion_id=NEW.recepcion_id AND f.clase='%s')" % clase
        condition = condition.replace(original, f'({original} OR {extra})')
    schema_editor.execute(f"""CREATE OR REPLACE FUNCTION masiscam_r2c_confirmacionrecepcion_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF ({condition}) THEN RAISE EXCEPTION 'recepcion: confirmacion protegida' USING ERRCODE='23514'; END IF;
        RETURN NEW; END $$""")
    schema_editor.execute("""CREATE FUNCTION masiscam_foto_referencia_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Referencia inmutable' USING ERRCODE='23514'; END IF;
      PERFORM 1 FROM masiscam_expedientemantenimiento x JOIN masiscam_recepcionequipo r ON r.expediente_id=x.id
        WHERE r.id=NEW.recepcion_id FOR UPDATE OF x;
      IF EXISTS(SELECT 1 FROM masiscam_confirmacionrecepcion WHERE recepcion_id=NEW.recepcion_id) OR
        NOT EXISTS(SELECT 1 FROM masiscam_recepcionequipo r
          JOIN masiscam_expedientemantenimiento x ON x.id=r.expediente_id
          JOIN masiscam_registroequipo reg ON reg.id=x.registro_id
          JOIN masiscam_equipo e ON e.id=reg.equipo_id
          WHERE r.id=NEW.recepcion_id AND (
            EXISTS(SELECT 1 FROM masiscam_documento d WHERE d.id=NEW.documento_id AND d.equipo_id=e.id
              AND d.proyecto_id=e.proyecto_id AND d.tipo_mime IN ('image/jpeg','image/png','image/webp') AND d.estado_sincronizacion<>'ARCHIVADO') OR
            EXISTS(SELECT 1 FROM masiscam_archivoregistro a JOIN masiscam_registroequipo ar ON ar.id=a.registro_id
              WHERE a.id=NEW.archivo_registro_id AND ar.equipo_id=e.id AND a.estado='DISPONIBLE'
              AND a.drive_file_id<>'' AND a.tipo_mime IN ('image/jpeg','image/png','image/webp'))))
      THEN RAISE EXCEPTION 'Fotografia fuera de ambito o recepcion confirmada' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$""")
    schema_editor.execute('CREATE TRIGGER masiscam_foto_referencia_guard BEFORE INSERT OR UPDATE OR DELETE ON masiscam_fotorecepcionexistente FOR EACH ROW EXECUTE FUNCTION masiscam_foto_referencia_guard()')
    schema_editor.execute("""CREATE FUNCTION masiscam_foto_origen_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='masiscam_documento' THEN
        IF EXISTS(SELECT 1 FROM masiscam_fotorecepcionexistente WHERE documento_id=OLD.id) THEN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Original protegido' USING ERRCODE='23514'; END IF;
          IF (to_jsonb(NEW)-ARRAY['estado_sincronizacion','error_sincronizacion','drive_file_id','drive_upload_id','drive_view_url','drive_download_url']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['estado_sincronizacion','error_sincronizacion','drive_file_id','drive_upload_id','drive_view_url','drive_download_url']) OR NEW.estado_sincronizacion='ARCHIVADO'
          THEN RAISE EXCEPTION 'Original protegido' USING ERRCODE='23514'; END IF;
        END IF;
      ELSIF EXISTS(SELECT 1 FROM masiscam_fotorecepcionexistente WHERE archivo_registro_id=OLD.id) THEN
        RAISE EXCEPTION 'Original protegido' USING ERRCODE='23514';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$""")
    for table in ('documento', 'archivoregistro'):
        schema_editor.execute(f'CREATE TRIGGER masiscam_foto_origen_guard BEFORE UPDATE OR DELETE ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION masiscam_foto_origen_guard()')


def uninstall(apps, schema_editor):
    for table in ('documento', 'archivoregistro'):
        schema_editor.execute(f'DROP TRIGGER masiscam_foto_origen_guard ON masiscam_{table}')
    schema_editor.execute('DROP FUNCTION masiscam_foto_origen_guard()')
    schema_editor.execute('DROP TRIGGER masiscam_foto_referencia_guard ON masiscam_fotorecepcionexistente')
    schema_editor.execute('DROP FUNCTION masiscam_foto_referencia_guard()')
    old = importlib.import_module('masiscam.migrations.0018_confirmacion_guardas')
    condition = old.rules('postgresql')[0][2]
    schema_editor.execute(f"""CREATE OR REPLACE FUNCTION masiscam_r2c_confirmacionrecepcion_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF ({condition}) THEN RAISE EXCEPTION 'recepcion: confirmacion protegida' USING ERRCODE='23514'; END IF;
        RETURN NEW; END $$""")


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0035_fotos_recepcion_existentes')]
    operations = [migrations.RunPython(install, uninstall)]

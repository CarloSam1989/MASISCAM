from importlib import import_module
from django.db import migrations


def install(apps, schema_editor):
    guard = import_module('masiscam.migrations.0039_posiciones_provisionales')
    guard.replace_guard(schema_editor, """NOT EXISTS (
        SELECT 1 FROM masiscam_referenciatecnica r JOIN masiscam_documento d ON d.id=r.documento_id
        WHERE r.id=NEW.referencia_id AND r.tipo='PLANO' AND d.hash_sha256=NEW.hash_documento
          AND length(NEW.hash_documento)=64 AND (
            (r.estado='PUBLICADA' AND NOT NEW.provisional) OR
            (r.estado='BORRADOR' AND NEW.provisional)))""")


def uninstall(apps, schema_editor):
    import_module('masiscam.migrations.0039_posiciones_provisionales').install(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('masiscam', '0045_equipo_operacion_referenciatecnica_operacion_and_more')]
    operations = [migrations.RunPython(install, uninstall)]

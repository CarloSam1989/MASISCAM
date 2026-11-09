"""Additive protection. No historical rows are updated or classified."""
from django.db import migrations


def rules(vendor):
    confirmed = "EXISTS (SELECT 1 FROM masiscam_confirmacionrecepcion WHERE recepcion_id=NEW.recepcion_id)"
    photos = "FROM masiscam_fotorecepcionclasificada c JOIN masiscam_evidenciarecepcion e ON e.id=c.evidencia_id WHERE e.recepcion_id=NEW.recepcion_id"
    frozen = ("g.snapshot->>'plantilla'='recepcion-v3' AND g.snapshot->>'pendiente_validacion'='false' AND g.snapshot->>'politica'=NEW.politica"
              if vendor == "postgresql" else
              "json_extract(g.snapshot, '$.plantilla')='recepcion-v3' AND json_extract(g.snapshot, '$.pendiente_validacion')=0 AND json_extract(g.snapshot, '$.politica')=NEW.politica")
    valid = f"""EXISTS (SELECT 1 FROM masiscam_recepcionequipo r
        JOIN masiscam_emisionguiaingreso g ON g.id=NEW.guia_id
        JOIN masiscam_documento d ON d.id=g.documento_id
        WHERE r.id=NEW.recepcion_id AND g.expediente_id=r.expediente_id
        AND g.estado='GENERADA' AND d.archivo<>'' AND d.tamano>0
        AND g.clave=NEW.clave AND length(NEW.huella)=64 AND ({frozen})
        AND length(g.hash_sha256)=64 AND g.hash_sha256=d.hash_sha256
        AND (r.guia_remision<>'' OR length(trim(NEW.motivo_sin_guia))>0))
        AND EXISTS (SELECT 1 {photos} AND c.clase='GENERAL')
        AND (NEW.placa='SIN_PLACA' AND length(trim(NEW.motivo_sin_placa))>0
             OR NEW.placa='CON_PLACA' AND EXISTS (SELECT 1 {photos} AND c.clase='PLACA'))"""
    return [
        ("confirmacionrecepcion", "INSERT", f"NOT ({valid})"),
        ("confirmacionrecepcion", "UPDATE", "1=1"),
        ("confirmacionrecepcion", "DELETE", "1=1"),
        ("fotorecepcionclasificada", "INSERT", "NOT EXISTS (SELECT 1 FROM masiscam_evidenciarecepcion e WHERE e.id=NEW.evidencia_id AND e.tipo='FOTO') OR EXISTS (SELECT 1 FROM masiscam_confirmacionrecepcion c JOIN masiscam_evidenciarecepcion e ON e.recepcion_id=c.recepcion_id WHERE e.id=NEW.evidencia_id)"),
        ("fotorecepcionclasificada", "UPDATE", "1=1"),
        ("fotorecepcionclasificada", "DELETE", "1=1"),
        ("evidenciarecepcion", "INSERT", confirmed),
        ("emisionguiaingreso", "INSERT", "EXISTS (SELECT 1 FROM masiscam_confirmacionrecepcion c JOIN masiscam_recepcionequipo r ON r.id=c.recepcion_id WHERE r.expediente_id=NEW.expediente_id)"),
    ]


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor not in {"postgresql", "sqlite"}:
        raise RuntimeError("Confirmación soporta PostgreSQL y SQLite local.")
    for table, operation, condition in rules(vendor):
        name = f"masiscam_r2c_{table}_{operation.lower()}"
        if vendor == "postgresql":
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition}) THEN RAISE EXCEPTION 'recepcion: confirmacion protegida' USING ERRCODE='23514'; END IF;
                RETURN {'OLD' if operation == 'DELETE' else 'NEW'}; END $$""")
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()")
        else:
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} WHEN ({condition}) BEGIN SELECT RAISE(ABORT, 'recepcion: confirmacion protegida'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    for table, operation, _ in reversed(rules(vendor)):
        name = f"masiscam_r2c_{table}_{operation.lower()}"
        if vendor == "postgresql":
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name} ON masiscam_{table}")
            schema_editor.execute(f"DROP FUNCTION IF EXISTS {name}()")
        elif vendor == "sqlite":
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}")


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0017_confirmacion_recepcion_local")]
    operations = [migrations.RunPython(install, uninstall)]

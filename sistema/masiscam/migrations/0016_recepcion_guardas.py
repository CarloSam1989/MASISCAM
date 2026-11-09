"""Protection of phase-2 rows, without modifying any historical data.

PostgreSQL is authoritative; SQLite equivalents permit isolated regression QA.
No network, storage, signals or runtime model imports in this migration.
"""
from django.db import migrations


def definitions(vendor):
    def changed(fields):
        op = "IS DISTINCT FROM" if vendor == "postgresql" else "IS NOT"
        return " OR ".join(f"OLD.{f} {op} NEW.{f}" for f in fields)

    exp = "EXISTS (SELECT 1 FROM masiscam_expedientemantenimiento WHERE registro_id=OLD.id)"
    doc = "(EXISTS (SELECT 1 FROM masiscam_emisionguiaingreso WHERE documento_id=OLD.id) OR EXISTS (SELECT 1 FROM masiscam_evidenciarecepcion WHERE documento_id=OLD.id))"
    fields = {
        "expedientemantenimiento": "registro_id empresa_id cliente_contractual_id correlativo numero huella_solicitud creado_por_id creado_en",
        "recepcionequipo": "expediente_id fecha responsable_id condicion_ingreso observaciones guia_remision snapshot creado_en",
        "evidenciarecepcion": "recepcion_id documento_id tipo",
    }
    rules = []
    for table, names in fields.items():
        rules += [(table, "UPDATE", changed(names.split())), (table, "DELETE", "1=1")]
    rules += [
        ("contadorexpediente", "UPDATE", "NEW.ultimo_numero < OLD.ultimo_numero OR " + changed(["empresa_id"])),
        ("contadorexpediente", "DELETE", "1=1"),
        ("emisionguiaingreso", "DELETE", "1=1"),
        ("emisionguiaingreso", "UPDATE", "(" + changed("expediente_id version clave snapshot creado_por_id creado_en".split()) + ") OR (OLD.documento_id IS NOT NULL AND (" + changed("documento_id hash_sha256 estado error".split()) + "))"),
        ("documento", "UPDATE", f"{doc} AND (NEW.estado_sincronizacion='ARCHIVADO' OR (" + changed("proyecto_id equipo_id categoria titulo descripcion archivo nombre_original tipo_mime tamano hash_sha256 publico subido_por_id cargado_en".split()) + "))"),
        ("documento", "DELETE", doc),
        ("registroequipo", "UPDATE", f"{exp} AND (" + changed("equipo_id tipo fecha observacion clave_creacion drive_folder_id drive_folder_url".split()) + ")"),
        ("registroequipo", "DELETE", exp),
        ("auditoria", "UPDATE", "OLD.expediente_id IS NOT NULL AND (" + changed("empresa_id proyecto_id usuario_id expediente_id accion objeto_tipo objeto_id detalle creado_en".split()) + ")"),
        ("auditoria", "DELETE", "OLD.expediente_id IS NOT NULL"),
        ("equipo", "UPDATE", "EXISTS (SELECT 1 FROM masiscam_expedientemantenimiento x JOIN masiscam_registroequipo r ON r.id=x.registro_id WHERE r.equipo_id=OLD.id) AND (SELECT empresa_id FROM masiscam_proyecto WHERE id=OLD.proyecto_id) <> (SELECT empresa_id FROM masiscam_proyecto WHERE id=NEW.proyecto_id)"),
        ("proyecto", "UPDATE", "EXISTS (SELECT 1 FROM masiscam_expedientemantenimiento x JOIN masiscam_registroequipo r ON r.id=x.registro_id JOIN masiscam_equipo e ON e.id=r.equipo_id WHERE e.proyecto_id=OLD.id) AND " + changed(["empresa_id"])),
    ]
    return rules


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor not in {"postgresql", "sqlite"}:
        raise RuntimeError("Recepción requiere PostgreSQL; SQLite solo para pruebas.")
    for table, operation, condition in definitions(vendor):
        name = f"masiscam_r2_{table}_{operation.lower()}"
        if vendor == "postgresql":
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition}) THEN RAISE EXCEPTION 'recepcion: informacion protegida' USING ERRCODE='23514'; END IF;
                RETURN {'OLD' if operation == 'DELETE' else 'NEW'}; END $$""")
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()")
        else:
            schema_editor.execute(f"""CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table}
                WHEN ({condition}) BEGIN SELECT RAISE(ABORT, 'recepcion: informacion protegida'); END""")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    for table, operation, _ in reversed(definitions(vendor)):
        name = f"masiscam_r2_{table}_{operation.lower()}"
        if vendor == "postgresql":
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name} ON masiscam_{table}")
            schema_editor.execute(f"DROP FUNCTION IF EXISTS {name}()")
        elif vendor == "sqlite":
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}")


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0015_recepcion_expedientes")]
    operations = [migrations.RunPython(install, uninstall)]

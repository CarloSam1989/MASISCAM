from django.db import migrations

TABLES = ('solicitudrepuestos','adquisicionrepuesto','enviorepuestos','detalleenvio','confirmacionenvio','recepcionlineaenvio')
CHECKS = {
    'solicitudrepuestos': "EXISTS (SELECT 1 FROM masiscam_diagnostico d WHERE d.id=NEW.diagnostico_id AND d.expediente_id=NEW.expediente_id AND d.tecnico_id=NEW.autor_id AND d.enviado_en IS NOT NULL)",
    'adquisicionrepuesto': "EXISTS (SELECT 1 FROM masiscam_solicitudrepuestos s JOIN masiscam_piezarequerida p ON p.diagnostico_id=s.diagnostico_id JOIN masiscam_decisioncomercial c ON c.id=NEW.autorizacion_id JOIN masiscam_propuestacomercial b ON b.id=c.propuesta_id WHERE s.id=NEW.solicitud_id AND s.expediente_id=NEW.expediente_id AND p.id=NEW.pieza_id AND p.unidad=NEW.unidad AND b.diagnostico_id=s.diagnostico_id AND c.expediente_id=s.expediente_id AND c.decision='APROBADO')",
    'enviorepuestos': "EXISTS (SELECT 1 FROM masiscam_solicitudrepuestos s JOIN masiscam_documento d ON d.id=NEW.documento_id JOIN masiscam_expedientemantenimiento e ON e.id=s.expediente_id JOIN masiscam_registroequipo r ON r.id=e.registro_id WHERE s.id=NEW.solicitud_id AND s.expediente_id=NEW.expediente_id AND d.equipo_id=r.equipo_id AND d.hash_sha256=NEW.hash_sha256 AND NOT d.publico)",
    'detalleenvio': "EXISTS (SELECT 1 FROM masiscam_enviorepuestos e JOIN masiscam_adquisicionrepuesto a ON a.solicitud_id=e.solicitud_id WHERE e.id=NEW.envio_id AND a.id=NEW.adquisicion_id AND a.unidad=NEW.unidad)",
    'confirmacionenvio': "EXISTS (SELECT 1 FROM masiscam_enviorepuestos e WHERE e.id=NEW.envio_id AND e.expediente_id=NEW.expediente_id)",
    'recepcionlineaenvio': "EXISTS (SELECT 1 FROM masiscam_confirmacionenvio c JOIN masiscam_detalleenvio d ON d.envio_id=c.envio_id JOIN masiscam_adquisicionrepuesto a ON a.id=d.adquisicion_id WHERE c.id=NEW.confirmacion_id AND d.id=NEW.detalle_id AND ((NEW.cantidad=0 AND NEW.recepcion_id IS NULL) OR EXISTS (SELECT 1 FROM masiscam_recepcionrepuesto r WHERE r.id=NEW.recepcion_id AND r.expediente_id=c.expediente_id AND r.pieza_id=a.pieza_id AND r.cantidad=NEW.cantidad AND r.unidad=d.unidad)))",
}


def instalar(apps, schema_editor):
    pg = schema_editor.connection.vendor == 'postgresql'
    if schema_editor.connection.vendor not in {'postgresql','sqlite'}: return
    for table in TABLES:
        full='masiscam_'+table
        if pg:
            schema_editor.execute(f"CREATE FUNCTION flujo_{table}_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Registro operativo inmutable' USING ERRCODE='23514'; END IF; IF NOT ({CHECKS[table]}) THEN RAISE EXCEPTION 'Origen operativo ajeno o incoherente' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
            schema_editor.execute(f'CREATE TRIGGER flujo_{table}_guard BEFORE INSERT OR UPDATE OR DELETE ON {full} FOR EACH ROW EXECUTE FUNCTION flujo_{table}_guard()')
        else:
            schema_editor.execute(f"CREATE TRIGGER flujo_{table}_insert BEFORE INSERT ON {full} BEGIN SELECT RAISE(ABORT,'Origen operativo ajeno o incoherente') WHERE NOT ({CHECKS[table]}); END")
            for op in ('UPDATE','DELETE'):
                schema_editor.execute(f"CREATE TRIGGER flujo_{table}_{op.lower()} BEFORE {op} ON {full} BEGIN SELECT RAISE(ABORT,'Registro operativo inmutable'); END")


def retirar(apps, schema_editor):
    for table in TABLES:
        if schema_editor.connection.vendor == 'postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS flujo_{table}_guard ON masiscam_{table}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS flujo_{table}_guard()')
        elif schema_editor.connection.vendor == 'sqlite':
            for op in ('insert','update','delete'):
                schema_editor.execute(f'DROP TRIGGER IF EXISTS flujo_{table}_{op}')


class Migration(migrations.Migration):
    dependencies = [('masiscam','0033_relevo_taller_oficina')]
    operations = [migrations.RunPython(instalar, retirar)]

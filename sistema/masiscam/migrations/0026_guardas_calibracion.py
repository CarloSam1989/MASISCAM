from django.db import migrations


def definitions(vendor):
    op='IS DISTINCT FROM' if vendor=='postgresql' else 'IS NOT'
    def changed(fields):
        return '('+' OR '.join(f'OLD.{f} {op} NEW.{f}' for f in fields.split())+')'
    rules=[]
    immutable=('plantillacalibracion','validacionplantillacalibracion','aplicabilidadcalibracion','decisioncalibracion','evidenciacalibracion')
    for table in immutable:
        rules += [(table,'UPDATE','1=1'),(table,'DELETE','1=1')]
    for table in ('calibracion','medicioncalibracion','emisioncalibracion'):
        rules.append((table,'DELETE','1=1'))
    identity='expediente_id diagnostico_id autorizacion_id plantilla_id reparacion_snapshot revision sustituye_id motivo_revision tecnico_id creado_en clave'
    content=identity+' fecha parametros instrumentos observaciones resultados motivo_sin_evidencia version_edicion enviado_en'
    rules.append(('calibracion','UPDATE',changed(identity)+' OR (OLD.enviado_en IS NOT NULL AND '+changed(content)+')'))
    rules.append(('calibracion','INSERT',"NEW.estado<>'NO_INICIADA' OR NEW.enviado_en IS NOT NULL OR NEW.revision<1 OR (NEW.sustituye_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_calibracion c WHERE c.id=NEW.sustituye_id AND c.expediente_id=NEW.expediente_id AND c.revision<NEW.revision))"))
    rules.append(('calibracion','UPDATE',"NEW.estado NOT IN ('NO_INICIADA','EN_PROCESO','PENDIENTE_REVISION','OBSERVADA','APROBADA','RECHAZADA') OR (OLD.enviado_en IS NOT NULL AND NEW.estado<>OLD.estado AND NOT (OLD.estado='PENDIENTE_REVISION' AND EXISTS (SELECT 1 FROM masiscam_decisioncalibracion d WHERE d.calibracion_id=NEW.id AND d.decision=NEW.estado)))"))
    rules.append(('calibracion','UPDATE',"(NEW.estado IN ('NO_INICIADA','EN_PROCESO') AND NEW.enviado_en IS NOT NULL) OR (NEW.estado='PENDIENTE_REVISION' AND NEW.enviado_en IS NULL) OR (NEW.estado IN ('APROBADA','OBSERVADA','RECHAZADA') AND (NEW.enviado_en IS NULL OR NOT EXISTS (SELECT 1 FROM masiscam_decisioncalibracion d WHERE d.calibracion_id=NEW.id AND d.decision=NEW.estado)))"))
    rules.append(('calibracion','UPDATE',"NEW.enviado_en IS NOT NULL AND ((SELECT COUNT(*) FROM masiscam_medicioncalibracion m WHERE m.calibracion_id=NEW.id AND m.valor IS NOT NULL)<>16 OR NEW.fecha IS NULL OR NEW.instrumentos='' OR NEW.resultados='')"))
    rules.append(('calibracion','INSERT',"NOT EXISTS (SELECT 1 FROM masiscam_decisioncomercial a JOIN masiscam_propuestacomercial p ON p.id=a.propuesta_id JOIN masiscam_plantillacalibracion t ON t.id=NEW.plantilla_id JOIN masiscam_expedientemantenimiento x ON x.id=NEW.expediente_id WHERE a.id=NEW.autorizacion_id AND a.decision='APROBADO' AND p.diagnostico_id=NEW.diagnostico_id AND p.expediente_id=x.id AND t.empresa_id=x.empresa_id AND EXISTS (SELECT 1 FROM masiscam_validacionplantillacalibracion v WHERE v.plantilla_id=t.id))"))
    for table in ('medicioncalibracion','evidenciacalibracion'):
        for operation in ('INSERT','UPDATE'):
            rules.append((table,operation,"EXISTS (SELECT 1 FROM masiscam_calibracion c WHERE c.id=NEW.calibracion_id AND c.enviado_en IS NOT NULL)"))
    point=("EXISTS (SELECT 1 FROM masiscam_calibracion c JOIN masiscam_plantillacalibracion p ON p.id=c.plantilla_id, jsonb_array_elements(p.definicion->'mediciones') d WHERE c.id=NEW.calibracion_id AND d->>'codigo'=NEW.punto)" if vendor=='postgresql' else
        "EXISTS (SELECT 1 FROM masiscam_calibracion c JOIN masiscam_plantillacalibracion p ON p.id=c.plantilla_id, json_each(p.definicion,'$.mediciones') d WHERE c.id=NEW.calibracion_id AND json_extract(d.value,'$.codigo')=NEW.punto)")
    rules.append(('medicioncalibracion','INSERT','NOT '+point))
    rules.append(('medicioncalibracion','UPDATE',changed('calibracion_id punto')))
    rules.append(('validacionplantillacalibracion','INSERT',"EXISTS (SELECT 1 FROM masiscam_plantillacalibracion p WHERE p.id=NEW.plantilla_id AND p.autor_id=NEW.responsable_id)"))
    rules.append(('aplicabilidadcalibracion','INSERT',"NOT EXISTS (SELECT 1 FROM masiscam_plantillacalibracion p JOIN masiscam_equipo e ON e.id=NEW.equipo_id JOIN masiscam_proyecto q ON q.id=e.proyecto_id WHERE p.id=NEW.plantilla_id AND p.empresa_id=q.empresa_id AND p.tipo_equipo=e.tipo_producto)"))
    for table,docfield,company in (
        ('plantillacalibracion','documento_id','NEW.empresa_id'),
        ('evidenciacalibracion','documento_id','(SELECT x.empresa_id FROM masiscam_calibracion c JOIN masiscam_expedientemantenimiento x ON x.id=c.expediente_id WHERE c.id=NEW.calibracion_id)'),
        ('emisioncalibracion','documento_id','(SELECT x.empresa_id FROM masiscam_calibracion c JOIN masiscam_expedientemantenimiento x ON x.id=c.expediente_id WHERE c.id=NEW.calibracion_id)')):
        for operation in ('INSERT','UPDATE'):
            rules.append((table,operation,f"NEW.{docfield} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_documento d JOIN masiscam_proyecto p ON p.id=d.proyecto_id WHERE d.id=NEW.{docfield} AND p.empresa_id={company} AND NOT d.publico AND d.estado_sincronizacion<>'ARCHIVADO')"))
    own="""EXISTS (WITH RECURSIVE chain AS (
        SELECT id,tecnico_id,sustituye_id FROM masiscam_calibracion WHERE id=NEW.calibracion_id
        UNION ALL SELECT p.id,p.tecnico_id,p.sustituye_id FROM masiscam_calibracion p JOIN chain c ON c.sustituye_id=p.id)
        SELECT 1 FROM chain WHERE tecnico_id=NEW.responsable_id)"""
    rules.append(('decisioncalibracion','INSERT',own+" OR NEW.decision NOT IN ('APROBADA','OBSERVADA','RECHAZADA') OR NEW.motivo='' OR NOT EXISTS (SELECT 1 FROM masiscam_calibracion c WHERE c.id=NEW.calibracion_id AND c.estado='PENDIENTE_REVISION' AND c.enviado_en IS NOT NULL)"))
    rules.append(('emisioncalibracion','UPDATE',changed('calibracion_id version snapshot huella autor_id creado_en clave')+' OR (OLD.documento_id IS NOT NULL AND '+changed('documento_id hash_sha256 estado error')+')'))
    for operation in ('INSERT','UPDATE'):
        rules.append(('emisioncalibracion',operation,"NEW.version<1 OR (NEW.estado='GENERADA' AND NOT EXISTS (SELECT 1 FROM masiscam_documento d WHERE d.id=NEW.documento_id AND d.hash_sha256=NEW.hash_sha256 AND length(NEW.hash_sha256)=64 AND d.tamano>0 AND d.archivo<>''))"))
    protected='(EXISTS (SELECT 1 FROM masiscam_plantillacalibracion WHERE documento_id=OLD.id) OR EXISTS (SELECT 1 FROM masiscam_evidenciacalibracion WHERE documento_id=OLD.id) OR EXISTS (SELECT 1 FROM masiscam_emisioncalibracion WHERE documento_id=OLD.id))'
    rules.append(('documento','DELETE',protected))
    rules.append(('documento','UPDATE',protected+" AND (NEW.estado_sincronizacion='ARCHIVADO' OR "+changed('proyecto_id equipo_id categoria titulo descripcion archivo nombre_original tipo_mime tamano hash_sha256 publico subido_por_id cargado_en')+')'))
    rules.append(('proyecto','UPDATE',changed('empresa_id')+" AND EXISTS (SELECT 1 FROM masiscam_plantillacalibracion t JOIN masiscam_documento d ON d.id=t.documento_id WHERE d.proyecto_id=OLD.id)"))
    grouped={}
    for table,operation,condition in rules:
        grouped.setdefault((table,operation),[]).append('('+condition+')')
    return [(t,o,' OR '.join(c)) for (t,o),c in grouped.items()]


def install(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    if vendor not in {'postgresql','sqlite'}:
        raise RuntimeError('Calibración QA requiere PostgreSQL o SQLite.')
    for table,operation,condition in definitions(vendor):
        name=f'masiscam_c5_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition}) THEN RAISE EXCEPTION 'calibracion: informacion protegida' USING ERRCODE='23514'; END IF;
                RETURN {'OLD' if operation=='DELETE' else 'NEW'}; END $$""")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        else:
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'calibracion: informacion protegida'); END")


def uninstall(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for table,operation,_ in reversed(definitions(vendor)):
        name=f'masiscam_c5_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_{table}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        else:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies=[('masiscam','0025_calibracion_fase5')]
    operations=[migrations.RunPython(install,uninstall)]

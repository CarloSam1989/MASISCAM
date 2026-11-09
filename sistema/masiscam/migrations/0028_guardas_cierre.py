from django.db import migrations


def definitions(vendor):
    op='IS DISTINCT FROM' if vendor=='postgresql' else 'IS NOT'
    def changed(fields): return '('+' OR '.join(f'OLD.{f} {op} NEW.{f}' for f in fields.split())+')'
    rules=[]
    for table in ('informefinal','revisioninformefinal','cierreexpediente'):
        rules.append((table,'DELETE','1=1'))
    for table in ('revisioninformefinal','cierreexpediente'): rules.append((table,'UPDATE','1=1'))
    identity='expediente_id calibracion_id version sustituye_id motivo snapshot huella autor_id creado_en clave'
    rules.append(('informefinal','UPDATE',changed(identity)+' OR (OLD.documento_id IS NOT NULL AND '+changed('documento_id hash_sha256 enviado_en error')+')'))
    rules.append(('informefinal','INSERT',"NEW.estado<>'BORRADOR' OR NEW.documento_id IS NOT NULL OR NEW.enviado_en IS NOT NULL OR length(NEW.huella)<>64 OR NOT EXISTS (SELECT 1 FROM masiscam_calibracion c WHERE c.id=NEW.calibracion_id AND c.expediente_id=NEW.expediente_id AND c.estado='APROBADA') OR (NEW.sustituye_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_informefinal f WHERE f.id=NEW.sustituye_id AND f.expediente_id=NEW.expediente_id AND f.version<NEW.version))"))
    rules.append(('informefinal','UPDATE',"NEW.estado<>OLD.estado AND NOT ((OLD.estado='BORRADOR' AND NEW.estado='PENDIENTE_REVISION') OR (OLD.estado='PENDIENTE_REVISION' AND EXISTS (SELECT 1 FROM masiscam_revisioninformefinal r WHERE r.informe_id=NEW.id AND r.decision=NEW.estado)) OR (OLD.estado='APROBADO' AND NEW.estado='FINALIZADO' AND EXISTS (SELECT 1 FROM masiscam_cierreexpediente x WHERE x.informe_id=NEW.id)))"))
    for operation in ('INSERT','UPDATE'):
        rules.append(('informefinal',operation,"NEW.documento_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_documento d JOIN masiscam_proyecto p ON p.id=d.proyecto_id JOIN masiscam_expedientemantenimiento x ON x.id=NEW.expediente_id WHERE d.id=NEW.documento_id AND NOT d.publico AND p.empresa_id=x.empresa_id AND d.equipo_id=(SELECT r.equipo_id FROM masiscam_registroequipo r WHERE r.id=x.registro_id) AND d.hash_sha256=NEW.hash_sha256 AND length(NEW.hash_sha256)=64 AND d.archivo<>'' AND d.tamano>0 AND d.estado_sincronizacion<>'ARCHIVADO')"))
    authors="""EXISTS (SELECT 1 FROM masiscam_informefinal f WHERE f.expediente_id=(SELECT expediente_id FROM masiscam_informefinal WHERE id=NEW.informe_id) AND f.autor_id=NEW.responsable_id)
        OR EXISTS (SELECT 1 FROM masiscam_inspeccion i WHERE i.expediente_id=(SELECT expediente_id FROM masiscam_informefinal WHERE id=NEW.informe_id) AND i.tecnico_id=NEW.responsable_id)
        OR EXISTS (SELECT 1 FROM masiscam_diagnostico d WHERE d.expediente_id=(SELECT expediente_id FROM masiscam_informefinal WHERE id=NEW.informe_id) AND d.tecnico_id=NEW.responsable_id)
        OR EXISTS (SELECT 1 FROM masiscam_actividadreparacion a WHERE a.expediente_id=(SELECT expediente_id FROM masiscam_informefinal WHERE id=NEW.informe_id) AND a.autor_id=NEW.responsable_id)
        OR EXISTS (SELECT 1 FROM masiscam_calibracion c WHERE c.expediente_id=(SELECT expediente_id FROM masiscam_informefinal WHERE id=NEW.informe_id) AND c.tecnico_id=NEW.responsable_id)"""
    rules.append(('revisioninformefinal','INSERT',authors+" OR NEW.decision NOT IN ('APROBADO','OBSERVADO') OR NEW.motivo='' OR NOT EXISTS (SELECT 1 FROM masiscam_informefinal f WHERE f.id=NEW.informe_id AND f.estado='PENDIENTE_REVISION' AND f.documento_id IS NOT NULL)"))
    rules.append(('cierreexpediente','INSERT',authors+" OR NEW.motivo='' OR NOT EXISTS (SELECT 1 FROM masiscam_informefinal f JOIN masiscam_revisioninformefinal r ON r.informe_id=f.id WHERE f.id=NEW.informe_id AND f.expediente_id=NEW.expediente_id AND f.huella=NEW.huella AND f.estado='APROBADO' AND r.decision='APROBADO' AND r.responsable_id<>NEW.responsable_id AND NOT EXISTS (SELECT 1 FROM masiscam_informefinal n WHERE n.expediente_id=f.expediente_id AND n.version>f.version))"))
    # Freeze every maintenance-owned record after closure, including drafts
    # of older revisions. Audit insertions and Drive metadata remain possible.
    paths={name:'NEW.expediente_id' for name in ('asignaciontecnica','etapaexpediente','inspeccion','diagnostico','emisioninformetecnico','propuestacomercial','decisioncomercial','recepcionrepuesto','actividadreparacion','usorepuesto','resultadoreparacion','revisionreparacion','evidenciaoperativa','emisionoperativa','calibracion','informefinal','emisionguiaingreso')}
    paths.update({
        'piezarequerida':'(SELECT expediente_id FROM masiscam_diagnostico WHERE id=NEW.diagnostico_id)',
        'evidenciatecnica':'COALESCE((SELECT expediente_id FROM masiscam_diagnostico WHERE id=NEW.diagnostico_id),(SELECT expediente_id FROM masiscam_inspeccion WHERE id=NEW.inspeccion_id))',
        'medicioncalibracion':'(SELECT expediente_id FROM masiscam_calibracion WHERE id=NEW.calibracion_id)',
        'evidenciacalibracion':'(SELECT expediente_id FROM masiscam_calibracion WHERE id=NEW.calibracion_id)',
        'decisioncalibracion':'(SELECT expediente_id FROM masiscam_calibracion WHERE id=NEW.calibracion_id)',
        'emisioncalibracion':'(SELECT expediente_id FROM masiscam_calibracion WHERE id=NEW.calibracion_id)',
        'decisiontecnica':'(SELECT expediente_id FROM masiscam_diagnostico WHERE id=NEW.diagnostico_id)',
        'revisioninformefinal':'(SELECT expediente_id FROM masiscam_informefinal WHERE id=NEW.informe_id)',
    })
    for table,path in paths.items():
        for operation in ('INSERT','UPDATE'):
            condition=f'EXISTS (SELECT 1 FROM masiscam_cierreexpediente x WHERE x.expediente_id={path})'
            if table=='informefinal' and operation=='UPDATE':
                condition+=" AND NOT (OLD.estado='APROBADO' AND NEW.estado='FINALIZADO' AND EXISTS (SELECT 1 FROM masiscam_cierreexpediente x WHERE x.informe_id=NEW.id))"
            rules.append((table,operation,condition))
    protected='EXISTS (SELECT 1 FROM masiscam_informefinal f WHERE f.documento_id=OLD.id)'
    rules.append(('documento','DELETE',protected))
    rules.append(('documento','UPDATE',protected+" AND (NEW.estado_sincronizacion='ARCHIVADO' OR "+changed('proyecto_id equipo_id categoria titulo descripcion archivo nombre_original tipo_mime tamano hash_sha256 publico subido_por_id cargado_en')+')'))
    grouped={}
    for table,operation,condition in rules: grouped.setdefault((table,operation),[]).append('('+condition+')')
    return [(t,o,' OR '.join(cs)) for (t,o),cs in grouped.items()]


def install(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    if vendor not in {'postgresql','sqlite'}: raise RuntimeError('Fase 6 requiere PostgreSQL o SQLite.')
    for table,operation,condition in definitions(vendor):
        name=f'masiscam_f6_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF ({condition}) THEN RAISE EXCEPTION 'cierre: informacion protegida' USING ERRCODE='23514'; END IF; RETURN {'OLD' if operation=='DELETE' else 'NEW'}; END $$")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        else:
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'cierre: informacion protegida'); END")


def uninstall(apps,schema_editor):
    for table,operation,_ in reversed(definitions(schema_editor.connection.vendor)):
        name=f'masiscam_f6_{table}_{operation.lower()}'
        if schema_editor.connection.vendor=='postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_{table}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        else: schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies=[('masiscam','0027_cierre_fase6')]
    operations=[migrations.RunPython(install,uninstall)]

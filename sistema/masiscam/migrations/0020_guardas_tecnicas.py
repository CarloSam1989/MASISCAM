"""Additive phase-3 guards. Earlier migrations and rows are not rewritten."""
from django.db import migrations


def definitions(vendor):
    op='IS DISTINCT FROM' if vendor=='postgresql' else 'IS NOT'
    def changed(fields):
        return '('+' OR '.join(f'OLD.{name} {op} NEW.{name}' for name in fields.split())+')'
    rules=[]
    immutable=('asignaciontecnica','etapaexpediente','inspeccion','diagnostico','decisiontecnica','referenciatecnica','componentetecnico','aplicabilidadreferenciaequipo','emisioninformetecnico')
    for table in immutable:
        rules.append((table,'DELETE','1=1'))
    for table in ('inspeccion','diagnostico'):
        content='expediente_id revision tecnico_id creado_en sustituye_id motivo_revision'
        allfields=content+' fecha enviado_en version_edicion motivo_sin_evidencia observaciones'
        allfields += ' estado_fisico condiciones_generales hallazgos' if table=='inspeccion' else ' inspeccion_id fallas causas componentes_afectados recomendaciones conclusiones repuestos'
        rules.append((table,'UPDATE',changed(content)+f' OR (OLD.enviado_en IS NOT NULL AND {changed(allfields)})'))
        rules.append((table,'INSERT',"NEW.estado<>'EN_PROCESO' OR NEW.enviado_en IS NOT NULL OR NEW.revision<1 OR " +
            "(NEW.sustituye_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_"+table+" s WHERE s.id=NEW.sustituye_id AND s.expediente_id=NEW.expediente_id AND s.revision<NEW.revision))"))
    rules.append(('inspeccion','UPDATE',"OLD.enviado_en IS NOT NULL AND NEW.estado<>OLD.estado"))
    # Multiple conditions on a table/operation are combined below.
    rules.append(('diagnostico','UPDATE',"OLD.enviado_en IS NOT NULL AND NEW.estado<>OLD.estado AND NOT ("
        "OLD.estado='PENDIENTE_APROBACION' AND EXISTS (SELECT 1 FROM masiscam_decisiontecnica d WHERE d.diagnostico_id=OLD.id AND d.decision=NEW.estado) OR "
        "OLD.estado='APROBADO' AND NEW.estado='FINALIZADO' AND EXISTS (SELECT 1 FROM masiscam_emisioninformetecnico e WHERE e.diagnostico_id=OLD.id AND e.estado='GENERADA' AND e.documento_id IS NOT NULL))"))
    rules.append(('diagnostico','INSERT',"NOT EXISTS (SELECT 1 FROM masiscam_inspeccion i WHERE i.id=NEW.inspeccion_id AND i.expediente_id=NEW.expediente_id AND i.enviado_en IS NOT NULL)"))
    rules.append(('diagnostico','UPDATE',"NEW.enviado_en IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_etapaexpediente e WHERE e.expediente_id=NEW.expediente_id AND e.codigo='INSPECCION' AND e.inspeccion_id=NEW.inspeccion_id)"))
    for table in ('evidenciatecnica','piezarequerida'):
        parent='(EXISTS (SELECT 1 FROM masiscam_diagnostico WHERE id={row}.diagnostico_id AND enviado_en IS NOT NULL)'
        if table=='evidenciatecnica':
            parent+=' OR EXISTS (SELECT 1 FROM masiscam_inspeccion WHERE id={row}.inspeccion_id AND enviado_en IS NOT NULL)'
        parent+=')'
        rules += [(table,'INSERT',parent.format(row='NEW')),(table,'UPDATE',parent.format(row='NEW')+' OR '+parent.format(row='OLD')),(table,'DELETE',parent.format(row='OLD'))]
    for operation in ('INSERT','UPDATE'):
        rules.append(('evidenciatecnica',operation,"NOT EXISTS (SELECT 1 FROM masiscam_documento d JOIN masiscam_proyecto p ON p.id=d.proyecto_id JOIN masiscam_expedientemantenimiento x ON x.empresa_id=p.empresa_id WHERE d.id=NEW.documento_id AND NOT d.publico AND (x.id=(SELECT expediente_id FROM masiscam_inspeccion WHERE id=NEW.inspeccion_id) OR x.id=(SELECT expediente_id FROM masiscam_diagnostico WHERE id=NEW.diagnostico_id)))"))
        rules.append(('piezarequerida',operation,"NEW.parte_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_partereferenciatecnica p JOIN masiscam_referenciatecnica r ON r.id=p.referencia_id JOIN masiscam_diagnostico d ON d.id=NEW.diagnostico_id JOIN masiscam_expedientemantenimiento x ON x.id=d.expediente_id WHERE p.id=NEW.parte_id AND r.empresa_id=x.empresa_id)"))
        rules.append(('referenciatecnica',operation,"NOT EXISTS (SELECT 1 FROM masiscam_documento d JOIN masiscam_proyecto p ON p.id=d.proyecto_id WHERE d.id=NEW.documento_id AND p.empresa_id=NEW.empresa_id AND NOT d.publico AND d.categoria=CASE WHEN NEW.tipo='PLANO' THEN 'PLANOS' ELSE 'MANUALES' END)"))
        rules.append(('aplicabilidadreferenciaequipo',operation,"NOT EXISTS (SELECT 1 FROM masiscam_referenciatecnica r JOIN masiscam_equipo e ON e.id=NEW.equipo_id JOIN masiscam_proyecto p ON p.id=e.proyecto_id WHERE r.id=NEW.referencia_id AND r.empresa_id=p.empresa_id)"))
        rules.append(('etapaexpediente',operation,"NOT EXISTS (SELECT 1 FROM masiscam_inspeccion i WHERE NEW.codigo='INSPECCION' AND i.id=NEW.inspeccion_id AND i.expediente_id=NEW.expediente_id AND i.estado=NEW.estado) AND NOT EXISTS (SELECT 1 FROM masiscam_diagnostico d WHERE NEW.codigo='DIAGNOSTICO' AND d.id=NEW.diagnostico_id AND d.expediente_id=NEW.expediente_id AND d.estado=NEW.estado)"))
    for operation in ('INSERT','UPDATE','DELETE'):
        row='OLD' if operation=='DELETE' else 'NEW'
        cond=f"EXISTS (SELECT 1 FROM masiscam_referenciatecnica WHERE id={row}.referencia_id AND estado<>'BORRADOR')"
        if operation=='UPDATE':
            cond+=" OR EXISTS (SELECT 1 FROM masiscam_referenciatecnica WHERE id=OLD.referencia_id AND estado<>'BORRADOR')"
        rules.append(('partereferenciatecnica',operation,cond))
    rules.append(('referenciatecnica','UPDATE',changed('empresa_id codigo revision autor_id creado_en sustituye_id')+
        " OR (OLD.estado<>'BORRADOR' AND ("+changed('documento_id tipo titulo marca modelo ratio version_fabricante procedencia autorizacion_uso')+
        " OR NOT (NEW.estado=OLD.estado OR OLD.estado='PUBLICADA' AND NEW.estado='RETIRADA')))"))
    rules.append(('componentetecnico','UPDATE',changed('empresa_id autor_id')))
    rules.append(('asignaciontecnica','UPDATE',changed('expediente_id tecnico_id asignado_por_id inicio motivo clave')+' OR (OLD.fin IS NOT NULL AND '+changed('fin')+')'))
    rules.append(('decisiontecnica','UPDATE','1=1'))
    authors="""EXISTS (WITH RECURSIVE ds AS (
        SELECT id,tecnico_id,inspeccion_id,sustituye_id FROM masiscam_diagnostico WHERE id=NEW.diagnostico_id
        UNION SELECT d.id,d.tecnico_id,d.inspeccion_id,d.sustituye_id FROM masiscam_diagnostico d JOIN ds ON d.id=ds.sustituye_id),
        ins AS (SELECT i.id,i.tecnico_id,i.sustituye_id FROM masiscam_inspeccion i JOIN ds ON i.id=ds.inspeccion_id
        UNION SELECT i.id,i.tecnico_id,i.sustituye_id FROM masiscam_inspeccion i JOIN ins s ON i.id=s.sustituye_id)
        SELECT 1 FROM ds WHERE tecnico_id=NEW.responsable_id UNION SELECT 1 FROM ins WHERE tecnico_id=NEW.responsable_id)"""
    rules.append(('decisiontecnica','INSERT',authors+" OR NEW.decision NOT IN ('APROBADO','OBSERVADO','RECHAZADO') OR (NEW.decision<>'APROBADO' AND trim(NEW.motivo)='') OR NOT EXISTS (SELECT 1 FROM masiscam_diagnostico d JOIN masiscam_etapaexpediente e ON e.diagnostico_id=d.id JOIN masiscam_etapaexpediente i ON i.inspeccion_id=d.inspeccion_id WHERE d.id=NEW.diagnostico_id AND d.estado='PENDIENTE_APROBACION' AND d.enviado_en IS NOT NULL AND i.codigo='INSPECCION')"))
    rules.append(('emisioninformetecnico','UPDATE',changed('expediente_id diagnostico_id tipo version clave huella snapshot autor_id creado_en')+
        ' OR (OLD.documento_id IS NOT NULL AND '+changed('documento_id hash_sha256 estado error')+')'))
    for operation in ('INSERT','UPDATE'):
        rules.append(('emisioninformetecnico',operation,"NOT EXISTS (SELECT 1 FROM masiscam_diagnostico d WHERE d.id=NEW.diagnostico_id AND d.expediente_id=NEW.expediente_id) OR NEW.version<1 OR NEW.tipo<>'INICIAL' OR (NEW.estado='GENERADA' AND NOT EXISTS (SELECT 1 FROM masiscam_documento d JOIN masiscam_proyecto p ON p.id=d.proyecto_id JOIN masiscam_expedientemantenimiento x ON x.id=NEW.expediente_id WHERE d.id=NEW.documento_id AND p.empresa_id=x.empresa_id AND NOT d.publico AND d.hash_sha256=NEW.hash_sha256 AND length(NEW.hash_sha256)=64 AND d.tamano>0 AND d.archivo<>''))"))
    doc="(EXISTS (SELECT 1 FROM masiscam_evidenciatecnica WHERE documento_id=OLD.id) OR EXISTS (SELECT 1 FROM masiscam_referenciatecnica WHERE documento_id=OLD.id) OR EXISTS (SELECT 1 FROM masiscam_emisioninformetecnico WHERE documento_id=OLD.id))"
    rules += [('documento','DELETE',doc),('documento','UPDATE',doc+" AND (NEW.estado_sincronizacion='ARCHIVADO' OR "+changed('proyecto_id equipo_id categoria titulo descripcion archivo nombre_original tipo_mime tamano hash_sha256 publico subido_por_id cargado_en')+')'),
        ('auditoria','DELETE',"OLD.objeto_tipo='BibliotecaTecnica'"),('auditoria','UPDATE',"OLD.objeto_tipo='BibliotecaTecnica'")]
    rules.append(('proyecto','UPDATE',changed('empresa_id')+" AND EXISTS (SELECT 1 FROM masiscam_documento d WHERE d.proyecto_id=OLD.id AND (EXISTS (SELECT 1 FROM masiscam_referenciatecnica WHERE documento_id=d.id) OR EXISTS (SELECT 1 FROM masiscam_evidenciatecnica WHERE documento_id=d.id) OR EXISTS (SELECT 1 FROM masiscam_emisioninformetecnico WHERE documento_id=d.id)))"))
    grouped={}
    for table,operation,condition in rules:
        grouped.setdefault((table,operation),[]).append('('+condition+')')
    return [(t,o,' OR '.join(c)) for (t,o),c in grouped.items()]


def install(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    if vendor not in {'postgresql','sqlite'}:
        raise RuntimeError('Fase 3 local requiere PostgreSQL o SQLite QA.')
    for table,operation,condition in definitions(vendor):
        name=f'masiscam_t3_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition}) THEN RAISE EXCEPTION 'tecnica: informacion protegida' USING ERRCODE='23514'; END IF;
                RETURN {'OLD' if operation=='DELETE' else 'NEW'}; END $$""")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        else:
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'tecnica: informacion protegida'); END")


def uninstall(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for table,operation,_ in reversed(definitions(vendor)):
        name=f'masiscam_t3_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_{table}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        else:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies=[('masiscam','0019_trabajo_tecnico_fase3')]
    operations=[migrations.RunPython(install,uninstall)]

"""Additive guards; never rewrite historical tables or seed approvals."""
from django.db import migrations


def definitions(vendor):
    distinct='IS DISTINCT FROM' if vendor=='postgresql' else 'IS NOT'
    def changed(fields):
        return '('+' OR '.join(f'OLD.{f} {distinct} NEW.{f}' for f in fields.split())+')'
    tables=('propuestacomercial','decisioncomercial','recepcionrepuesto','actividadreparacion',
        'usorepuesto','resultadoreparacion','revisionreparacion','evidenciaoperativa','emisionoperativa')
    rules=[]
    for table in tables:
        rules.append((table,'DELETE','1=1'))
        if table!='emisionoperativa':
            rules.append((table,'UPDATE','1=1'))
    rules.append(('emisionoperativa','UPDATE',changed('expediente_id autor_id creado_en clave tipo version huella snapshot')+
        ' OR (OLD.documento_id IS NOT NULL AND '+changed('documento_id hash_sha256 estado error')+')'))
    relations={
        'propuestacomercial': [('diagnostico','diagnostico'),('informe','emisioninformetecnico'),('sustituye','propuestacomercial')],
        'decisioncomercial':[('propuesta','propuestacomercial')],
        'actividadreparacion':[('autorizacion','decisioncomercial')],
        'usorepuesto':[('actividad','actividadreparacion'),('recepcion','recepcionrepuesto')],
        'resultadoreparacion':[('actividad','actividadreparacion')],
        'revisionreparacion':[('resultado','resultadoreparacion')],
        'evidenciaoperativa':[('propuesta','propuestacomercial'),('decision','decisioncomercial'),('recepcion','recepcionrepuesto'),('actividad','actividadreparacion')],
    }
    for table,links in relations.items():
        for field,target in links:
            rules.append((table,'INSERT',f'NEW.{field}_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_{target} p WHERE p.id=NEW.{field}_id AND p.expediente_id=NEW.expediente_id)'))
    rules.append(('propuestacomercial','INSERT',"NOT EXISTS (SELECT 1 FROM masiscam_emisioninformetecnico i WHERE i.id=NEW.informe_id AND i.diagnostico_id=NEW.diagnostico_id AND i.estado='GENERADA' AND i.documento_id IS NOT NULL) OR NEW.version<1"))
    rules.append(('decisioncomercial','INSERT',"NEW.decision NOT IN ('APROBADO','RECHAZADO') OR NEW.representante_cliente='' OR NEW.motivo=''"))
    rules.append(('recepcionrepuesto','INSERT',"NEW.pieza_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_piezarequerida p JOIN masiscam_diagnostico d ON d.id=p.diagnostico_id WHERE p.id=NEW.pieza_id AND d.expediente_id=NEW.expediente_id)"))
    rules.append(('actividadreparacion','INSERT',"NOT EXISTS (SELECT 1 FROM masiscam_decisioncomercial a JOIN masiscam_propuestacomercial p ON p.id=a.propuesta_id JOIN masiscam_diagnostico d ON d.id=p.diagnostico_id JOIN masiscam_etapaexpediente e ON e.diagnostico_id=d.id AND e.codigo='DIAGNOSTICO' JOIN masiscam_etapaexpediente i ON i.inspeccion_id=d.inspeccion_id AND i.codigo='INSPECCION' WHERE a.id=NEW.autorizacion_id AND a.decision='APROBADO' AND d.estado IN ('APROBADO','FINALIZADO') AND NOT EXISTS (SELECT 1 FROM masiscam_propuestacomercial p2 WHERE p2.expediente_id=p.expediente_id AND p2.version>p.version))"))
    rules.append(('resultadoreparacion','INSERT',"NOT EXISTS (SELECT 1 FROM masiscam_actividadreparacion a WHERE a.id=NEW.actividad_id AND a.autor_id=NEW.autor_id AND a.inicio<=NEW.fin) OR NEW.version<1"))
    rules.append(('revisionreparacion','INSERT',"NEW.decision NOT IN ('APROBADO','OBSERVADO') OR NEW.motivo='' OR EXISTS (SELECT 1 FROM masiscam_resultadoreparacion r JOIN masiscam_actividadreparacion a ON a.id=r.actividad_id WHERE r.id=NEW.resultado_id AND (r.autor_id=NEW.autor_id OR a.autor_id=NEW.autor_id))"))
    rules.append(('usorepuesto','INSERT',"NEW.cantidad+(SELECT COALESCE(SUM(u.cantidad),0) FROM masiscam_usorepuesto u WHERE u.recepcion_id=NEW.recepcion_id)>(SELECT cantidad FROM masiscam_recepcionrepuesto WHERE id=NEW.recepcion_id)"))
    for table in ('usorepuesto','evidenciaoperativa'):
        rules.append((table,'INSERT',"NEW.actividad_id IS NOT NULL AND EXISTS (SELECT 1 FROM masiscam_resultadoreparacion r WHERE r.actividad_id=NEW.actividad_id AND NOT EXISTS (SELECT 1 FROM masiscam_resultadoreparacion r2 WHERE r2.actividad_id=r.actividad_id AND r2.version>r.version) AND NOT EXISTS (SELECT 1 FROM masiscam_revisionreparacion v WHERE v.resultado_id=r.id AND v.decision='OBSERVADO'))"))
    for table in ('evidenciaoperativa','emisionoperativa'):
        for operation in ('INSERT','UPDATE'):
            rules.append((table,operation,"NEW.documento_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM masiscam_documento d JOIN masiscam_proyecto p ON p.id=d.proyecto_id JOIN masiscam_expedientemantenimiento x ON x.id=NEW.expediente_id WHERE d.id=NEW.documento_id AND p.empresa_id=x.empresa_id AND NOT d.publico AND d.estado_sincronizacion<>'ARCHIVADO')"))
    rules.append(('evidenciaoperativa','INSERT',"NEW.propuesta_id IS NOT NULL AND EXISTS (SELECT 1 FROM masiscam_decisioncomercial WHERE propuesta_id=NEW.propuesta_id)"))
    for operation in ('INSERT','UPDATE'):
        rules.append(('emisionoperativa',operation,"NEW.version<1 OR NEW.tipo NOT IN ('REPUESTOS','REPARACION') OR (NEW.estado='GENERADA' AND NOT EXISTS (SELECT 1 FROM masiscam_documento d WHERE d.id=NEW.documento_id AND d.hash_sha256=NEW.hash_sha256 AND length(NEW.hash_sha256)=64 AND d.tamano>0 AND d.archivo<>''))"))
    protected='(EXISTS (SELECT 1 FROM masiscam_evidenciaoperativa WHERE documento_id=OLD.id) OR EXISTS (SELECT 1 FROM masiscam_emisionoperativa WHERE documento_id=OLD.id))'
    rules.append(('documento','DELETE',protected))
    rules.append(('documento','UPDATE',protected+" AND (NEW.estado_sincronizacion='ARCHIVADO' OR "+changed('proyecto_id equipo_id categoria titulo descripcion archivo nombre_original tipo_mime tamano hash_sha256 publico subido_por_id cargado_en')+')'))
    grouped={}
    for table,operation,condition in rules:
        grouped.setdefault((table,operation),[]).append('('+condition+')')
    return [(t,o,' OR '.join(c)) for (t,o),c in grouped.items()]


def install(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    if vendor not in {'postgresql','sqlite'}:
        raise RuntimeError('Fase 4 requiere PostgreSQL o SQLite QA.')
    for table,operation,condition in definitions(vendor):
        name=f'masiscam_o4_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF ({condition}) THEN RAISE EXCEPTION 'operacion: informacion protegida' USING ERRCODE='23514'; END IF;
                RETURN {'OLD' if operation=='DELETE' else 'NEW'}; END $$""")
            schema_editor.execute(f'CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        else:
            schema_editor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON masiscam_{table} WHEN ({condition}) BEGIN SELECT RAISE(ABORT,'operacion: informacion protegida'); END")


def uninstall(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for table,operation,_ in reversed(definitions(vendor)):
        name=f'masiscam_o4_{table}_{operation.lower()}'
        if vendor=='postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name} ON masiscam_{table}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {name}()')
        else:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies=[('masiscam','0023_operacion_fase4')]
    operations=[migrations.RunPython(install,uninstall)]

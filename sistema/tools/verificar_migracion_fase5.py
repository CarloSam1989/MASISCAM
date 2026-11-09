"""Rehearse 0024 -> phase 5 with synthetic phase-2/3 rows and media only.

PostgreSQL: --seed then take pg_dump externally, then --upgrade.
SQLite: --sqlite performs both phases with a native backup in QA artifacts.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE']='config.recepcion_test_settings'
sqlite='--sqlite' in sys.argv
if sqlite:
    os.environ.pop('MASISCAM_TEST_POSTGRES',None)
import django
from django.conf import settings
out=ROOT/'artifacts'/'fase5-qa'
out.mkdir(parents=True,exist_ok=True)
if sqlite:
    work=Path(tempfile.mkdtemp(prefix='masiscam-fase5-upgrade-'))
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':work/'qa.sqlite3'}}
else:
    assert os.environ.get('MASISCAM_TEST_POSTGRES')
    assert settings.DATABASES['default']['NAME']=='masiscam_qa_fase5'
    assert settings.DATABASES['default']['HOST']=='127.0.0.1'
settings.GOOGLE_DRIVE_ENABLED=False
settings.MASISCAM_RECEPCION_LOCAL=True
settings.MASISCAM_TECNICA_LOCAL=False
settings.MEDIA_ROOT=out/('upgrade-media-sqlite' if sqlite else 'upgrade-media-postgres')
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

kind='sqlite' if sqlite else 'postgres'
before_file=out/f'migracion-{kind}-antes.json'
tables=['accounts_empresa','accounts_perfil','masiscam_rolmasiscam','masiscam_cliente','masiscam_proyecto',
    'masiscam_equipo','masiscam_registroequipo','masiscam_documento','masiscam_contadorexpediente',
    'masiscam_expedientemantenimiento','masiscam_recepcionequipo','masiscam_evidenciarecepcion',
    'masiscam_fotorecepcionclasificada','masiscam_emisionguiaingreso','masiscam_confirmacionrecepcion','masiscam_auditoria']
tables += ['masiscam_'+name for name in ('asignaciontecnica','inspeccion','diagnostico','etapaexpediente',
    'evidenciatecnica','decisiontecnica','referenciatecnica','componentetecnico','partereferenciatecnica',
    'aplicabilidadreferenciaequipo','piezarequerida','emisioninformetecnico')]
tables += ['masiscam_'+name for name in ('propuestacomercial','decisioncomercial','recepcionrepuesto','actividadreparacion',
    'usorepuesto','resultadoreparacion','revisionreparacion','evidenciaoperativa','emisionoperativa')]


def inventory():
    result={}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f'SELECT * FROM {table} ORDER BY id')
            result[table]=[list(map(str,row)) for row in cursor.fetchall()]
    return result


def files():
    return {str(p.relative_to(settings.MEDIA_ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in settings.MEDIA_ROOT.rglob('*') if p.is_file()}


executor=MigrationExecutor(connection)
if '--seed' in sys.argv or sqlite:
    assert not executor.loader.applied_migrations,'Start only from empty disposable QA base'
    target=[('masiscam','0024_guardas_operacion')]
    executor.migrate(target)
    # Historical model classes do not execute current runtime signals.
    apps=executor.loader.project_state(target).apps
    m=lambda name:apps.get_model('masiscam',name)
    user=apps.get_model('auth','User').objects.create(username='QA-HISTORICO',password='!')
    empresa=apps.get_model('accounts','Empresa').objects.create(nombre='QA HISTÓRICA FICTICIA')
    cliente=m('Cliente').objects.create(empresa=empresa,ruc='QA-NO-REAL',nombre_comercial='QA',razon_social='QA')
    proyecto=m('Proyecto').objects.create(empresa=empresa,codigo='QA',nombre='QA',responsable='QA',fecha_inicio='2026-11-06',creado_por=user)
    equipo=m('Equipo').objects.create(proyecto=proyecto,cliente=cliente,nombre='QA',token_publico='TOKEN-QA-NO-REAL',drive_folder_id='DRIVE-QA-NO-REAL')
    registro=m('RegistroEquipo').objects.create(equipo=equipo,tipo='MANTENIMIENTO',fecha='2026-11-06',secuencia=1)
    exp=m('ExpedienteMantenimiento').objects.create(registro=registro,empresa=empresa,cliente_contractual=cliente,
        correlativo=1,numero='EXP-QA-HISTORICO',huella_solicitud='a'*64,creado_por=user)
    m('ContadorExpediente').objects.create(empresa=empresa,ultimo_numero=1)
    rec=m('RecepcionEquipo').objects.create(expediente=exp,fecha='2026-11-06T10:00:00-05:00',responsable=user,condicion_ingreso='QA',snapshot={'numero':exp.numero})
    from masiscam.test_recepcion import foto
    image=foto().read()
    settings.MEDIA_ROOT.mkdir(parents=True,exist_ok=True)
    (settings.MEDIA_ROOT/'foto-qa.png').write_bytes(image)
    photo=m('Documento').objects.create(proyecto=proyecto,equipo=equipo,categoria='FOTOGRAFIAS',titulo='QA',archivo='foto-qa.png',nombre_original='foto-qa.png',tipo_mime='image/png',tamano=len(image),hash_sha256=hashlib.sha256(image).hexdigest(),subido_por=user)
    ev=m('EvidenciaRecepcion').objects.create(recepcion=rec,documento=photo,tipo='FOTO')
    m('FotoRecepcionClasificada').objects.create(evidencia=ev,clase='GENERAL')
    from masiscam.recepcion_pdf import construir_guia
    snap=dict(numero=exp.numero,version=1,plantilla='recepcion-v3',pendiente_validacion=False,politica='recepcion-2026-11-06',empresa='QA',cliente='QA',equipo='QA',placa='SIN_PLACA',motivo_sin_placa='QA sin placa',motivo_sin_guia='QA sin guía',confirmado_por='QA',confirmado_en='2026-11-06T10:00:00-05:00')
    pdf=construir_guia(snap)
    (settings.MEDIA_ROOT/'guia-qa.pdf').write_bytes(pdf)
    doc=m('Documento').objects.create(proyecto=proyecto,equipo=equipo,categoria='INFORMES',titulo='QA',archivo='guia-qa.pdf',nombre_original='guia-qa.pdf',tipo_mime='application/pdf',tamano=len(pdf),hash_sha256=hashlib.sha256(pdf).hexdigest(),subido_por=user,drive_file_id='FILE-QA-NO-REAL')
    em=m('EmisionGuiaIngreso').objects.create(expediente=exp,version=1,snapshot=snap,documento=doc,hash_sha256=doc.hash_sha256,estado='GENERADA',creado_por=user)
    m('ConfirmacionRecepcion').objects.create(recepcion=rec,guia=em,clave=em.clave,huella='a'*64,placa='SIN_PLACA',motivo_sin_placa='QA',motivo_sin_guia='QA',politica=snap['politica'],responsable=user,confirmado_en='2026-11-06T10:00:00-05:00')
    from django.utils import timezone
    from io import BytesIO
    from reportlab.pdfgen.canvas import Canvas
    def pdf_doc(name,category):
        stream=BytesIO(); c=Canvas(stream); c.drawString(40,750,name+' FICTICIO QA');c.save()
        data=stream.getvalue(); (settings.MEDIA_ROOT/name).write_bytes(data)
        return m('Documento').objects.create(proyecto=proyecto,equipo=equipo,categoria=category,titulo=name,archivo=name,
            nombre_original=name,tipo_mime='application/pdf',tamano=len(data),hash_sha256=hashlib.sha256(data).hexdigest(),subido_por=user,
            drive_file_id='DRIVE-FICTICIO-'+name)
    reviewer=apps.get_model('auth','User').objects.create(username='QA-REVISOR-INDEPENDIENTE',password='!')
    m('AsignacionTecnica').objects.create(expediente=exp,tecnico=user,asignado_por=reviewer,motivo='Fixture QA')
    i=m('Inspeccion').objects.create(expediente=exp,revision=1,tecnico=user,fecha='2026-11-06',estado_fisico='QA',condiciones_generales='QA',hallazgos='QA')
    m('EvidenciaTecnica').objects.create(inspeccion=i,documento=photo,clase='ESTADO_FISICO',leyenda='Imagen QA histórica',autor=user)
    i.enviado_en=timezone.now();i.estado='FINALIZADO';i.save()
    m('EtapaExpediente').objects.create(expediente=exp,codigo='INSPECCION',estado='FINALIZADO',inspeccion=i)
    d=m('Diagnostico').objects.create(expediente=exp,revision=1,tecnico=user,inspeccion=i,fecha='2026-11-06',fallas='QA',causas='No determinadas QA',
        componentes_afectados='QA',recomendaciones='QA',conclusiones='QA',repuestos='REQUERIDOS')
    plan=pdf_doc('plano-historico-qa.pdf','PLANOS')
    ref=m('ReferenciaTecnica').objects.create(empresa=empresa,documento=plan,codigo='QA-HIST',revision=1,tipo='PLANO',titulo='Fuente ficticia QA',autor=reviewer,autorizacion_uso='Solo QA')
    component=m('ComponenteTecnico').objects.create(empresa=empresa,codigo='QA-C',descripcion='Componente QA',autor=reviewer)
    part=m('ParteReferenciaTecnica').objects.create(referencia=ref,componente=component,posicion='A',descripcion='Parte QA',numero_parte='QA',unidad='unidad declarada')
    ref.estado='PUBLICADA';ref.save()
    m('AplicabilidadReferenciaEquipo').objects.create(referencia=ref,equipo=equipo,fundamento='Aplicabilidad QA',autor=reviewer)
    m('EvidenciaTecnica').objects.create(diagnostico=d,documento=plan,referencia=ref,clase='REFERENCIA',leyenda='Fuente QA',autor=user)
    m('PiezaRequerida').objects.create(diagnostico=d,parte=part,origen='BIBLIOTECA',descripcion='Pieza QA',numero_parte='QA',cantidad=1,unidad='unidad declarada',condicion='QA',accion='QA',snapshot={'fuente':'QA'})
    d.enviado_en=timezone.now();d.estado='PENDIENTE_APROBACION';d.save()
    etapa=m('EtapaExpediente').objects.create(expediente=exp,codigo='DIAGNOSTICO',estado='PENDIENTE_APROBACION',diagnostico=d)
    m('DecisionTecnica').objects.create(diagnostico=d,decision='APROBADO',responsable=reviewer)
    d.estado='APROBADO';d.save();etapa.estado='APROBADO';etapa.save()
    initial=pdf_doc('informe-historico-qa.pdf','INFORMES')
    m('EmisionInformeTecnico').objects.create(expediente=exp,diagnostico=d,version=1,huella='b'*64,snapshot={'historico':'QA'},documento=initial,hash_sha256=initial.hash_sha256,estado='GENERADA',autor=user)
    d.estado='FINALIZADO';d.save();etapa.estado='FINALIZADO';etapa.save()
    m('Auditoria').objects.create(empresa=empresa,expediente=exp,usuario=user,accion='QA_HISTORICO',objeto_tipo='Expediente',detalle={'sintetico':True})
    # Historical phase-4 rows are explicitly fictional QA. The fixture policy
    # does not match any operational configuration and never authorizes work.
    prop=m('PropuestaComercial').objects.create(expediente=exp,autor=reviewer,diagnostico=d,informe=m('EmisionInformeTecnico').objects.get(diagnostico=d),
        version=1,alcance='FICTICIO QA SIN VALIDEZ COMERCIAL',snapshot={'sintetico':True})
    auth=m('DecisionComercial').objects.create(expediente=exp,autor=reviewer,propuesta=prop,decision='APROBADO',fecha_cliente=timezone.now(),
        representante_cliente='PERSONA FICTICIA QA',motivo='Fixture histórica, no aceptación real',politica={'FICTICIA_QA_NO_OPERATIVA':True})
    received=m('RecepcionRepuesto').objects.create(expediente=exp,autor=user,pieza=m('PiezaRequerida').objects.get(diagnostico=d),
        procedencia='QA',entregado_por='QA',numero_parte='QA',descripcion='Pieza ficticia QA',cantidad=1,unidad='unidad declarada',fecha=timezone.now(),snapshot={'QA':True})
    a=m('ActividadReparacion').objects.create(expediente=exp,autor=user,autorizacion=auth,trabajo='Trabajo histórico FICTICIO QA',componentes='QA',inicio=timezone.now())
    m('UsoRepuesto').objects.create(expediente=exp,autor=user,actividad=a,recepcion=received,cantidad=1)
    m('EvidenciaOperativa').objects.create(expediente=exp,autor=user,documento=photo,actividad=a,clase='ANTES',leyenda='Foto QA histórica')
    rr=m('ResultadoReparacion').objects.create(expediente=exp,autor=user,actividad=a,version=1,fin=timezone.now(),trabajo_realizado='FICTICIO QA',resultado='No industrial',snapshot={'QA':True})
    m('RevisionReparacion').objects.create(expediente=exp,autor=reviewer,resultado=rr,decision='APROBADO',motivo='SOLO QA')
    repair_doc=pdf_doc('reparacion-historica-qa.pdf','INFORMES')
    m('EmisionOperativa').objects.create(expediente=exp,autor=user,tipo='REPARACION',version=1,huella='c'*64,snapshot={'QA':True},documento=repair_doc,
        hash_sha256=repair_doc.hash_sha256,estado='GENERADA')
    before_file.write_text(json.dumps(dict(rows=inventory(),files=files()),indent=2),encoding='utf-8')
    if sqlite:
        with sqlite3.connect(out/'backup-0024-sintetico.sqlite3') as backup:
            connection.connection.backup(backup)
    print('0024 seeded with historical fixture and persistent files. Take QA backup before upgrade.')
if '--upgrade' in sys.argv or sqlite:
    before=json.loads(before_file.read_text(encoding='utf-8'))
    executor=MigrationExecutor(connection)
    executor.migrate([('masiscam','0026_guardas_calibracion')])
    assert inventory()==before['rows'],'Historical rows changed'
    assert files()==before['files'],'Historical files changed'
    with connection.cursor() as cursor:
        for table in ('plantillacalibracion','calibracion','medicioncalibracion','decisioncalibracion','emisioncalibracion'):
            cursor.execute('SELECT COUNT(*) FROM masiscam_'+table)
            assert cursor.fetchone()[0]==0
    report=dict(database=kind,from_migration='0024',to_migration='0026',historical_rows_unchanged=True,
        files_and_hashes_unchanged=True,no_backfill=True,drive_real=False,tables_verified=len(tables),
        phase3_historical_rows=True,phase4_historical_rows=True)
    (out/f'migracion-{kind}-resultados.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
connection.close()

"""Rehearse 0018 -> phase 3 with synthetic phase-2 rows and media only.

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
out=ROOT/'artifacts'/'fase3-qa'
out.mkdir(parents=True,exist_ok=True)
if sqlite:
    work=Path(tempfile.mkdtemp(prefix='masiscam-fase3-upgrade-'))
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':work/'qa.sqlite3'}}
else:
    assert os.environ.get('MASISCAM_TEST_POSTGRES')
    assert settings.DATABASES['default']['NAME']=='masiscam_qa_fase3'
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
    target=[('masiscam','0018_confirmacion_guardas')]
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
    before_file.write_text(json.dumps(dict(rows=inventory(),files=files()),indent=2),encoding='utf-8')
    if sqlite:
        with sqlite3.connect(out/'backup-0018-sintetico.sqlite3') as backup:
            connection.connection.backup(backup)
    print('0018 seeded with historical fixture and persistent files. Take QA backup before upgrade.')
if '--upgrade' in sys.argv or sqlite:
    before=json.loads(before_file.read_text(encoding='utf-8'))
    executor=MigrationExecutor(connection)
    executor.migrate([('masiscam','0022_guardas_citas')])
    assert inventory()==before['rows'],'Historical rows changed'
    assert files()==before['files'],'Historical files changed'
    with connection.cursor() as cursor:
        for table in ('inspeccion','diagnostico','referenciatecnica','emisioninformetecnico'):
            cursor.execute('SELECT COUNT(*) FROM masiscam_'+table)
            assert cursor.fetchone()[0]==0
    report=dict(database=kind,from_migration='0018',to_migration='0022',historical_rows_unchanged=True,
        files_and_hashes_unchanged=True,no_backfill=True,drive_real=False)
    (out/f'migracion-{kind}-resultados.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
connection.close()

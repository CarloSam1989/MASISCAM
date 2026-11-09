"""Synthetic backup/restore rehearsal; explicit disposable loopback DB only."""
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE']='config.recepcion_test_settings'
assert os.environ.get('MASISCAM_TEST_POSTGRES'),'Explicit isolated QA URI required'
import django
from django.conf import settings
db=settings.DATABASES['default']
assert db['HOST']=='127.0.0.1' and db['NAME'] in {'masiscam_qa_revision','masiscam_qa_restauracion'}
out=ROOT/'artifacts/estado-final-qa';out.mkdir(parents=True,exist_ok=True)
settings.GOOGLE_DRIVE_ENABLED=False
settings.MEDIA_ROOT=out/('media-restaurada' if '--verify-restored' in sys.argv else 'media-seed')
django.setup()
from django.core.management import call_command
from django.db import connection
from django.test import override_settings,Client
from django.urls import reverse
from masiscam.models import Documento,ExpedienteMantenimiento,InformeFinal,Equipo,CierreExpediente
from masiscam.test_cierre import FLAGS,preparar_final
from masiscam.test_calibracion import anchor_qa
from masiscam.test_tecnica import usuario
from masiscam import cierre_services as s,calibracion_services as c,operacion_services as o
from masiscam.recepcion_services import verificar_copia_persistente,abrir_recepcion
from masiscam.test_recepcion import argumentos


def inventory():
    rows={}
    with connection.cursor() as cursor:
        for name in connection.introspection.table_names():
            if name=='django_migrations': continue
            cursor.execute(f'SELECT * FROM {connection.ops.quote_name(name)} ORDER BY 1')
            rows[name]=[list(map(str,r)) for r in cursor.fetchall()]
    return rows


def files():
    result={}
    for path in settings.MEDIA_ROOT.rglob('*'):
        if path.is_file(): result[path.relative_to(settings.MEDIA_ROOT).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    for doc in Documento.objects.all(): verificar_copia_persistente(doc)
    return result


def schema():
    with connection.cursor() as cursor:
        cursor.execute("SELECT t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname='public' ORDER BY t.tgname")
        triggers=cursor.fetchall()
        cursor.execute("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        sequences=cursor.fetchall()
    return dict(triggers=triggers,sequences=sequences)


def write(name,data):
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


if '--seed' in sys.argv:
    assert db['NAME']=='masiscam_qa_revision'
    call_command('migrate','contenttypes',verbosity=0);call_command('migrate','auth',verbosity=0)
    call_command('migrate','masiscam','0028',verbosity=0)
    call_command('migrate','admin',verbosity=0);call_command('migrate','sessions',verbosity=0)
    assert not ExpedienteMantenimiento.objects.exists(),'Never seed a populated database'
    with override_settings(**FLAGS):
        empresa,office,planta,tech,exp,p=preparar_final()
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(p)]):
            cal=exp.calibraciones.get()
            ce=c.reservar_informe(usuario=office,empresa=empresa,calibracion_id=cal.pk,clave=uuid.uuid4());c.generar_informe(ce.pk)
            re=o.reservar_documento(usuario=office,empresa=empresa,expediente_id=exp.pk,tipo='REPARACION',clave=uuid.uuid4());o.generar_documento(re.pk)
            em=s.reservar(usuario=office,empresa=empresa,expediente_id=exp.pk,clave=uuid.uuid4());s.generar(em.pk)
            s.revisar(usuario=planta,empresa=empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='REVISIÓN FICTICIA QA SIN VALIDEZ INDUSTRIAL')
            manager=usuario(empresa,'gerente-restauracion-qa','GERENCIA')
            s.cerrar(usuario=manager,empresa=empresa,informe_id=em.pk,clave=uuid.uuid4(),motivo='CIERRE FICTICIO QA - NO OPERACIÓN REAL')
    write('inventario-0028.json',dict(rows=inventory(),files=files(),expediente=exp.pk,qr=exp.registro.equipo.token_publico))
    print('Synthetic 0028 seeded, writers stopped; take native QA backup before --upgrade.')
elif '--capture-seed' in sys.argv:
    assert db['NAME']=='masiscam_qa_revision'
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_migrations WHERE app='masiscam' AND name LIKE '0029%'")
        assert cursor.fetchone()[0]==0
    exp=ExpedienteMantenimiento.objects.get()
    assert CierreExpediente.objects.filter(expediente=exp).exists()
    write('inventario-0028.json',dict(rows=inventory(),files=files(),expediente=exp.pk,qr=exp.registro.equipo.token_publico))
    print('Captured existing complete synthetic 0028 seed without repeating the workflow.')
elif '--upgrade' in sys.argv:
    assert db['NAME']=='masiscam_qa_revision'
    before=json.loads((out/'inventario-0028.json').read_text(encoding='utf-8'))
    call_command('migrate',verbosity=0)
    assert inventory()==before['rows'] and files()==before['files'],'Historical data changed'
    write('inventario-0029.json',dict(rows=inventory(),files=files(),schema=schema(),qr=before['qr']))
    write('migracion-0028-0029.json',dict(success=True,additive=True,historical_tables=len(before['rows']),rows_unchanged=True,files_unchanged=True,real_data=False))
    print('0028 -> 0029: all historical rows, files and hashes unchanged. Take final QA backup.')
elif '--verify-restored' in sys.argv:
    assert db['NAME']=='masiscam_qa_restauracion'
    before=json.loads((out/'inventario-0029.json').read_text(encoding='utf-8'))
    assert inventory()==before['rows'],'Restored rows differ'
    assert files()==before['files'],'Restored files differ'
    assert json.loads(json.dumps(schema()))==before['schema'],'Restored guards/sequences differ'
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    assert not executor.migration_plan(executor.loader.graph.leaf_nodes()),'Pending migrations after restore'
    exp=ExpedienteMantenimiento.objects.get()
    assert exp.registro.equipo.token_publico==before['qr'] and CierreExpediente.objects.filter(expediente=exp).exists()
    em=InformeFinal.objects.get(expediente=exp);client=Client();client.force_login(em.autor)
    with override_settings(**FLAGS,MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(em.calibracion.plantilla)]):
        response=client.get(reverse('masiscam:cierre_documento',args=[exp.pk,em.pk]))
        assert response.status_code==200
        assert hashlib.sha256(b''.join(response.streaming_content)).hexdigest()==em.hash_sha256
        data=argumentos(exp.empresa,em.autor,exp.cliente_contractual,exp.registro.equipo)
        new=abrir_recepcion(**data);retry=abrir_recepcion(**data)
        assert new.pk==retry.pk and new.correlativo==exp.correlativo+1
        exp.registro.equipo.refresh_from_db();assert exp.registro.equipo.token_publico==before['qr']
    write('restauracion.json',dict(success=True,rows_identical=True,files_and_hashes_identical=True,guards_and_sequences_identical=True,
        final_pdf_download_verified=True,qr_unchanged=True,numbering_and_idempotency_verified=True,pending_migrations=False,
        real_data=False,drive_real=False,tables_verified=len(before['rows']),files_verified=len(before['files'])))
    print('Restore verified: rows, media, PDF hash/download, SQL guards, sequences, QR and next maintenance idempotency.')
else: raise SystemExit('Choose --seed, --upgrade or --verify-restored; explicit disposable QA URI required.')
connection.close()

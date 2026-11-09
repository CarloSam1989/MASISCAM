"""Synthetic HTTP, PDF and responsive QA, never active DB/Drive/policy."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE']='config.recepcion_test_settings'
os.environ.pop('MASISCAM_TEST_POSTGRES',None)
import django
from django.conf import settings
settings.GOOGLE_DRIVE_ENABLED=False
settings.MASISCAM_RECEPCION_LOCAL=True
settings.MASISCAM_TECNICA_LOCAL=True
settings.MASISCAM_OPERACION_LOCAL=True
assert settings.DATABASES['default']['NAME']==':memory:'
out=ROOT/'artifacts'/'fase4-qa'
out.mkdir(parents=True,exist_ok=True)
settings.MEDIA_ROOT=out/'media-sintetica'
django.setup()
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.test import Client,override_settings
from django.urls import reverse
from django.utils import timezone
from masiscam.test_tecnica import preparar,usuario,pdf_plano
from masiscam.test_operacion import aprobar_diagnostico,QA_POLICY
from masiscam import operacion_services as s
from masiscam.models import PropuestaComercial,DecisionComercial,RecepcionRepuesto,ActividadReparacion,ResultadoReparacion,EmisionOperativa
from masiscam.operacion_tasks import generar_documento_operativo
from masiscam.operacion_pdf import construir_documento
from generar_muestra_guia_ingreso import ilustracion

call_command('migrate',verbosity=0)
empresa,oficina,planta,tecnico,exp=preparar()
gerencia=usuario(empresa,'gerencia-demo-ficticia','GERENCIA')
d=aprobar_diagnostico(empresa,oficina,planta,tecnico,exp)
token=exp.registro.equipo.token_publico
receipt_doc=exp.recepcion.confirmacion.guia.documento
initial_hash=receipt_doc.hash_sha256
pages={};client=Client()
detail=reverse('masiscam:operacion_detalle',args=[exp.pk])
def login(user): client.force_login(user)
def form(op): return reverse('masiscam:operacion_operar',args=[exp.pk,op])
def post(op,**data):
    data.setdefault('clave',str(uuid.uuid4()))
    response=client.post(form(op),data)
    assert response.status_code==302,(op,response.status_code,response.content.decode()[-12000:])
    return response

login(oficina)
pages['recepcion-repuesto']=client.get(form('repuesto')).content.decode()
from django.core.files.uploadedfile import SimpleUploadedFile
image=ilustracion()
post('repuesto',procedencia='Origen ficticio, solo QA',entregado_por='Entregador ficticio QA',numero_parte='DEMO-SIN-USO-REAL',
    descripcion='Componente demostrativo sin especificación industrial',cantidad='1',unidad='unidad declarada QA',fecha='2026-11-06T10:00',
    guia='DEMO-NO-VALIDA',discrepancia='Recepción no vinculada a pieza requerida; escenario QA',archivo=pdf_plano(),
    fotografia=SimpleUploadedFile('ilustracion-qa.png',image,content_type='image/png'))
r=RecepcionRepuesto.objects.get(expediente=exp)
login(gerencia)
pages['propuesta-comercial']=client.get(form('propuesta')).content.decode()
post('propuesta',diagnostico_id=d.pk,alcance='Alcance FICTICIO QA. No representa oferta ni trabajo real.',observaciones='Solo prueba de interfaz y trazabilidad')
p=PropuestaComercial.objects.get(expediente=exp)
pages['aceptacion-bloqueada']=client.get(form('decision')).content.decode()
assert 'value="APROBADO"' not in pages['aceptacion-bloqueada']
login(tecnico)
pages['operacion-bloqueada']=client.get(detail).content.decode()
response=client.post(form('actividad'),dict(clave=str(uuid.uuid4()),trabajo='Intento QA bloqueado',componentes='QA',inicio='2026-11-06T11:00'))
assert response.status_code==400
assert not ActividadReparacion.objects.exists()

# Approved-policy fixture ONLY within this in-memory QA context, never settings
# files, migration seeds, owner authorization or production configuration.
with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY):
    login(gerencia)
    post('decision',propuesta_id=p.pk,decision='APROBADO',fecha_cliente='2026-11-06T10:30',
        representante_cliente='PERSONA FICTICIA QA - SIN ACEPTACIÓN REAL',motivo='Fixture de prueba aislada. No constituye autorización del propietario ni de un cliente.',archivo=pdf_plano())
    login(tecnico)
    post('actividad',trabajo='Actividad FICTICIA QA, sin procedimiento industrial',componentes='Componente demostrativo',inicio='2026-11-06T11:00',observaciones='Sin ejecución física real')
    a=ActividadReparacion.objects.get(expediente=exp)
    pages['actividad-tecnico']=client.get(form('resultado')).content.decode()
    post('uso',actividad_id=a.pk,recepcion_id=r.pk,cantidad='1',observaciones='Trazabilidad ficticia QA')
    for clase in ('ANTES','DURANTE','DESPUES'):
        post('foto',actividad_id=a.pk,clase=clase,leyenda=f'{clase}: ilustración sintética, no equipo real',
            archivo=SimpleUploadedFile('ilustracion-qa.png',image,content_type='image/png'))
    post('resultado',actividad_id=a.pk,fin='2026-11-06T12:00',trabajo_realizado='Trabajo ficticio para probar el registro. No se ejecutó reparación industrial.',
        resultado='Escenario de software verificado; sin conclusión industrial.',observaciones='Sin calibración ni aprobación técnica final')
    result=ResultadoReparacion.objects.get(actividad=a)
    login(planta)
    pages['revision-planta']=client.get(form('revision')).content.decode()
    post('revision',resultado_id=result.pk,decision='APROBADO',motivo='Revisión FICTICIA QA independiente, solo prueba de software.')
    assert s.estado(exp)=='REPARACION_FINALIZADA'
    login(oficina)
    emissions=[]
    for tipo in ('REPUESTOS','REPARACION'):
        # Reserve through actual HTTP; a real broker/worker is deliberately not
        # needed. Existing queue uses the sandbox memory broker.
        post('documento',tipo=tipo,motivo='')
        em=EmisionOperativa.objects.get(expediente=exp,tipo=tipo)
        doc_id=generar_documento_operativo.run(em.pk)
        em.refresh_from_db()
        response=client.get(reverse('masiscam:operacion_documento',args=[exp.pk,doc_id]))
        assert response.status_code==200
        binary=b''.join(response.streaming_content)
        assert hashlib.sha256(binary).hexdigest()==em.hash_sha256
        (out/f'{tipo.lower()}-persistido-qa.pdf').write_bytes(binary)
        emissions.append(em)
    pages['operacion-revisada-qa']=client.get(detail).content.decode()
assert s.politica() is None
assert s.estado(exp)=='OBSERVADO'  # The temporary QA policy is not active.

from pypdf import PdfReader
from io import BytesIO
import pymupdf
artifacts=[]
for em in emissions:
    snapshot=dict(em.snapshot)
    snapshot.update(demostrativo=True,numero='EXP-DEMO-SIN-VALIDEZ',recepcion=dict(cliente='CLIENTE FICTICIO DEMOSTRATIVO',equipo='EQUIPO FICTICIO - SIN DATOS INDUSTRIALES'))
    pictures=[]
    for e in snapshot['evidencias']:
        if e['clase']!='DOCUMENTO':
            pictures.append((e,image))
    binary=construir_documento(snapshot,pictures)
    filename='MASISCAM_'+('Repuestos' if em.tipo=='REPUESTOS' else 'Reparacion')+'_DEMONSTRATIVO.pdf'
    target=ROOT.parent/'output'/'pdf'/filename
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(binary)
    reader=PdfReader(BytesIO(binary))
    assert 'DEMOSTRATIVO - DATOS FICTICIOS' in '\n'.join(page.extract_text() for page in reader.pages)
    assert not reader.get_fields()
    assert all(abs(float(page.mediabox.width)-595.276)<.02 and abs(float(page.mediabox.height)-841.89)<.02 for page in reader.pages)
    for n,page in enumerate(pymupdf.open(stream=binary,filetype='pdf'),1):
        page.get_pixmap(matrix=pymupdf.Matrix(1.1,1.1)).save(str(out/f'{em.tipo.lower()}-pagina-{n}.png'))
    artifacts.append(dict(tipo=em.tipo,version=em.version,persistent_sha256=em.hash_sha256,demo_pdf=str(target),
        demo_sha256=hashlib.sha256(binary).hexdigest(),pages=len(reader.pages)))

def inline_assets(html):
    def css(match):
        path=finders.find(match.group(1).removeprefix('/static/'))
        return '<style>'+Path(path).read_text(encoding='utf-8')+'</style>' if path else ''
    html=re.sub(r'<link[^>]*href="(/static/[^\"]+\.css)"[^>]*>',css,html)
    html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
    def img(match):
        path=finders.find(match.group(1).removeprefix('/static/'))
        return 'src="data:image/png;base64,'+base64.b64encode(Path(path).read_bytes()).decode()+'"' if path else match.group(0)
    return re.sub(r'src="(/static/[^\"]+)"',img,html)

from playwright.sync_api import sync_playwright
viewports=[]
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(channel='msedge',headless=True)
    try:
        for state,html in pages.items():
            for label,width in [('escritorio',1400),('tablet',768),('movil',390)]:
                page=browser.new_page(viewport={'width':width,'height':1000})
                page.route('**/*',lambda route:route.abort())
                page.set_content(inline_assets(html))
                metrics=page.locator('main').evaluate('e => ({width:e.clientWidth,scroll:e.scrollWidth})')
                assert metrics['scroll']<=width,(state,label,metrics)
                page.screenshot(path=str(out/f'{state}-{label}.png'),full_page=True)
                viewports.append(dict(state=state,viewport=label,**metrics))
                page.close()
    finally:
        browser.close()
with receipt_doc.archivo.open('rb') as f:
    assert hashlib.sha256(f.read()).hexdigest()==initial_hash
assert exp.registro.equipo.token_publico==token
report=dict(synthetic=True,real_db=False,drive_real=False,operational_policy_active=False,
    qr_unchanged=True,reception_pdf_unchanged=True,default_acceptance_and_repair_blocked=True,
    temporary_policy='QA fixture only; not an owner/client acceptance',documents=artifacts,viewports=viewports)
(out/'aceptacion-visual.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))

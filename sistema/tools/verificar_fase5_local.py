"""In-memory calibration acceptance with explicitly fictional protocols only."""
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
settings.MASISCAM_CALIBRACION_LOCAL=True
assert settings.DATABASES['default']['NAME']==':memory:'
out=ROOT/'artifacts'/'fase5-qa';out.mkdir(parents=True,exist_ok=True)
settings.MEDIA_ROOT=out/'media-sintetica'
django.setup()
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.test import Client,override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from masiscam.test_calibracion import preparar_calibracion,anchor_qa,validar_qa
from masiscam.test_operacion import QA_POLICY
from masiscam import calibracion_services as s
from masiscam.models import Calibracion,EmisionCalibracion
from masiscam.calibracion_tasks import generar_informe_calibracion
from masiscam.calibracion_pdf import construir_informe
from generar_muestra_guia_ingreso import ilustracion
call_command('migrate',verbosity=0)
pages={};client=Client();pdfs=[]
def login(user):client.force_login(user)
def post(url,**data):
    data.setdefault('clave',str(uuid.uuid4()))
    response=client.post(url,data)
    assert response.status_code==302,(response.status_code,response.content.decode()[-16000:])

assert getattr(settings,'MASISCAM_POLITICA_COMERCIAL_LOCAL',None) is None
assert not getattr(settings,'MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL',[])
with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY):
    empresa,oficina,planta,tecnico,exp,revisor,p=preparar_calibracion()
    token=exp.registro.equipo.token_publico
    historical=exp.recepcion.confirmacion.guia.documento
    historical_hash=historical.hash_sha256
    login(tecnico);expurl=reverse('masiscam:calibracion_expediente',args=[exp.pk])
    with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
        pages['inicio-bloqueado']=client.get(expurl).content.decode()
        response=client.post(expurl,dict(clave=str(uuid.uuid4()),plantilla_id=p.pk))
        assert response.status_code==400 and not Calibracion.objects.exists()
    with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(p)]):
        validar_qa(empresa,revisor,p)
        login(planta)
        pages['plantilla-validada-qa']=client.get(reverse('masiscam:calibracion_plantilla',args=[p.pk])).content.decode()
        login(tecnico)
        post(expurl,plantilla_id=p.pk)
        c=Calibracion.objects.get(expediente=exp)
        url=reverse('masiscam:calibracion_revision',args=[c.pk])
        pages['borrador-pendiente']=client.get(url).content.decode()
        when=timezone.localtime(timezone.now()).strftime('%Y-%m-%dT%H:%M')
        post(url,op='datos',version=c.version_edicion,fecha=when,instrumentos='INSTRUMENTO FICTICIO QA - NO ES INSTRUMENTO REAL NI CERTIFICADO',
            resultados='Escenario demostrativo de software, sin resultado industrial.',observaciones='No constituye calibración de un equipo real.',param_0='0')
        c.refresh_from_db()
        for point in p.definicion['mediciones']:
            post(url,op='medir',version=c.version_edicion,punto=point['codigo'],valor='0',observaciones='Valor ficticio QA sin interpretación industrial')
            c.refresh_from_db()
        picture=ilustracion()
        post(url,op='evidencia',version=c.version_edicion,clase='FOTO',leyenda='Ilustración DEMOSTRATIVA, no fotografía de ensayo real',
            archivo=SimpleUploadedFile('ilustracion-calibracion-qa.png',picture,content_type='image/png'))
        c.refresh_from_db()
        post(url,op='enviar',version=c.version_edicion)
        c.refresh_from_db();assert c.estado=='PENDIENTE_REVISION' and c.mediciones.count()==16
        pending=s.reservar_informe(usuario=oficina,empresa=empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        doc_id=generar_informe_calibracion.run(pending.pk);pending.refresh_from_db()
        login(oficina)
        response=client.get(reverse('masiscam:calibracion_documento',args=[exp.pk,doc_id]))
        assert response.status_code==200
        binary=b''.join(response.streaming_content);assert hashlib.sha256(binary).hexdigest()==pending.hash_sha256
        (out/'calibracion-pendiente-persistida-qa.pdf').write_bytes(binary)
        login(planta)
        pages['revision-independiente-qa']=client.get(url).content.decode()
        values={f'eval_{n}':'CONFORME' for n in range(16)}
        post(url,op='decision',decision='APROBADA',motivo='REVISIÓN FICTICIA QA. No es aprobación real de un ensayo ni del propietario.',**values)
        c.refresh_from_db();assert c.estado=='APROBADA'
        login(oficina)
        post(url,op='informe',nueva_version=True,motivo='Estado de revisión ficticia QA actualizado')
        approved=EmisionCalibracion.objects.get(calibracion=c,version=2)
        generar_informe_calibracion.run(approved.pk)
        approved.refresh_from_db();pages['revision-congelada-qa']=client.get(url).content.decode()
        pdfs=[dict(version=em.version,sha256=em.hash_sha256,estado=em.snapshot['estado']) for em in (pending,approved)]
    assert not s.plantilla_validada(p)
assert s.o.politica() is None
assert not getattr(settings,'MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL',[])

snapshot=dict(pending.snapshot)
snapshot.update(demostrativo=True,version=1,numero='EXP-DEMO-SIN-VALIDEZ',
    recepcion=dict(cliente='CLIENTE FICTICIO DEMOSTRATIVO',equipo='EQUIPO FICTICIO - SIN DATOS INDUSTRIALES'))
photo=snapshot['evidencias'][0]
binary=construir_informe(snapshot,[(photo,picture)])
target=ROOT.parent/'output'/'pdf'/'MASISCAM_Calibracion_DEMOSTRATIVO.pdf'
target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(binary)
from pypdf import PdfReader
from io import BytesIO
reader=PdfReader(BytesIO(binary));text='\n'.join(page.extract_text() for page in reader.pages)
assert 'DEMOSTRATIVO - DATOS FICTICIOS QA' in text and 'No existe aprobación técnica' in text
assert not reader.get_fields()
assert all(abs(float(page.mediabox.width)-595.276)<.02 and abs(float(page.mediabox.height)-841.89)<.02 for page in reader.pages)
import pymupdf
for n,page in enumerate(pymupdf.open(stream=binary,filetype='pdf'),1):
    page.get_pixmap(matrix=pymupdf.Matrix(1.1,1.1)).save(str(out/f'calibracion-pagina-{n}.png'))

def inline_assets(html):
    def css(match):
        path=finders.find(match.group(1).removeprefix('/static/'))
        return '<style>'+Path(path).read_text(encoding='utf-8')+'</style>' if path else ''
    html=re.sub(r'<link[^>]*href="(/static/[^\"]+\.css)"[^>]*>',css,html)
    html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
    def image(match):
        path=finders.find(match.group(1).removeprefix('/static/'))
        return 'src="data:image/png;base64,'+base64.b64encode(Path(path).read_bytes()).decode()+'"' if path else match.group(0)
    return re.sub(r'src="(/static/[^\"]+)"',image,html)
from playwright.sync_api import sync_playwright
viewports=[]
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(channel='msedge',headless=True)
    try:
        for state,html in pages.items():
            for label,width in [('escritorio',1400),('tablet',768),('movil',390)]:
                page=browser.new_page(viewport={'width':width,'height':1000})
                page.route('**/*',lambda route:route.abort());page.set_content(inline_assets(html))
                metrics=page.locator('main').evaluate('e => ({width:e.clientWidth,scroll:e.scrollWidth})')
                assert metrics['scroll']<=width,(state,label,metrics)
                page.screenshot(path=str(out/f'{state}-{label}.png'),full_page=True)
                viewports.append(dict(state=state,viewport=label,**metrics));page.close()
    finally:browser.close()
with historical.archivo.open('rb') as f:assert hashlib.sha256(f.read()).hexdigest()==historical_hash
assert exp.registro.equipo.token_publico==token
report=dict(synthetic=True,real_db=False,drive_real=False,commercial_policy_active=False,industrial_protocol_active=False,
    default_start_blocked=True,qr_unchanged=True,reception_pdf_unchanged=True,mediciones=16,
    persistent_documents=pdfs,demo_pdf=str(target),demo_sha256=hashlib.sha256(binary).hexdigest(),pages=len(reader.pages),viewports=viewports)
(out/'aceptacion-visual.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))

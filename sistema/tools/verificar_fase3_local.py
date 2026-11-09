"""Synthetic phase-3 HTTP, PDF and responsive acceptance; no real DB or Drive."""
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
assert settings.DATABASES['default']['NAME']==':memory:'
out=ROOT/'artifacts'/'fase3-qa'
out.mkdir(parents=True,exist_ok=True)
settings.MEDIA_ROOT=out/'media-sintetica'
django.setup()
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from masiscam.test_tecnica import preparar,pdf_plano
from masiscam import tecnica_services as s
from masiscam.models import Inspeccion,Diagnostico,EmisionInformeTecnico,Auditoria
from masiscam.tecnica_pdf import construir_informe

call_command('migrate',verbosity=0)
empresa,oficina,planta,tecnico,exp=preparar()
token=exp.registro.equipo.token_publico
doc_recepcion=exp.recepcion.confirmacion.guia.documento
hash_recepcion=doc_recepcion.hash_sha256
client=Client()
client.force_login(tecnico)
i=s.crear_revision(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,tipo='INSPECCION',clave=uuid.uuid4())
i=s.guardar_revision(usuario=tecnico,empresa=empresa,tipo='INSPECCION',revision_id=i.pk,version=1,clave=uuid.uuid4(),datos=dict(fecha=timezone.localdate(),estado_fisico='Descripción demostrativa del estado visible. No representa evaluación de un equipo real.',condiciones_generales='Condiciones generales ficticias, sin mediciones ni tolerancias simuladas.',hallazgos='Hallazgos de maquetación: identificación pendiente. No se afirma conformidad industrial.'))
s.agregar_evidencia(usuario=tecnico,empresa=empresa,tipo='INSPECCION',revision_id=i.pk,version=i.version_edicion,clave=uuid.uuid4(),documento_id=exp.recepcion.evidencias.get().documento_id,clase='ESTADO_FISICO',leyenda='Imagen sintética QA, no es fotografía industrial.')
i.refresh_from_db()
i=s.enviar_revision(usuario=tecnico,empresa=empresa,tipo='INSPECCION',revision_id=i.pk,version=i.version_edicion,clave=uuid.uuid4())
ref=s.guardar_referencia(usuario=planta,empresa=empresa,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(codigo='PLANO-DEMO',tipo='PLANO',titulo='Plano demostrativo sin contenido industrial',procedencia='Fuente sintética QA',autorizacion_uso='Uso sintético exclusivamente QA',proyecto_id=exp.registro.equipo.proyecto_id))
part_id=s.guardar_parte(usuario=planta,empresa=empresa,referencia_id=ref.pk,clave=uuid.uuid4(),datos=dict(posicion='DEMO-A',descripcion='Componente ficticio de maquetación',numero_parte='DEMO-PARTE',unidad=''))
s.estado_referencia(usuario=planta,empresa=empresa,referencia_id=ref.pk,estado='PUBLICADA',clave=uuid.uuid4())
s.aplicar_referencia(usuario=planta,empresa=empresa,referencia_id=ref.pk,equipo_id=exp.registro.equipo_id,fundamento='Aplicabilidad ficticia QA; sin uso industrial',clave=uuid.uuid4())
d=s.crear_revision(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,tipo='DIAGNOSTICO',clave=uuid.uuid4())
url=reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',d.pk])
payload=dict(op='guardar',clave=str(uuid.uuid4()),version=d.version_edicion,fecha=str(timezone.localdate()),
    fallas='Fallas pendientes de determinar. Documento demostrativo sin conclusión industrial.',
    causas='Hipótesis no validada; no se han inferido causas de un equipo real.',componentes_afectados='Componente ficticio; identificación de maquetación.',recomendaciones='Continuar evaluación según alcance autorizado. Esta muestra no prescribe una reparación.',
    conclusiones='Alcance exclusivamente demostrativo. Sin mediciones, límites o tolerancias inventados.',repuestos='REQUERIDOS')
response=client.post(url,payload)
assert response.status_code==302,(response.status_code,response.content[:500])
d.refresh_from_db()
response=client.post(url,dict(op='pieza',clave=str(uuid.uuid4()),version=d.version_edicion,origen='BIBLIOTECA',parte_id=part_id,cantidad='1',motivo_unidad_pendiente='La fuente ficticia no declara unidad.',condicion='Sin evaluación real',accion='Evaluación posterior; no autoriza reparación'))
assert response.status_code==302,response.content[:1000]
d.refresh_from_db()
pages={'diagnostico-borrador':client.get(url).content.decode(),'biblioteca-tecnico':client.get(reverse('masiscam:biblioteca')).content.decode()}
response=client.post(url,dict(op='enviar',clave=str(uuid.uuid4()),version=d.version_edicion))
assert response.status_code==302,response.content[:1000]
client.force_login(planta)
pages['revision-planta']=client.get(url).content.decode()
response=client.post(url,dict(op='decision',clave=str(uuid.uuid4()),decision='APROBADO',motivo='Decisión sintética QA, sin aprobación industrial real.'))
assert response.status_code==302,response.content[:1000]
client.force_login(oficina)
response=client.post(reverse('masiscam:tecnica_informe',args=[d.pk]),dict(clave=str(uuid.uuid4())))
assert response.status_code==302
em=EmisionInformeTecnico.objects.get(diagnostico=d)
from masiscam.tecnica_tasks import generar_informe_inicial
doc_id=generar_informe_inicial(em.pk)  # Local worker simulation, no broker/Drive.
em.refresh_from_db()
pages['expediente-informe']=client.get(reverse('masiscam:tecnica_expediente',args=[exp.pk])).content.decode()
response=client.get(reverse('masiscam:tecnica_documento',args=[exp.pk,doc_id]))
assert response.status_code==200
pdf=b''.join(response.streaming_content)
assert hashlib.sha256(pdf).hexdigest()==em.hash_sha256
(out/'informe-inicial-qa.pdf').write_bytes(pdf)
snapshot={**em.snapshot,'demostrativo':True,'cliente':'CLIENTE FICTICIO DEMOSTRATIVO','ruc':'DEMO-NO-VALIDO','empresa':'MASISCAM QA - DEMOSTRACIÓN',
    'potencia':'','ratio':'','rpm':''}
photos=[]
from generar_muestra_guia_ingreso import ilustracion
for n,e in enumerate(s._evidencias(d),1):
    image=ilustracion()
    photos.append((dict(documento_id=f'DEMO-{n}',hash=hashlib.sha256(image).hexdigest(),clase=e.clase,
        leyenda='Ilustración demostrativa; no es una fotografía industrial real.'),image))
demo=construir_informe(snapshot,photos)
target=ROOT.parent/'output'/'pdf'/'MASISCAM_Informe_Inicial_DEMOSTRATIVO.pdf'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_bytes(demo)
from pypdf import PdfReader
from io import BytesIO
reader=PdfReader(BytesIO(demo))
text='\n'.join(page.extract_text() for page in reader.pages)
assert 'DEMOSTRACIÓN - DATOS FICTICIOS' in text
assert 'Este informe no autoriza reparaciones' in text
assert not reader.get_fields()
assert all(abs(float(page.mediabox.width)-595.276)<.02 and abs(float(page.mediabox.height)-841.89)<.02 for page in reader.pages)
import pymupdf
for n,page in enumerate(pymupdf.open(stream=demo,filetype='pdf'),1):
    page.get_pixmap(matrix=pymupdf.Matrix(1.1,1.1)).save(str(out/f'informe-pagina-{n}.png'))

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
                page.route('**/*',lambda route:route.abort())
                page.set_content(inline_assets(html))
                metrics=page.locator('main').evaluate('e => ({width:e.clientWidth,scroll:e.scrollWidth})')
                assert metrics['scroll']<=width,(state,label,metrics)
                page.screenshot(path=str(out/f'{state}-{label}.png'),full_page=True)
                viewports.append(dict(state=state,viewport=label,**metrics))
                page.close()
    finally:
        browser.close()
with doc_recepcion.archivo.open('rb') as f:
    assert hashlib.sha256(f.read()).hexdigest()==hash_recepcion
assert exp.registro.equipo.token_publico==token
report=dict(synthetic=True,drive_real=False,documento=doc_id,version=em.version,sha256=em.hash_sha256,
    demo_pdf=str(target),demo_sha256=hashlib.sha256(demo).hexdigest(),pages=len(reader.pages),qr_unchanged=True,
    reception_pdf_unchanged=True,audit_events=Auditoria.objects.filter(expediente=exp).count(),viewports=viewports)
(out/'aceptacion-visual.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))

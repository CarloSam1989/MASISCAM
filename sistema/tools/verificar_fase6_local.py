"""Disposable in-memory acceptance, report sample and offline responsive QA."""
import base64
import copy
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
assert settings.DATABASES['default']['NAME']==':memory:'
for name in ('RECEPCION','TECNICA','OPERACION','CALIBRACION','CIERRE'): setattr(settings,f'MASISCAM_{name}_LOCAL',True)
settings.GOOGLE_DRIVE_ENABLED=False
out=ROOT/'artifacts/fase6-qa';out.mkdir(parents=True,exist_ok=True)
settings.MEDIA_ROOT=out/'media-demostrativa'
django.setup()
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.test import Client,override_settings
from django.urls import reverse
from masiscam.test_cierre import preparar_final
from masiscam.test_calibracion import anchor_qa
from masiscam.test_operacion import QA_POLICY
from masiscam.test_tecnica import usuario
from masiscam import cierre_services as s
from masiscam.cierre_pdf import construir_informe
from generar_muestra_guia_ingreso import ilustracion

call_command('migrate',verbosity=0)
assert s.o.politica() is None and not settings.MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL
pages={};client=Client()
with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY):
    empresa,oficina,planta,tecnico,exp,p=preparar_final()
    token=exp.registro.equipo.token_publico
    olddoc=exp.recepcion.confirmacion.guia.documento;oldhash=olddoc.hash_sha256
    client.force_login(oficina)
    url=reverse('masiscam:cierre_detalle',args=[exp.pk])
    with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
        pages['bloqueo-operativo']=client.get(url).content.decode()
        response=client.post(url,dict(op='reservar',clave=str(uuid.uuid4())))
        assert response.status_code==400
    with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(p)]):
        response=client.post(url,dict(op='reservar',clave=str(uuid.uuid4())))
        assert response.status_code==302
        em=exp.informes_finales.get()
        s.generar(em.pk);em.refresh_from_db()
        response=client.get(reverse('masiscam:cierre_documento',args=[exp.pk,em.pk]))
        assert response.status_code==200
        data=b''.join(response.streaming_content)
        assert hashlib.sha256(data).hexdigest()==em.hash_sha256
        (out/'informe-final-persistido-qa.pdf').write_bytes(data)
        client.force_login(planta);pages['revision-independiente']=client.get(url).content.decode()
        response=client.post(url,dict(op='revisar',informe=em.pk,clave=str(uuid.uuid4()),decision='APROBADO',motivo='Revisión FICTICIA QA, sin validez industrial'))
        assert response.status_code==302
        gerente=usuario(empresa,'gerencia-cierre-demo','GERENCIA');client.force_login(gerente)
        response=client.post(url,dict(op='cerrar',informe=em.pk,clave=str(uuid.uuid4()),motivo='Cierre FICTICIO QA, no operación real'))
        assert response.status_code==302
        pages['expediente-cerrado']=client.get(url).content.decode()
        pages['dashboard']=client.get(reverse('masiscam:cierre_dashboard')).content.decode()
        pages['historial']=client.get(reverse('masiscam:cierre_historial',args=[exp.registro.equipo_id])).content.decode()
        em.refresh_from_db();assert em.estado=='FINALIZADO'
        demo=copy.deepcopy(em.snapshot)
assert s.o.politica() is None and not settings.MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL
demo.update(demostrativo=True,numero='EXP-DEMO-SIN-VALIDEZ',version=1)
demo['recepcion']['numero']=demo['numero']
demo['recepcion']['cliente']='CLIENTE FICTICIO DEMOSTRATIVO'
demo['recepcion']['equipo']='EQUIPO FICTICIO - SIN VALIDEZ INDUSTRIAL'
picture=ilustracion()
photo=dict(documento='DEMO',hash=hashlib.sha256(picture).hexdigest(),seccion='Recepción',leyenda='Ilustración demostrativa; no fotografía de un equipo real.',foto=True)
demo['evidencias'].append(photo)
target=ROOT.parent/'output/pdf/MASISCAM_Informe_Final_DEMOSTRATIVO.pdf'
if '--preservar-muestra' in sys.argv and target.exists():
    binary=target.read_bytes()
else:
    binary=construir_informe(demo,[(photo,picture)])
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(binary)
from pypdf import PdfReader
from io import BytesIO
reader=PdfReader(BytesIO(binary));text='\n'.join(p.extract_text() for p in reader.pages)
assert 'DEMOSTRATIVO - DATOS FICTICIOS QA' in text and 'QA-16' in text
assert not reader.get_fields()
assert all(abs(float(p.mediabox.width)-595.276)<.02 and abs(float(p.mediabox.height)-841.89)<.02 for p in reader.pages)
import pymupdf
for n,page in enumerate(pymupdf.open(stream=binary,filetype='pdf'),1):
    page.get_pixmap(matrix=pymupdf.Matrix(1.1,1.1)).save(str(out/f'informe-final-pagina-{n}.png'))

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
    finally: browser.close()
with olddoc.archivo.open('rb') as f: assert hashlib.sha256(f.read()).hexdigest()==oldhash
exp.registro.equipo.refresh_from_db();assert exp.registro.equipo.token_publico==token
report=dict(synthetic=True,real_db=False,drive_real=False,default_closure_blocked=True,
    commercial_policy_active=False,industrial_protocol_active=False,qr_unchanged=True,old_pdf_unchanged=True,
    persistent_final_hash=em.hash_sha256,final_state_qa=em.estado,demo_pdf=str(target),
    demo_sha256=hashlib.sha256(binary).hexdigest(),pages=len(reader.pages),viewports=viewports)
(out/'aceptacion-visual.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))

"""Inventario de rutas y recorrido visual sobre datos ficticios, sin red externa."""
import json
import os
from pathlib import Path
from contextlib import nullcontext
from unittest import skipUnless
from urllib.parse import urlsplit
from html.parser import HTMLParser
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.test.utils import CaptureQueriesContext
from django.db import connection
from . import test_revision_administrativa as revision
from . import test_oficina_gestion as oficina
from .models import (RolMasiscam,Cliente,Calibracion,PlantillaCalibracion,Documento,InformeFinal,
    EmisionGuiaIngreso,EmisionInformeTecnico,Inspeccion,EnvioRepuestos)
from .urls import urlpatterns
from .operacion_forms import AREAS


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.append(dict(attrs).get('href',''))


class InventarioFixture(oficina.OficinaFixture):
    revisar=revision.RevisionAdministrativaTests.revisar
    def recepcion(self,equipo):
        from io import BytesIO
        import uuid
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils import timezone
        from .recepcion_services import abrir_recepcion
        contenido=BytesIO()
        Image.new('RGB',(40,30),(equipo.pk % 255,90,120)).save(contenido,'PNG')
        foto=SimpleUploadedFile('recepcion-qa.png',contenido.getvalue(),content_type='image/png')
        return abrir_recepcion(usuario=self.taller,empresa=self.empresa,equipo_id=equipo.pk,
            cliente_id=self.cliente.pk,clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),
            condicion_ingreso='Recepcion aislada QA',fotografias=[foto])
    def inventario(self):
        revision.RevisionAdministrativaTests.test_informe_final_conserva_aprobaciones_independientes(self)
        self.roles={'ADMIN':self.admin,'TALLER':self.taller,'OFICINA':self.oficina}
        original={key:getattr(self,key) for key in ('equipo','exp','diag','sol','pieza','ref','parte','cliente')}
        self.cliente=Cliente.objects.create(empresa=self.empresa,ruc='UI-BORRADOR-QA',razon_social='Cliente borrador QA',nombre_comercial='Borrador QA')
        self.preparar_solicitud(enviar=False,con_piezas=False)
        self.borrador_exp,self.borrador_diag=self.exp,self.diag
        from .plano_services import cargar_plano_trabajo
        import uuid
        from django.core.files.uploadedfile import SimpleUploadedFile
        pdf=oficina.pdf_plano()
        archivo=SimpleUploadedFile('borrador-qa.pdf',pdf.read()+b'\n% Borrador aislado QA\n',content_type='application/pdf')
        self.borrador_ref=cargar_plano_trabajo(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
            archivo=archivo,clave=uuid.uuid4())
        for key,value in original.items():setattr(self,key,value)

    def rutas(self):
        for route in urlpatterns:
            pattern=str(route.pattern);kw={}
            for key in route.pattern.converters:
                if key=='pk':
                    value=(self.equipo.proyecto_id if pattern.startswith('proyectos/') else
                        self.equipo.cliente_id if pattern.startswith('clientes/') else
                        RolMasiscam.objects.get(perfil__user=self.otro).pk if pattern.startswith('usuarios/') else
                        self.ref.pk if pattern.startswith('biblioteca-tecnica/') else
                        PlantillaCalibracion.objects.first().pk if pattern.startswith('calibracion/plantillas/') else
                        Calibracion.objects.first().pk if pattern.startswith('calibracion/revisiones/') else
                        self.diag.pk if pattern.startswith('tecnica/') else self.equipo.pk if pattern.startswith(('equipos/','taller/equipos/')) else self.exp.pk)
                elif key=='tipo':value='reductor' if pattern.startswith('productos/') else 'DIAGNOSTICO' if 'tecnic' in pattern else 'equipo'
                elif key=='seccion':value='cotizaciones'
                elif key=='op':value='propuesta'
                elif key=='equipo_pk':value=self.equipo.pk
                elif key=='registro_pk':value=self.exp.registro_id
                elif key=='informe_pk':value=InformeFinal.objects.first().pk
                elif key=='guia_pk':value=EmisionGuiaIngreso.objects.first().pk if EmisionGuiaIngreso.objects.exists() else 999999
                elif key=='archivo_id':value='archivo-qa-inexistente'
                elif key=='documento_pk':
                    value=(self.diag.piezas.get().fotografias.first().documento_id if '/flujo/' in pattern else
                        EmisionInformeTecnico.objects.first().documento_id if '/tecnico/' in pattern else
                        self.exp.recepcion.evidencias.first().documento_id if '/documentos/' in pattern and '/operacion/' not in pattern and '/calibracion/' not in pattern else
                        Documento.objects.filter(evidencias_operativas__propuesta__expediente=self.exp).first().pk)
                else:raise AssertionError((route.name,key))
                kw[key]=value
            yield route.name,reverse('masiscam:'+route.name,kwargs=kw),route.callback
            if 'pk' in kw and pattern.startswith('expedientes/'):
                variante=dict(kw,pk=self.borrador_exp.pk)
                yield route.name+'_borrador',reverse('masiscam:'+route.name,kwargs=variante),route.callback
        for section in ('solicitudes','compras','ventas','guias','documentos','cuenta'):
            yield 'oficina_'+section,reverse('masiscam:oficina_seccion',args=[section]),None
        for op in AREAS:
            yield 'operacion_'+op,reverse('masiscam:operacion_operar',args=[self.exp.pk,op]),None
        yield 'tecnica_inspeccion',reverse('masiscam:tecnica_revision',args=['INSPECCION',self.diag.inspeccion_id]),None
        yield 'administracion_avanzada',reverse('masiscam:tecnica_expediente',args=[self.exp.pk])+'?avanzado=1',None
        yield 'diagnostico_borrador',reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',self.borrador_diag.pk]),None
        yield 'plano_borrador',reverse('masiscam:plano',args=[self.borrador_ref.pk])+f'?diagnostico={self.borrador_diag.pk}&flujo=1',None

    def guardar(self,nombre,datos):
        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
            (Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/nombre).write_text(json.dumps(datos,ensure_ascii=False,indent=2),encoding='utf-8')


@override_settings(**oficina.FLAGS)
class AuditoriaInterfazTests(InventarioFixture,TestCase):
    def test_rutas_perfiles_y_enlaces_accesibles(self):
        self.inventario();resultados={};fallos=[]
        for role,user in self.roles.items():
            client=Client(raise_request_exception=False);client.force_login(user);rows=[];cache={}
            for name,url,callback in self.rutas():
                response=cache.get(url) or client.get(url,follow=True);cache[url]=response
                code=response.status_code;rows.append(dict(ruta=name,url=url,estado=code))
                if code>=500:fallos.append((role,name,url,code,response.content[:350] if not response.streaming else 'stream'))
                if code==200 and 'text/html' in response.get('Content-Type',''):
                    parser=Links();parser.feed(response.content.decode('utf-8'))
                    for href in set(parser.links):
                        target=urlsplit(href)
                        if not target.path.startswith('/app/') or target.netloc or not target.path:continue
                        linked=cache.get(href) or client.get(href,follow=True);cache[href]=linked
                        if linked.status_code not in (200,):fallos.append((role,name,'enlace',href,linked.status_code))
                        if linked.streaming:b''.join(linked.streaming_content)
                if response.streaming:b''.join(response.streaming_content)
            resultados[role]=rows
        self.guardar('rutas-perfiles.json',resultados)
        self.assertFalse(fallos,fallos)

    def test_paginacion_busqueda_orden_filtros_y_empresa(self):
        from accounts.models import Empresa
        self.inventario()
        for i in range(24):Cliente.objects.create(empresa=self.empresa,ruc=f'QA-UI-{i:03}',razon_social=f'Cliente {i:03}',nombre_comercial=f'Nombre {i:03}')
        otra=Empresa.objects.create(nombre='Ajena auditoría QA')
        Cliente.objects.create(empresa=otra,ruc='RUC-AJENO-UI',razon_social='AJENO',nombre_comercial='AJENO')
        for user in (self.admin,self.oficina):
            self.client.force_login(user)
            with CaptureQueriesContext(connection) as queries:
                response=self.client.get(reverse('masiscam:clientes'),{'q':'QA-UI','orden':'-c1','page':'2'})
            self.assertEqual(response.status_code,200)
            page=response.context['pagina'];self.assertEqual(page.paginator.per_page,10)
            self.assertEqual(len(page),10);self.assertEqual(page.number,2)
            self.assertNotContains(response,'RUC-AJENO-UI')
            self.assertLess(len(queries),35)
            filtered=self.client.get(reverse('masiscam:clientes'),{'filtro':'c1','valor':'QA-UI-023'})
            self.assertEqual(filtered.context['pagina'].paginator.count,1)
            invalid=self.client.get(reverse('masiscam:clientes'),{'orden':'password;DROP','filtro':'empresa__id','valor':str(otra.pk),'page':'invalida'})
            self.assertEqual(invalid.status_code,200);self.assertNotContains(invalid,'RUC-AJENO-UI')
        self.client.force_login(self.taller)
        self.assertEqual(self.client.get(reverse('masiscam:usuarios')).status_code,403)
        self.assertEqual(self.client.get(reverse('masiscam:cliente_crear')).status_code,403)
        self.assertEqual(self.client.get('/app/ruta-qa-no-existe/').status_code,404)
        self.assertEqual(self.client.get(reverse('masiscam:usuario_estado',args=[RolMasiscam.objects.get(perfil__user=self.otro).pk])).status_code,405)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador aislado explícito')
@override_settings(**oficina.FLAGS)
class AuditoriaInterfazBrowserTests(InventarioFixture,StaticLiveServerTestCase):
    def captureOnCommitCallbacks(self,execute=False):return nullcontext()

    def test_tablas_y_busqueda_movil(self):
        from playwright.sync_api import sync_playwright,expect
        perfiles=[]
        for role,user,view in [('ADMIN',self.admin,'usuarios'),('OFICINA',self.oficina,'oficina_inicio'),('TALLER',self.taller,'taller_inicio')]:
            client=Client();client.force_login(user)
            perfiles.append((role,view,client.cookies[settings.SESSION_COOKIE_NAME].value))
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for role,view,cookie in perfiles:
                    context=browser.new_context(viewport=dict(width=390,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith((self.live_server_url,'data:','blob:')) else route.abort())
                    page=context.new_page();page.goto(self.live_server_url+reverse('masiscam:'+view),wait_until='networkidle')
                    expect(page.locator('header.mc-header')).to_have_count(1)
                    for width in (1440,1200,1024,768,390,320):
                        page.set_viewport_size(dict(width=width,height=1000))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(role,width))
                        self.assertLessEqual(page.locator('header.mc-header').bounding_box()['height'],74,(role,width))
                        expect(page.locator('.mc-logout button')).to_be_visible()
                        logo=page.locator('.mc-header .app-brand').bounding_box()
                        for selector in ('.mc-session','.mc-logout'):
                            box=page.locator('.mc-header '+selector).bounding_box()
                            self.assertLess(abs((box['y']+box['height']/2)-(logo['y']+logo['height']/2)),10,(role,width,selector))
                        if width<1200:
                            page.locator('.app-menu-toggle').click()
                            expect(page.locator('#appMenu')).to_have_class(__import__('re').compile(r'\bshow\b'))
                            expect(page.locator('#appMenu')).to_be_visible()
                            page.locator('#appMenu a').last.scroll_into_view_if_needed()
                            expect(page.locator('#appMenu a').last).to_be_visible()
                            page.locator('.app-menu-toggle').click()
                            expect(page.locator('#appMenu')).not_to_be_visible()
                        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'compacta-{role}-{width}.png'),full_page=True)
                    if role=='ADMIN':
                        self.assertGreaterEqual(page.locator('#usuarios-tabla').evaluate('e=>e.getBoundingClientRect().width'),1050)
                        self.assertEqual(page.locator('#usuarios-tabla tbody td:nth-child(3)').first.evaluate('e=>getComputedStyle(e).whiteSpace'),'nowrap')
                    elif role=='OFICINA':self.assertLessEqual(page.locator('.mc-toolbar .mc-filtro-buscar button').evaluate('e=>e.getBoundingClientRect().height'),48)
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'control-{role}-390.png'),full_page=True)
                    context.close()
            finally:browser.close()

    def test_informacion_tecnica_unificada_conserva_guardado(self):
        import uuid
        from . import flujo_services as flujo
        from playwright.sync_api import sync_playwright,expect
        self.cargar();parte=self.componente();self.seleccionar(parte);self.diag.refresh_from_db()
        flujo.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4(),preliminar=True)
        self.client.force_login(self.taller)
        cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith((self.live_server_url,'data:','blob:')) else route.abort())
                page=context.new_page();page.goto(self.live_server_url+self.url,wait_until='networkidle')
                section=page.locator('#informacion-tecnica')
                expect(section).to_have_count(1)
                expect(section.locator('h2')).to_contain_text('Diagnóstico e información técnica')
                self.assertEqual(page.locator('footer.mf-links').count(),0)
                for ausente in ('Evidencias de inspección','Etapas de TALLER','Guardar detalles técnicos'):
                    self.assertEqual(page.get_by_text(ausente).count(),0)
                inspection=section.locator('#mf-diagnostico')  # un solo formulario: inspección y diagnóstico
                expect(inspection).to_have_count(1)
                self.assertTrue(section.evaluate('e=>{const ids=Array.from(e.querySelectorAll("[id]"),n=>n.id);return ids.length===new Set(ids).size}'))
                for width in (1440,768,390):
                    page.set_viewport_size(dict(width=width,height=1000))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'informacion-tecnica-{width}.png'),full_page=True)
                inspection.locator('[name=ins-hallazgos]').fill('Hallazgo conservado QA compacto')
                inspection.get_by_role('button',name='Guardar avance',exact=True).click()
                expect(section).to_be_visible()
                expect(inspection.locator('[name=ins-hallazgos]')).to_have_value('Hallazgo conservado QA compacto')
                diag=section.locator('#mf-diagnostico')
                diag.locator('[name=repuestos]').select_option('REQUERIDOS')
                diag.locator('[name=conclusiones]').fill('Conclusión conservada QA compacta')
                diag.get_by_role('button',name='Guardar avance',exact=True).click()
                expect(section).to_be_visible()
                expect(diag.locator('[name=conclusiones]')).to_have_value('Conclusión conservada QA compacta')
                context.close()
            finally:browser.close()
        self.diag.refresh_from_db()
        self.assertEqual(self.diag.conclusiones,'Conclusión conservada QA compacta')
        self.assertFalse(self.diag.enviado_en)
        self.assertEqual(self.diag.piezas.count(),1)

    def test_todas_las_vistas_html_tres_roles_y_tres_tamanos(self):
        from playwright.sync_api import sync_playwright,expect
        self.inventario();routes=list(self.rutas());profiles={};cookies={};failures=[]
        for i in range(24):Cliente.objects.create(empresa=self.empresa,ruc=f'QA-UI-{i:03}',razon_social=f'Cliente {i:03}',nombre_comercial=f'Nombre {i:03}')
        for role,user in self.roles.items():
            client=Client(raise_request_exception=False);client.force_login(user)
            cookies[role]=client.cookies[settings.SESSION_COOKIE_NAME].value
            urls=set()
            for name,url,_ in routes:
                response=client.get(url,follow=True)
                if response.status_code==200 and 'text/html' in response.get('Content-Type',''):urls.add(url)
                if response.streaming:b''.join(response.streaming_content)
            profiles[role]=sorted(urls)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for role,urls in profiles.items():
                    context=browser.new_context(viewport=dict(width=1440,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies[role],url=self.live_server_url)])
                    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith((self.live_server_url,'data:','blob:')) else route.abort())
                    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    for url in urls:
                        errors.clear();response=page.goto(self.live_server_url+url,wait_until='networkidle')
                        if not response or response.status>=400:failures.append((role,url,'HTTP',response.status if response else None));continue
                        if page.locator('header.mc-header').count()!=1:
                            failures.append((role,url,'Encabezado',page.url,page.title(),page.locator('body').inner_text()[:180]));continue
                        if page.locator('nav[aria-label="Migas de pan"]').count()!=1:failures.append((role,url,'Breadcrumbs'))
                        if page.locator('main h1').count()==0:failures.append((role,url,'Falta encabezado'))
                        for width in (1440,768,390):
                            page.set_viewport_size(dict(width=width,height=1000))
                            if not page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'):
                                overflow=page.evaluate('Array.from(document.querySelectorAll("main *")).filter(e=>e.getBoundingClientRect().right>innerWidth+1&&!e.closest(".table-responsive,.pl-viewport")&&getComputedStyle(e).display!=="none").map(e=>e.tagName+"."+e.className).slice(0,5)')
                                failures.append((role,url,width,'desbordamiento',overflow))
                            if width==390:
                                toggle=page.locator('.mc-header .app-menu-toggle');toggle.click()
                                expect(page.locator('#appMenu')).to_have_class(__import__('re').compile('show'))
                                toggle.click()
                                expect(page.locator('#appMenu')).not_to_be_visible()
                        if errors:failures.append((role,url,'JS',errors.copy()))
                        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR') and url in [reverse('masiscam:taller_inicio'),reverse('masiscam:oficina_inicio'),reverse('masiscam:usuarios')]:
                            page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'{role}-{url.split("/")[-2]}.png'),full_page=True)
                    if role in ('ADMIN','OFICINA'):
                        page.goto(self.live_server_url+reverse('masiscam:clientes')+'?q=QA-UI',wait_until='networkidle')
                        expect(page.locator('table tbody tr')).to_have_count(10)
                        page.locator('.mc-pagination a',has_text='Siguiente').click()
                        expect(page).to_have_url(__import__('re').compile('page=2'))
                        expect(page.locator('table tbody tr')).to_have_count(10)
                        page.locator('[name=orden]').select_option('-c1')
                        page.locator('form[data-listado-servidor] button').click()
                        expect(page.locator('table tbody tr').first).to_contain_text('QA-UI-023')
                        page.locator('form[data-listado-servidor] summary').click()
                        page.locator('[name=filtro]').select_option('c1')
                        page.locator('[name=valor]').fill('QA-UI-017')
                        page.locator('form[data-listado-servidor] button').click()
                        expect(page.locator('table tbody tr')).to_have_count(1)
                        expect(page.locator('table tbody tr')).to_contain_text('QA-UI-017')
                        page.get_by_role('link',name='Crear cliente',exact=True).click()
                        expect(page.locator('#masiscamFormModal')).to_be_visible()
                    context.close()
            finally:
                browser.close()
                self.guardar('navegador-cobertura.json',dict(perfiles=profiles,fallos=failures))
        self.guardar('navegador-cobertura.json',dict(perfiles=profiles,fallos=failures))
        self.assertFalse(failures,failures)

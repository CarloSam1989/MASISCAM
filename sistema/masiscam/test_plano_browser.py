import hashlib
import os
import re
import time
import uuid
from io import BytesIO
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse

from . import test_plano as qa, tecnica_services as tecnica, plano_services as svc
from .models import ParteReferenciaTecnica, ReferenciaTecnica


def en_hilo(funcion):
    """El ORM no puede usarse dentro del bucle async de Playwright; se consulta en un hilo propio."""
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connection as conexion
    def ejecutar():
        try: return funcion()
        finally: conexion.close()
    with ThreadPoolExecutor(1) as pool: return pool.submit(ejecutar).result()


def dibujo_sintetico():
    from reportlab.pdfgen.canvas import Canvas
    data = BytesIO()
    c = Canvas(data, pagesize=(612, 792))
    c.setStrokeColorRGB(.2,.34,.49); c.setLineWidth(2)
    c.rect(35, 35, 542, 722)
    c.setFont('Helvetica-Bold', 18); c.drawString(65, 710, 'MASISCAM - PLANO SINTETICO QA')
    c.setFont('Helvetica', 10); c.drawString(65, 688, 'Prueba del visor. Sin validez industrial ni equivalencias reales.')
    for x in (165, 306, 447):
        c.circle(x, 420, 45); c.circle(x, 420, 18); c.line(x-55,420,x+55,420)
    c.line(110,420,502,420)
    c.line(306,465,306,510); c.circle(306,530,14)
    c.drawCentredString(306,526,'A')
    c.setFont('Helvetica-Bold',11); c.drawString(65, 160, 'POSICION A - COMPONENTE FICTICIO DE PRUEBA')
    c.setFont('Helvetica',10); c.drawString(65, 140, 'Las posiciones reales requieren configuracion manual de PLANTA.')
    c.showPage(); c.save()
    return SimpleUploadedFile('plano-sintetico-qa.pdf', data.getvalue(), content_type='application/pdf')


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS') == '1' and connection.vendor == 'postgresql', 'Navegador QA opcional sobre PostgreSQL aislado')
@override_settings(**qa.FLAGS)
class PlanoBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        qa.fixture(self)
        self.sin_mapa_id = self.ref.pk
        # A separate synthetic drawing is used for positioning tests: no marker
        # locations are inferred from the supplied manufacturer's drawing.
        self.ref = tecnica.guardar_referencia(usuario=self.planta, empresa=self.empresa, clave=uuid.uuid4(),
            archivo=dibujo_sintetico(), datos=dict(codigo='PLANO-VISOR-QA', tipo='PLANO',
                titulo='Plano sintético de interacción', procedencia='Fixture QA sin uso industrial',
                autorizacion_uso='Prueba aislada', proyecto_id=self.exp.registro.equipo.proyecto_id))
        pk = tecnica.guardar_parte(usuario=self.planta, empresa=self.empresa, referencia_id=self.ref.pk,
            clave=uuid.uuid4(), datos=dict(posicion='A', numero_parte='QA-PARTE', descripcion='Componente sintético de prueba', unidad='unidad QA'))
        self.parte = ParteReferenciaTecnica.objects.get(pk=pk)
        tecnica.estado_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=self.ref.pk, estado='PUBLICADA', clave=uuid.uuid4())
        tecnica.aplicar_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=self.ref.pk,
            equipo_id=self.exp.registro.equipo_id, fundamento='Solo prueba sintética del visor; no certifica compatibilidad industrial', clave=uuid.uuid4())
        svc.guardar_mapa(usuario=self.planta, empresa=self.empresa, referencia_id=self.ref.pk, base_version=0,
            posiciones=[dict(parte=self.parte.pk, pagina=1, x='.5', y=str(1-530/792))], clave=uuid.uuid4())

    def test_visores_seleccion_configuracion_y_responsive(self):
        from playwright.sync_api import sync_playwright, expect
        cookies = {}
        for name, user in [('tecnico',self.tecnico),('planta',self.planta)]:
            client = Client(); client.force_login(user)
            cookies[name] = client.cookies[settings.SESSION_COOKIE_NAME].value
        photo = qa.foto_pieza().read()
        actual = None
        source = os.environ.get('MASISCAM_QA_REFERENCE_PDF')
        if source:
            source = Path(source)
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            seed = (Path(__file__).parent/'data/m20a_hnrc_fig1_revision.csv').read_text(encoding='utf-8')
            actual = svc.importar_m20(usuario=self.planta, empresa=self.empresa,
                proyecto_id=self.exp.registro.equipo.proyecto_id,
                archivo=SimpleUploadedFile(source.name, source.read_bytes(), content_type='application/pdf'),
                materiales=seed, procedencia='Archivo suministrado para prueba QA de importación; revisión humana simulada',
                autorizacion='QA aislado sin publicación ni aplicabilidad', confirmado=True, clave=uuid.uuid4())
            self.assertEqual(actual.estado,'BORRADOR')
            self.assertFalse(actual.aplicabilidades.exists())
            self.assertFalse(actual.mapas.exists())
        output = Path(os.environ.get('MASISCAM_UI_EVIDENCE_DIR', settings.BASE_DIR/'.venv-rbac/plano-evidencias'))
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for width in (1440,768,390):
                    context = browser.new_context(viewport={'width':width,'height':1000})
                    context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':cookies['tecnico'],'url':self.live_server_url}])
                    page = context.new_page()
                    # Al primer guardado desde el PDF se crea el borrador de trabajo (r2); los demás anchos lo reutilizan.
                    vigente = en_hilo(lambda: ReferenciaTecnica.objects.filter(empresa=self.empresa, codigo=self.ref.codigo).order_by('-revision').first())
                    url = self.live_server_url+reverse('masiscam:plano',args=[vigente.pk])+'?diagnostico='+str(self.diag.pk)
                    page.goto(url)
                    expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                    self.assertTrue(page.locator('#pl-canvas').evaluate('(c)=>c.width>0 && c.height>0'))
                    page.locator('#pl-plus').click()
                    expect(page.locator('#pl-zoom')).to_have_text('125%')
                    bounds = page.locator('#pl-viewport').bounding_box()
                    page.mouse.move(bounds['x']+120,bounds['y']+100)
                    page.mouse.down(); page.mouse.move(bounds['x']+50,bounds['y']+100,steps=5); page.mouse.up()
                    self.assertGreater(page.locator('#pl-viewport').evaluate('v=>v.scrollLeft'),0)
                    point = page.locator('.pl-point').first
                    self.assertAlmostEqual(point.evaluate('(p)=>(p.getBoundingClientRect().x+p.getBoundingClientRect().width/2-p.parentElement.getBoundingClientRect().x)/p.parentElement.getBoundingClientRect().width'),.5,places=2)
                    point.click()
                    expect(page.locator('#pl-detail')).to_contain_text('QA-PARTE')
                    if width==1440:  # sin pieza previa, el PDF abre el formulario único
                        expect(page.locator('#pl-position-dialog')).to_be_visible()
                        page.locator('#pl-position-cancel').click()
                    expect(page.locator('#pl-position-dialog')).not_to_be_visible()
                    if width<992:
                        page.get_by_role('tab',name='Componentes',exact=True).click()
                    page.locator('#pl-search').fill('QA-PARTE')
                    expect(page.locator('.pl-part')).to_have_count(1)
                    page.locator('#pl-search').fill('NO EXISTE QA')
                    expect(page.locator('.pl-part')).to_have_count(0)
                    page.locator('#pl-search').fill('')
                    if width==1440:
                        page.get_by_role('button',name='Agregar al diagnóstico',exact=True).click()
                    else:  # la pieza ya existe en el borrador de trabajo: se edita, no se duplica
                        if width<992:
                            page.get_by_role('tab',name=re.compile('Seleccionados')).click()
                        page.locator('#pl-selected-list').get_by_role('button',name='Editar',exact=True).last.click()
                    expect(page.locator('#pl-position-action')).to_have_value('EVALUAR')
                    page.locator('#pl-position-quantity').fill('2.5')
                    page.locator('#pl-position-condition').fill('Condición sintética QA')
                    page.locator('#pl-position-action').select_option('EVALUAR')
                    page.locator('#pl-position-form summary').click()
                    page.locator('#pl-position-notes').fill('Observaciones de prueba')
                    if width==1440:
                        page.locator('#pl-position-photos').set_input_files({'name':'pieza.png','mimeType':'image/png','buffer':photo})
                    page.locator('#pl-position-save').click()
                    expect(page.locator('#pl-position-dialog')).not_to_be_visible(timeout=15000)
                    if width<992:
                        page.get_by_role('tab',name=re.compile('Seleccionados')).click()
                    expect(page.locator('#pl-selected-list')).to_contain_text('2.5 unidad QA')
                    self.assertEqual(en_hilo(lambda: self.diag.piezas.count()),1)
                    page.locator('#pl-selected-list').get_by_role('button',name='Editar',exact=True).last.click()
                    page.locator('#pl-position-quantity').fill('3')
                    page.locator('#pl-position-save').click()
                    expect(page.locator('#pl-position-dialog')).not_to_be_visible()
                    if width<992:
                        page.get_by_role('tab',name='Plano',exact=True).click()
                    page.locator('#pl-fit').click()
                    expect(page.locator('#pl-zoom')).to_have_text('100%')
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    page.screenshot(path=str(output/f'plano-tecnico-{width}.png'),full_page=True)
                    if width<992:
                        page.get_by_role('tab',name=re.compile('Seleccionados')).click()
                        page.screenshot(path=str(output/f'plano-seleccionados-{width}.png'),full_page=True)
                    context.close()
                context = browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':cookies['tecnico'],'url':self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url+reverse('masiscam:plano',args=[self.sin_mapa_id])+'?diagnostico='+str(self.diag.pk))
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                expect(page.locator('.pl-point')).to_have_count(0)
                page.locator('.pl-part').click()
                page.get_by_role('button',name='Agregar al diagnóstico',exact=True).click()
                expect(page.locator('[name=posicion_plano_id]')).to_have_value('')
                page.locator('#pl-position-quantity').fill('1')
                page.locator('#pl-position-condition').fill('Selección por listado QA')
                page.locator('#pl-position-action').select_option('EVALUAR')
                page.locator('#pl-position-save').click()
                expect(page.locator('#pl-position-dialog')).not_to_be_visible()
                page.screenshot(path=str(output/'plano-listado-sin-posiciones-1440.png'),full_page=True)
                context.close()
                context = browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':cookies['planta'],'url':self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url+reverse('masiscam:plano',args=[self.ref.pk]))
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                page.get_by_role('button',name='Configurar posiciones',exact=True).click()
                page.locator('.pl-part').click()
                page.locator('#pl-canvas').click(position={'x':200,'y':250})
                page.locator('#pl-save-map').click()
                expect(page.locator('#pl-map-label')).to_contain_text('v2')
                if actual:
                    page.goto(self.live_server_url+reverse('masiscam:plano',args=[actual.pk]))
                    expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                    expect(page.locator('.pl-point')).to_have_count(0)
                    expect(page.locator('#pl-config-toggle')).to_have_count(0)
                    page.screenshot(path=str(output/'m20-original-borrador-sin-posiciones.png'),full_page=True)
                    page.goto(self.live_server_url+reverse('masiscam:plano_importar'))
                    page.locator('[name=archivo]').set_input_files(str(source))
                    page.get_by_role('button',name='Revisar filas sin guardar',exact=True).click()
                    expect(page.locator('#pl-import-status')).to_contain_text('86 filas listas')
                    self.assertEqual(page.locator('[name=archivo]').evaluate('input=>input.files.length'),1)
                    expect(page.locator('[name=confirmado]')).not_to_be_checked()
                context.close()
            finally:
                browser.close()
        if source:
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original_hash)

    def test_pdf_y_lista_abren_solo_pl_position_dialog_sin_duplicar(self):
        from playwright.sync_api import sync_playwright, expect
        from .tecnica_models import PiezaRequerida
        client = Client(); client.force_login(self.tecnico)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        def guardar(page, cantidad):
            expect(page.locator('dialog[open]')).to_have_count(1)
            expect(page.locator('#pl-position-dialog[open]')).to_have_count(1)
            expect(page.locator('#pl-piece-dialog')).to_have_count(0)
            page.locator('#pl-position-quantity').fill(cantidad)
            page.locator('#pl-position-condition').fill('Condición QA')
            page.locator('#pl-position-action').select_option('EVALUAR')
            page.locator('#pl-position-save').click()
            expect(page.locator('dialog[open]')).to_have_count(0, timeout=15000)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                context = browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':cookie,'url':self.live_server_url}])
                page = context.new_page()
                pdf_requests = []
                page.on('request', lambda r: pdf_requests.append(r.url) if 'documento' in r.url and r.resource_type in ('fetch','xhr','other') else None)
                inicio = time.perf_counter()
                page.goto(self.live_server_url+reverse('masiscam:plano',args=[self.ref.pk])+'?diagnostico='+str(self.diag.pk))
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                print('MEDICION visor_listo_1440_ms', round((time.perf_counter()-inicio)*1000))
                page.locator('.pl-point').first.click()          # desde el PDF
                guardar(page, '2')
                self.assertEqual(en_hilo(lambda: PiezaRequerida.objects.filter(diagnostico=self.diag).count()), 1)
                page.locator('.pl-part').click()                 # desde la lista: resalta el marcador existente
                expect(page.locator('.pl-point')).to_have_count(1)
                expect(page.locator('.pl-point.is-active')).to_have_count(1)
                page.get_by_role('button',name='Agregar al diagnóstico',exact=True).click()
                expect(page.locator('#pl-position-quantity')).to_have_value('2')   # misma pieza, en edición
                guardar(page, '3')
                self.assertEqual(en_hilo(lambda: PiezaRequerida.objects.filter(diagnostico=self.diag).count()), 1)
                self.assertEqual(en_hilo(lambda: PiezaRequerida.objects.get(diagnostico=self.diag).cantidad), 3)
                expect(page.locator('.pl-point')).to_have_count(1)
                # El visor sigue abierto: el PDF se pidió una sola vez pese a guardar dos veces (PDF y lista).
                self.assertEqual(len(pdf_requests), 1, pdf_requests)
                self.assertTrue(page.evaluate("() => !document.querySelector('[data-ready=error]')"))
                page.set_viewport_size({'width':390,'height':900})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                page.get_by_role('tab',name=re.compile('Seleccionados')).click()
                expect(page.locator('#pl-selected-list')).to_contain_text('3')
                page.get_by_role('tab',name='Plano',exact=True).click()
                expect(page.locator('.pl-point.is-selected')).to_have_count(1)   # el marcador indica la pieza elegida
                self.assertEqual(len(pdf_requests), 1, pdf_requests)
                context.close()
            finally:
                browser.close()

    def test_regresion_visor_carga_sin_errores_js_y_con_huella_de_cache(self):
        """El visor debe iniciar sin excepciones de JavaScript para cada rol y el script debe versionarse por contenido."""
        from playwright.sync_api import sync_playwright, expect
        cookies = {}
        for nombre, usuario in (('TALLER', self.tecnico), ('ADMIN', self.planta), ('OFICINA', self.oficina)):
            cliente = Client(); cliente.force_login(usuario)
            cookies[nombre] = cliente.cookies[settings.SESSION_COOKIE_NAME].value
        js = Path(__file__).parent/'static/masiscam/js/plano.js'
        huella = hashlib.sha256(js.read_bytes()).hexdigest()[:12]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for ancho in (1440, 390):
                    for nombre, galleta in cookies.items():
                        context = browser.new_context(viewport={'width':ancho,'height':900})
                        context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':galleta,'url':self.live_server_url}])
                        page = context.new_page()
                        errores = []
                        page.on('pageerror', lambda error: errores.append(str(error)))
                        consulta = '?diagnostico='+str(self.diag.pk) if nombre == 'TALLER' else ''
                        page.goto(self.live_server_url+reverse('masiscam:plano',args=[self.ref.pk])+consulta)
                        expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                        self.assertEqual(errores, [], f'{nombre} {ancho}: {errores}')
                        self.assertTrue(page.locator('#pl-canvas').evaluate('(c)=>c.width>0 && c.height>0'))
                        expect(page.locator('.pl-part')).to_have_count(1)
                        expect(page.locator('.pl-point')).to_have_count(1)
                        src = page.locator('script[type=module][src*="plano.js"]').get_attribute('src')
                        self.assertIn('?v='+huella, src)
                        context.close()
            finally:
                browser.close()

    def test_piezas_nuevas_consecutivas_sin_marcador_crean_una_sola_revision_y_la_reutilizan(self):
        from playwright.sync_api import sync_playwright, expect
        from .tecnica_models import PiezaRequerida
        cliente = Client(); cliente.force_login(self.tecnico)
        galleta = cliente.cookies[settings.SESSION_COOKIE_NAME].value
        codigo = ReferenciaTecnica.objects.get(pk=self.sin_mapa_id).codigo
        base = en_hilo(lambda: (ReferenciaTecnica.objects.filter(codigo=codigo).count(),))
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for ancho in (1440, 390):
                    context = browser.new_context(viewport={'width':ancho,'height':1000})
                    context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':galleta,'url':self.live_server_url}])
                    page = context.new_page()
                    errores = []; pdfs = []
                    page.on('pageerror', lambda e: errores.append(str(e)))
                    page.on('request', lambda r: pdfs.append(r.url) if 'documento' in r.url and r.resource_type in ('fetch','xhr','other') else None)
                    page.goto(self.live_server_url+reverse('masiscam:plano',args=[self.sin_mapa_id])+'?diagnostico='+str(self.diag.pk))
                    expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                    for numero, (x, y) in enumerate(((200, 250), (260, 320), (330, 380)), 1):
                        if page.locator('.pl-selected-card').count() >= 3 and ancho == 390:
                            break
                        page.locator('#pl-canvas').click(position={'x':x+numero*(1 if ancho == 1440 else 0),'y':y})
                        expect(page.locator('#pl-position-dialog')).to_be_visible()
                        page.locator('#pl-position-number').fill(f'{ancho}-{numero}')
                        page.locator('#pl-position-description').fill(f'Pieza nueva {ancho}-{numero}')
                        page.locator('#pl-position-quantity').fill('1')
                        page.locator('#pl-position-condition').fill('Daño QA')
                        page.locator('#pl-position-save').click()
                        expect(page.locator('#pl-position-dialog')).not_to_be_visible(timeout=15000)
                        self.assertEqual(errores, [])
                        self.assertNotIn('versión posterior', page.locator('#pl-message').inner_text())
                    context.close()
            finally:
                browser.close()
        refs = en_hilo(lambda: list(ReferenciaTecnica.objects.filter(codigo=codigo).values_list('revision','estado')))
        self.assertEqual(len(refs), base[0] + 1, refs)  # una sola revisión de trabajo

    def _galleta(self, usuario):
        cliente = Client(); cliente.force_login(usuario)
        return cliente.cookies[settings.SESSION_COOKIE_NAME].value

    def _abrir(self, pw, galleta, ancho, alto, ref_pk):
        from playwright.sync_api import expect
        browser = pw.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width':ancho,'height':alto})
        context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':galleta,'url':self.live_server_url}])
        page = context.new_page()
        page.goto(self.live_server_url+reverse('masiscam:plano',args=[ref_pk])+'?diagnostico='+str(self.diag.pk))
        expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
        return browser, page

    def test_numero_34_no_se_trunca_a_3_y_continuar_al_diagnostico(self):
        from playwright.sync_api import sync_playwright, expect
        from .tecnica_models import PiezaRequerida
        galleta = self._galleta(self.tecnico)
        with sync_playwright() as pw:
            browser, page = self._abrir(pw, galleta, 1440, 1000, self.sin_mapa_id)
            try:
                def pieza(x, y, numero, pausa_ms):
                    page.locator('#pl-canvas').click(position={'x':x,'y':y})
                    expect(page.locator('#pl-position-dialog')).to_be_visible()
                    campo = page.locator('#pl-position-number')
                    for digito in numero:  # pausa mayor que el debounce: la consulta del primer dígito no debe pisar el siguiente
                        campo.press_sequentially(digito); page.wait_for_timeout(pausa_ms)
                    expect(campo).to_have_value(numero)
                    self.assertFalse(campo.evaluate('c=>c.readOnly'))
                    page.locator('#pl-position-description').fill(f'Pieza {numero}')
                    page.locator('#pl-position-quantity').fill('1')
                    page.locator('#pl-position-condition').fill('Daño QA')
                    page.locator('#pl-position-save').click()
                    expect(page.locator('#pl-position-dialog')).not_to_be_visible(timeout=15000)
                pieza(200, 250, '3', 900)
                pieza(300, 350, '34', 1000)      # «3» ya existe en el plano: no se adopta mientras se escribe «34»
                pieza(380, 420, '345', 100)
                expect(page.locator('#pl-count')).to_have_text('3')
                self.assertEqual(len(page.locator('.pl-selected-card').all()), 3)
                page.locator('#pl-canvas').click(position={'x':120,'y':140})
                page.locator('#pl-position-number').press_sequentially('3')
                page.locator('#pl-position-number').press('Enter')   # Enter confirma «3»: reutiliza el componente, no guarda
                expect(page.locator('#pl-position-dialog')).to_be_visible()
                expect(page.locator('#pl-position-number')).to_have_value('3')
                page.locator('#pl-position-cancel').click()
                page.locator('.swal2-confirm').click()   # descartar el borrador del formulario
                expect(page.locator('#pl-position-dialog')).not_to_be_visible()
                # Continuar al diagnóstico: mismo expediente, piezas conservadas, sin botón de envío en la etapa de selección.
                expect(page.get_by_role('button',name='Enviar a OFICINA')).to_have_count(0)
                page.get_by_role('link',name='Continuar al diagnóstico').click()
                expect(page.locator('#mf-diagnostico')).to_be_visible()
                expect(page.locator('#mf-piezas')).to_contain_text('Pieza 34')
                page.locator('#mf-diagnostico').get_by_role('button',name='Guardar avance',exact=True).click()
                expect(page.locator('#mf-piezas')).to_contain_text('Pieza 345')
                expect(page.locator('#mf-diagnostico').get_by_role('button',name='Validar diagnóstico con ADMINISTRADOR')).to_be_visible()
            finally:
                browser.close()
        piezas = en_hilo(lambda: sorted(PiezaRequerida.objects.filter(diagnostico=self.diag).values_list('parte__posicion', flat=True)))
        self.assertEqual(piezas, ['3', '34', '345'])
        codigo = ReferenciaTecnica.objects.get(pk=self.sin_mapa_id).codigo
        self.assertEqual(en_hilo(lambda: ReferenciaTecnica.objects.filter(codigo=codigo).count()), 2)   # catálogo + un borrador

    def test_lista_resalta_y_centra_marcador_y_scroll_del_formulario(self):
        from playwright.sync_api import sync_playwright, expect
        galleta = self._galleta(self.tecnico)
        with sync_playwright() as pw:
            for ancho, alto in ((1440, 900), (390, 640)):
                browser, page = self._abrir(pw, galleta, ancho, alto, self.ref.pk)
                try:
                    if ancho < 992:
                        page.get_by_role('tab',name='Plano',exact=True).click()
                    for _ in range(3):
                        page.locator('#pl-plus').click()
                    page.locator('#pl-viewport').evaluate('v=>{v.scrollLeft=0;v.scrollTop=0}')
                    if ancho < 992:
                        page.get_by_role('tab',name='Componentes',exact=True).click()
                    page.locator('.pl-part').click()
                    if ancho < 992:
                        page.get_by_role('tab',name='Plano',exact=True).click()
                    expect(page.locator('.pl-point.is-active')).to_have_count(1)
                    page.wait_for_timeout(300)
                    dentro = page.evaluate("""()=>{const m=document.querySelector('.pl-point.is-active').getBoundingClientRect(),v=document.querySelector('#pl-viewport').getBoundingClientRect();
                        return m.left>=v.left-1&&m.right<=v.right+1&&m.top>=v.top-1&&m.bottom<=v.bottom+1}""")
                    self.assertTrue(dentro, f'marcador fuera del visor a {ancho}px')
                    # Formulario: se desplaza por dentro, las acciones siguen visibles y la página se restaura al cerrar.
                    page.locator('.pl-point.is-active').click()
                    expect(page.locator('#pl-position-dialog')).to_be_visible()
                    page.locator('#pl-position-form details').first.evaluate('d=>d.open=true')
                    overflow = page.locator('#pl-position-dialog').evaluate('d=>getComputedStyle(d).overflowY')
                    self.assertIn(overflow, ('auto','scroll'))
                    page.locator('#pl-position-dialog').hover(); page.mouse.wheel(0, 600)
                    page.wait_for_timeout(200)
                    guardar = page.locator('#pl-position-save').bounding_box()
                    self.assertTrue(0 <= guardar['y'] and guardar['y']+guardar['height'] <= alto+1, f'acciones fuera de pantalla a {ancho}px: {guardar}')
                    page.locator('#pl-position-cancel').click()
                    expect(page.locator('#pl-position-dialog')).not_to_be_visible()
                    self.assertNotEqual(page.evaluate('getComputedStyle(document.body).overflow'), 'hidden')
                    page.evaluate('window.scrollTo(0,0)'); page.mouse.move(5, alto-5); page.mouse.wheel(0, 300); page.wait_for_timeout(200)
                    self.assertGreater(page.evaluate('window.scrollY'), 0)
                finally:
                    browser.close()

    def test_taller_selecciona_marca_modelo_ratio_rotacion_operacion_y_guarda_reductor(self):
        from playwright.sync_api import sync_playwright, expect
        from .models import Equipo
        galleta = self._galleta(self.tecnico)
        cliente = self.exp.cliente_contractual
        total = Equipo.objects.count()
        with sync_playwright() as pw:
            for ancho, alto in ((1440, 1000), (390, 900)):
                browser = pw.chromium.launch(channel='msedge', headless=True)
                try:
                    context = browser.new_context(viewport={'width':ancho,'height':alto})
                    context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':galleta,'url':self.live_server_url}])
                    page = context.new_page()
                    page.goto(self.live_server_url+reverse('masiscam:ficha_crear')+f'?tipo=REDUCTOR&cliente={cliente.pk}')
                    marca = page.locator('#sel-marca')
                    expect(marca).to_be_visible()
                    self.assertFalse(marca.is_disabled(), f'selector de marca deshabilitado a {ancho}px')
                    opciones = marca.locator('option').all_inner_texts()
                    self.assertIn('DERAN', opciones, f'marca ausente a {ancho}px: {opciones}')
                    # Cadena: marca → modelo → ratio → rotación → operación (cada nivel se habilita con el anterior).
                    expect(page.locator('#sel-modelo')).to_be_disabled()
                    marca.select_option('DERAN')
                    for nivel, valor in (('modelo','M20A'),('ratio','3:1'),('rotacion','#1'),('operacion','VERTICAL')):
                        sel = page.locator('#sel-'+nivel)
                        expect(sel).to_be_enabled()
                        self.assertIn(valor, sel.locator('option').all_inner_texts())
                        sel.select_option(valor)
                    for nombre, valor in (('numero_serie',f'SER-MARCA-{ancho}'),('potencia','20 HP')):
                        page.locator('#id_'+nombre).fill(valor)
                    page.locator('#id_camaronera').fill('Camaronera QA'); page.locator('#id_estacion').fill(f'Estación {ancho}')
                    page.locator('#id_sector').fill('Sector QA'); page.locator('#id_numero_equipo').fill(f'EQ-{ancho}')
                    expect(page.locator('#cat-estado')).to_contain_text('Catálogo activo encontrado')
                    page.get_by_role('button', name=re.compile(r'^Guardar reductor')).click()
                    expect(page).not_to_have_url(re.compile(r'/equipos/nuevo/'), timeout=30000)
                    context.close()
                finally:
                    browser.close()
        self.assertEqual(Equipo.objects.count(), total+2)
        for ancho in (1440, 390):
            equipo = Equipo.objects.get(numero_serie=f'SER-MARCA-{ancho}')
            self.assertEqual((equipo.marca, equipo.modelo, equipo.ratio, equipo.rotacion, equipo.operacion), ('DERAN','M20A','3:1','#1','VERTICAL'))

    def test_catalogo_vacio_avisa_y_no_afecta_otras_categorias(self):
        from .models import ReferenciaTecnica
        ReferenciaTecnica.objects.filter(empresa=self.empresa).update(estado='RETIRADA')
        client = Client(); client.force_login(self.tecnico)
        vacio = client.get(reverse('masiscam:ficha_crear')+'?tipo=REDUCTOR')
        self.assertContains(vacio, 'data-catalogo-vacio')
        self.assertNotContains(vacio, 'data-catalogo="')
        bomba = client.get(reverse('masiscam:ficha_crear')+'?tipo=BOMBA')
        self.assertNotContains(bomba, 'data-catalogo-vacio')

    def test_seleccion_lateral_resalta_y_centra_marcador_sin_inventar_coordenadas(self):
        from playwright.sync_api import sync_playwright, expect
        ref = tecnica.guardar_referencia(usuario=self.planta, empresa=self.empresa, clave=uuid.uuid4(),
            archivo=dibujo_sintetico(), datos=dict(codigo='PLANO-S250-QA', tipo='PLANO', titulo='Plano S250 sintético',
                procedencia='Fixture QA sin uso industrial', autorizacion_uso='Prueba aislada', proyecto_id=self.exp.registro.equipo.proyecto_id))
        ids = {}
        for clave, posicion, codigo in (('con', '1', 'S250'), ('sin', '2', 'S251')):
            ids[clave] = tecnica.guardar_parte(usuario=self.planta, empresa=self.empresa, referencia_id=ref.pk, clave=uuid.uuid4(),
                datos=dict(posicion=posicion, numero_parte=codigo, descripcion='Componente '+codigo, unidad='unidad QA'))
        tecnica.estado_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=ref.pk, estado='PUBLICADA', clave=uuid.uuid4())
        tecnica.aplicar_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=ref.pk,
            equipo_id=self.exp.registro.equipo_id, fundamento='Solo prueba sintética del visor; no certifica compatibilidad industrial', clave=uuid.uuid4())
        svc.guardar_mapa(usuario=self.planta, empresa=self.empresa, referencia_id=ref.pk, base_version=0,
            posiciones=[dict(parte=ids['con'], pagina=1, x='.5', y=str(1-530/792))], clave=uuid.uuid4())
        client = Client(); client.force_login(self.tecnico)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for ancho, alto in ((1440, 1000), (390, 900)):
                    context = browser.new_context(viewport={'width': ancho, 'height': alto})
                    context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': cookie, 'url': self.live_server_url}])
                    page = context.new_page()
                    page.goto(self.live_server_url+reverse('masiscam:plano', args=[ref.pk])+'?diagnostico='+str(self.diag.pk))
                    expect(page.locator('#pl-app')).to_have_attribute('data-ready', 'true', timeout=60000)
                    if ancho < 992: page.get_by_role('tab', name=re.compile('Componentes')).click()
                    page.locator('.pl-part', has_text='S250').click()
                    if ancho < 992: page.get_by_role('tab', name='Plano', exact=True).click()
                    marcador = page.locator('.pl-point.is-active')
                    expect(marcador).to_have_count(1); expect(marcador).to_have_text('1')
                    centrado = """() => { const m=document.querySelector('.pl-point.is-active').getBoundingClientRect(),
                        v=document.querySelector('.pl-viewport').getBoundingClientRect();
                        return m.left>=v.left-1 && m.right<=v.right+1 && m.top>=v.top-1 && m.bottom<=v.bottom+1; }"""
                    expect(page.locator('.pl-viewport')).to_be_visible()
                    self.assertTrue(page.evaluate(centrado), f'marcador 1 fuera del visor a {ancho}px')
                    if ancho < 992: page.get_by_role('tab', name=re.compile('Componentes')).click(); page.get_by_role('tab', name='Plano', exact=True).click()
                    expect(page.locator('.pl-point.is-active')).to_have_count(1)           # se mantiene mientras siga seleccionado
                    if ancho < 992: page.get_by_role('tab', name=re.compile('Componentes')).click()
                    page.get_by_role('button', name='Agregar al diagnóstico', exact=True).click()   # registrar desde la selección lateral
                    page.locator('#pl-position-quantity').fill('1'); page.locator('#pl-position-condition').fill('Condición QA')
                    page.locator('#pl-position-action').select_option('EVALUAR'); page.locator('#pl-position-save').click()
                    expect(page.locator('dialog[open]')).to_have_count(0, timeout=15000)
                    if ancho < 992: page.get_by_role('tab', name='Plano', exact=True).click()
                    expect(page.locator('.pl-point.is-active')).to_have_count(1)           # sigue resaltado tras registrar
                    expect(page.locator('.pl-point.is-active')).to_have_text('1')
                    self.assertTrue(page.evaluate(centrado), f'marcador 1 descentrado tras registrar a {ancho}px')
                    if ancho < 992: page.get_by_role('tab', name=re.compile('Componentes')).click()
                    page.locator('.pl-part', has_text='S251').click()                       # sin coordenadas: no se inventa marcador
                    expect(page.locator('.pl-point.is-active')).to_have_count(0)
                    expect(page.locator('.pl-point')).to_have_count(1)
                    context.close()
            finally:
                browser.close()

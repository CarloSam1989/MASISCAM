"""Planos de QA o PDF real explícito, PostgreSQL aislado y sin servicios externos."""
import os
import uuid
from unittest import skipUnless

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse

from . import test_taller, test_tecnica as qa, tecnica_services as tecnica, plano_services as planos
from .models import Documento, ReferenciaTecnica, SolicitudRepuestos, AplicabilidadReferenciaEquipo
from .test_plano import foto_pieza


FLAGS = dict(GOOGLE_DRIVE_ENABLED=False, MASISCAM_RECEPCION_LOCAL=True,
    MASISCAM_TECNICA_LOCAL=True, MASISCAM_OPERACION_LOCAL=True)
PDF_REAL = os.environ.get('MASISCAM_QA_REFERENCE_PDF')
DESCRIPCION_COMPONENTE = 'HEAT EXCHANGER' if PDF_REAL else 'Componente ficticio QA'
CODIGO_COMPONENTE = 'M2021E' if PDF_REAL else 'QA-7'


def pdf_trabajo():
    if PDF_REAL:
        from pathlib import Path
        source = Path(PDF_REAL)
        return SimpleUploadedFile(source.name, source.read_bytes(), content_type='application/pdf')
    return qa.pdf_plano()


class TrabajoFixture:
    setUp = test_taller.TallerTests.setUp
    datos = test_taller.TallerTests.datos
    crear = test_taller.TallerTests.crear
    recepcion = test_taller.TallerTests.recepcion

    def cargar(self):
        self.equipo = self.crear('PLANO-TRABAJO')
        self.exp = self.recepcion(self.equipo)
        self.url = reverse('masiscam:flujo_detalle', args=[self.exp.pk])
        archivo = pdf_trabajo()
        self.pdf = archivo.read()
        archivo.seek(0)
        response = self.client.post(self.url, dict(op='plano_trabajo', clave=uuid.uuid4(),
            archivo=archivo))
        self.assertEqual(response.status_code, 302, response.content)
        self.ref = self.exp.planos_trabajo.get()
        self.diag = self.exp.etapas_tecnicas.get(codigo='DIAGNOSTICO').diagnostico
        self.plano_url = reverse('masiscam:plano', args=[self.ref.pk]) + f'?diagnostico={self.diag.pk}&flujo=1'
        self.assertEqual(response.url, self.plano_url)
        return response

    def componente(self):
        url = reverse('masiscam:plano_componente', args=[self.ref.pk]) + f'?diagnostico={self.diag.pk}&flujo=1'
        response = self.client.post(url, dict(clave=uuid.uuid4(), posicion='7',
            descripcion=DESCRIPCION_COMPONENTE, numero_parte=CODIGO_COMPONENTE, unidad='unidad', pagina='1'))
        self.assertEqual(response.status_code, 302, response.content)
        return self.ref.partes.get()

    def seleccionar(self, parte):
        self.diag.refresh_from_db()
        response = self.client.post(reverse('masiscam:plano_pieza', args=[self.diag.pk]),
            dict(clave=uuid.uuid4(), version=self.diag.version_edicion, parte_id=parte.pk,
                cantidad='2', unidad='unidad', condicion='Daño observado QA', accion='EVALUAR', fotografias=foto_pieza()))
        self.assertEqual(response.status_code, 200, response.content)
        return self.diag.piezas.get()


@override_settings(**FLAGS)
class PlanoTrabajoTests(TrabajoFixture, TestCase):
    def test_marcado_parcial_versionado_publicacion_y_permisos(self):
        import json
        from .models import MapaPlano, PosicionPlano
        self.cargar()
        parte = self.componente()
        otra = planos.registrar_componente_trabajo(usuario=self.taller, empresa=self.empresa,
            referencia_id=self.ref.pk, diagnostico_id=self.diag.pk, clave=uuid.uuid4(),
            datos=dict(posicion='8', descripcion='RADIAL BEARING CARRIER' if PDF_REAL else 'Otra pieza QA',
                numero_parte='M2002B' if PDF_REAL else 'QA-8', unidad='unidad', pagina='1'))
        url = reverse('masiscam:plano_mapa', args=[self.ref.pk])
        posiciones = [dict(parte=parte.pk, pagina=1, x='.7886', y='.3388')]
        response = self.client.post(url, json.dumps(dict(clave=str(uuid.uuid4()), version=0,
            posiciones=posiciones)), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['provisional'])
        mapa = self.ref.mapas.get()
        self.assertEqual(mapa.posiciones.count(), 1)
        self.assertEqual(self.ref.partes.count(), 2)
        punto = mapa.posiciones.get()
        pieza_id = planos.guardar_seleccion(usuario=self.taller, empresa=self.empresa,
            diagnostico_id=self.diag.pk, version=self.diag.version_edicion, clave=uuid.uuid4(),
            datos=dict(origen='BIBLIOTECA', parte_id=parte.pk, posicion_plano_id=punto.pk,
                cantidad='1', unidad='unidad', condicion='Daño QA', accion='EVALUAR'))
        pieza = self.diag.piezas.get(pk=pieza_id)
        self.assertTrue(pieza.snapshot['plano']['provisional'])
        self.diag.refresh_from_db()
        planos.guardar_seleccion(usuario=self.taller, empresa=self.empresa,
            diagnostico_id=self.diag.pk, version=self.diag.version_edicion, clave=uuid.uuid4(),
            datos=dict(origen='BIBLIOTECA', parte_id=otra.pk, cantidad='1', unidad='unidad',
                condicion='Revisión QA', accion='EVALUAR'))
        self.assertIsNone(self.diag.piezas.get(parte=otra).posicion_plano_id)
        for change in ({'pagina':2}, {'x':'NaN'}, {'parte':999999}):
            invalidas = [dict(posiciones[0], **change)]
            self.assertEqual(self.client.post(url, json.dumps(dict(clave=str(uuid.uuid4()), version=1,
                posiciones=invalidas)), content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(url, json.dumps(dict(clave=str(uuid.uuid4()), version=0,
            posiciones=posiciones)), content_type='application/json').status_code, 400)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MapaPlano.objects.filter(pk=mapa.pk).update(provisional=False)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            PosicionPlano.objects.filter(pk=punto.pk).update(x='.1')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MapaPlano.objects.create(referencia=self.ref, version=2, autor=self.taller,
                hash_documento=self.ref.documento.hash_sha256, clave=uuid.uuid4(), provisional=False)
        nuevo = planos.guardar_mapa(usuario=self.taller, empresa=self.empresa, referencia_id=self.ref.pk,
            base_version=1, posiciones=[dict(posiciones[0], x='.7')], clave=uuid.uuid4())
        self.assertTrue(nuevo.provisional)
        pieza.refresh_from_db()
        self.assertEqual(pieza.posicion_plano_id, punto.pk)
        self.assertEqual(pieza.snapshot['plano']['x'], '0.78860000')
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.post(url, '{}', content_type='application/json').status_code, 403)
        planta = self.usuarios['PLANTA']
        revisado = planos.guardar_mapa(usuario=planta, empresa=self.empresa, referencia_id=self.ref.pk,
            base_version=2, posiciones=posiciones, clave=uuid.uuid4())
        self.assertTrue(revisado.provisional)
        tecnica.estado_referencia(usuario=planta, empresa=self.empresa, referencia_id=self.ref.pk,
            estado='PUBLICADA', clave=uuid.uuid4())
        publicado = planos.guardar_mapa(usuario=planta, empresa=self.empresa, referencia_id=self.ref.pk,
            base_version=3, posiciones=posiciones, clave=uuid.uuid4())
        self.assertFalse(publicado.provisional)
        mapa.refresh_from_db()
        self.assertTrue(mapa.provisional)
        with self.assertRaises(PermissionDenied):
            planos.guardar_mapa(usuario=self.taller, empresa=self.empresa, referencia_id=self.ref.pk,
                base_version=4, posiciones=posiciones, clave=uuid.uuid4())

    def test_carga_componentes_seleccion_envio_y_aprobaciones_posteriores(self):
        self.cargar()
        self.assertEqual(self.ref.estado, 'BORRADOR')
        self.assertFalse(AplicabilidadReferenciaEquipo.objects.filter(referencia=self.ref).exists())
        self.assertFalse(self.ref.partes.exists())
        self.assertContains(self.client.get(self.plano_url), 'plano no aprobado')
        docs = Documento.objects.count()
        planos.cargar_plano_trabajo(usuario=self.taller, empresa=self.empresa, expediente_id=self.exp.pk,
            clave=uuid.uuid4(), archivo=SimpleUploadedFile('plano-qa.pdf', self.pdf, content_type='application/pdf'))
        self.assertEqual(Documento.objects.count(), docs)
        self.assertEqual(self.exp.planos_trabajo.count(), 1)
        parte = self.componente()
        pieza = self.seleccionar(parte)
        self.assertTrue(pieza.snapshot['provisional'])
        foto = pieza.fotografias.get()
        documentos = Documento.objects.count()
        self.diag.refresh_from_db()
        planos.guardar_seleccion(usuario=self.taller, empresa=self.empresa, diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4(), pieza_id=pieza.pk,
            datos=dict(origen='BIBLIOTECA', parte_id=parte.pk, cantidad='2', unidad='unidad',
                condicion='Daño observado QA', accion='EVALUAR'), fotografias=[foto_pieza(), foto_pieza()])
        self.assertEqual(pieza.fotografias.get().pk, foto.pk)
        self.assertEqual(Documento.objects.count(), documentos)
        self.diag.refresh_from_db()
        response = self.client.post(self.url, dict(op='enviar_piezas', clave=uuid.uuid4(), version=self.diag.version_edicion))
        self.assertRedirects(response, self.url)
        sol = SolicitudRepuestos.objects.get(diagnostico=self.diag)
        self.assertTrue(sol.snapshot['preliminar'])
        self.diag.refresh_from_db()
        self.assertIsNone(self.diag.enviado_en)
        self.assertEqual(self.diag.fallas, '')
        self.assertEqual(sol.snapshot['piezas'][0]['fotos'][0]['documento'], pieza.fotografias.get().documento_id)
        self.assertEqual(self.client.post(self.url, dict(op='enviar_piezas', clave=uuid.uuid4(),
            version=self.diag.version_edicion)).status_code, 302)
        self.assertEqual(SolicitudRepuestos.objects.count(), 1)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.diag.piezas.filter(pk=pieza.pk).update(cantidad=5)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            pieza.fotografias.all().delete()
        with self.assertRaises(ValidationError):
            planos.guardar_seleccion(usuario=self.taller, empresa=self.empresa, diagnostico_id=self.diag.pk,
                clave=uuid.uuid4(), version=self.diag.version_edicion, pieza_id=pieza.pk, operacion='retirar')
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(self.url), DESCRIPCION_COMPONENTE)
        self.assertContains(self.client.get(self.plano_url), 'plano no aprobado')
        self.assertFalse(self.client.get(reverse('masiscam:plano_datos', args=[self.ref.pk]),
            {'diagnostico': self.diag.pk}).json()['editable'])
        from .test_utils import consumir_respuesta
        original = self.client.get(reverse('masiscam:biblioteca_documento', args=[self.ref.pk]), {'ver': '1'})
        self.assertEqual(original.status_code, 200)
        self.assertEqual(consumir_respuesta(original), self.pdf)
        from . import operacion_services as operacion
        with self.assertRaises(ValidationError):
            operacion.iniciar_actividad(usuario=self.taller, empresa=self.empresa, expediente_id=self.exp.pk,
                clave=uuid.uuid4(), datos=dict(trabajo='QA', componentes='QA', inicio=qa.timezone.now()))
        with self.assertRaises(PermissionDenied):
            tecnica.estado_referencia(usuario=self.taller, empresa=self.empresa, referencia_id=self.ref.pk,
                clave=uuid.uuid4(), estado='PUBLICADA')
        planta = self.usuarios['PLANTA']
        tecnica.estado_referencia(usuario=planta, empresa=self.empresa, referencia_id=self.ref.pk,
            clave=uuid.uuid4(), estado='PUBLICADA')
        tecnica.aplicar_referencia(usuario=planta, empresa=self.empresa, referencia_id=self.ref.pk,
            equipo_id=self.equipo.pk, fundamento='Revisión explícita QA', clave=uuid.uuid4())
        with self.assertRaises(ValidationError):
            tecnica.enviar_revision(usuario=self.taller, empresa=self.empresa, tipo='DIAGNOSTICO',
                revision_id=self.diag.pk, clave=uuid.uuid4(), version=self.diag.version_edicion)
        # The preliminary handoff must still reach the existing approval and quotation flow.
        ins = self.diag.inspeccion
        common = dict(usuario=self.taller, empresa=self.empresa)
        ins = tecnica.guardar_revision(**common, tipo='INSPECCION', revision_id=ins.pk,
            version=ins.version_edicion, clave=uuid.uuid4(), datos=dict(fecha=qa.timezone.localdate(),
                estado_fisico='Inspeccionado QA', condiciones_generales='QA', hallazgos='Daño QA',
                motivo_sin_evidencia='Fotografía asociada a la pieza'))
        tecnica.enviar_revision(**common, tipo='INSPECCION', revision_id=ins.pk,
            version=ins.version_edicion, clave=uuid.uuid4())
        self.diag = tecnica.guardar_revision(**common, tipo='DIAGNOSTICO', revision_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4(), datos=dict(fecha=qa.timezone.localdate(),
                fallas='Daño QA', causas='Pendiente de análisis QA', componentes_afectados=DESCRIPCION_COMPONENTE,
                recomendaciones='Cotizar pieza QA', conclusiones='Revisión QA', repuestos='REQUERIDOS'))
        tecnica.enviar_revision(**common, tipo='DIAGNOSTICO', revision_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4())
        tecnica.decidir_diagnostico(usuario=planta, empresa=self.empresa, diagnostico_id=self.diag.pk,
            decision='APROBADO', motivo='', clave=uuid.uuid4())
        from . import flujo_services as flujo
        flujo.vigente(sol)
        propuesta = flujo.cotizar(usuario=self.oficina, empresa=self.empresa, solicitud_id=sol.pk,
            clave=uuid.uuid4(), referencia='COTIZACIÓN PRELIMINAR QA')
        self.assertEqual(propuesta.diagnostico_id, self.diag.pk)

    def test_permisos_pdf_invalido_fuente_y_componentes_protegidos(self):
        self.cargar()
        for role in ('OFICINA', 'PLANTA', 'GERENCIA'):
            self.client.force_login(self.usuarios[role])
            self.assertEqual(self.client.post(self.url, dict(op='plano_trabajo', clave=uuid.uuid4(),
                archivo=qa.pdf_plano())).status_code, 403)
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(self.plano_url).status_code, 404)
        self.client.force_login(self.taller)
        docs = Documento.objects.count()
        invalid = SimpleUploadedFile('no.pdf', b'no es un PDF', content_type='application/pdf')
        self.assertEqual(self.client.post(self.url, dict(op='plano_trabajo', clave=uuid.uuid4(), archivo=invalid)).status_code, 400)
        self.assertEqual(Documento.objects.count(), docs)
        parte = self.componente()
        self.seleccionar(parte)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.ref.partes.filter(pk=parte.pk).update(descripcion='Cambio de fuente')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            ReferenciaTecnica.objects.filter(pk=self.ref.pk).update(expediente_trabajo=None)
        equipo = self.crear('OTRO-PLANO')
        from .recepcion_services import abrir_recepcion
        exp = abrir_recepcion(usuario=self.taller, empresa=self.empresa, equipo_id=equipo.pk,
            cliente_id=self.cliente.pk, clave=uuid.uuid4(), tipo='MANTENIMIENTO',
            fecha=qa.timezone.now(), condicion_ingreso='QA', fotografias=[])
        _, diag = tecnica.iniciar_inspeccion(usuario=self.taller, empresa=self.empresa,
            expediente_id=exp.pk, clave=uuid.uuid4())
        self.assertEqual(self.client.get(reverse('masiscam:plano', args=[self.ref.pk]),
            {'diagnostico': diag.pk}).status_code, 403)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS') == '1', 'Navegador QA explícito')
@override_settings(**FLAGS)
class PlanoTrabajoBrowserTests(TrabajoFixture, StaticLiveServerTestCase):
    def test_marcar_con_zoom_desplazar_pulsar_y_recargar(self):
        import re
        from playwright.sync_api import sync_playwright, expect
        self.cargar()
        self.componente()
        planos.registrar_componente_trabajo(usuario=self.taller, empresa=self.empresa,
            referencia_id=self.ref.pk, diagnostico_id=self.diag.pk, clave=uuid.uuid4(),
            datos=dict(posicion='8', descripcion='RADIAL BEARING CARRIER' if PDF_REAL else 'Otra pieza QA',
                numero_parte='M2002B' if PDF_REAL else 'QA-8', unidad='unidad', pagina='1'))
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                context = browser.new_context(viewport=dict(width=1440, height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,
                    value=self.client.cookies[settings.SESSION_COOKIE_NAME].value, url=self.live_server_url)])
                page = context.new_page()
                page.goto(self.live_server_url + self.plano_url)
                expect(page.locator('#pl-app')).to_have_attribute('data-ready', 'true', timeout=20000)
                page.locator('#pl-plus').click(); page.locator('#pl-plus').click()
                expect(page.locator('#pl-zoom')).to_have_text('150%')
                page.locator('#pl-config-toggle').click()
                page.locator('.pl-part').filter(has_text=DESCRIPCION_COMPONENTE).click()
                viewport = page.locator('#pl-viewport')
                viewport.scroll_into_view_if_needed()
                viewport.evaluate("vp => {const s=vp.querySelector('#pl-stage'); vp.scrollLeft=s.clientWidth*.7886-vp.clientWidth/2; vp.scrollTop=s.clientHeight*.3388-vp.clientHeight/2;}")
                bounds = page.locator('#pl-stage').bounding_box()
                page.mouse.click(bounds['x'] + bounds['width']*.7886, bounds['y'] + bounds['height']*.3388)
                expect(page.locator('.pl-point')).to_have_count(1)
                page.locator('#pl-save-map').click()
                expect(page.locator('#pl-map-label')).to_contain_text('v1')
                expect(page.locator('#pl-map-label')).to_contain_text('provisional')
                page.reload()
                expect(page.locator('#pl-app')).to_have_attribute('data-ready', 'true')
                expect(page.locator('.pl-point')).to_have_count(1)
                page.locator('#pl-plus').click()
                expect(page.locator('#pl-zoom')).to_have_text('125%')
                viewport.scroll_into_view_if_needed()
                viewport.evaluate("vp => {const s=vp.querySelector('#pl-stage'); vp.scrollLeft=s.clientWidth*.7886-vp.clientWidth/2; vp.scrollTop=s.clientHeight*.3388-vp.clientHeight/2;}")
                stage = page.locator('#pl-stage').bounding_box()
                marker = page.locator('.pl-point').bounding_box()
                self.assertAlmostEqual((marker['x']+marker['width']/2-stage['x'])/stage['width'], .7886, delta=.002)
                self.assertAlmostEqual((marker['y']+marker['height']/2-stage['y'])/stage['height'], .3388, delta=.002)
                page.locator('.pl-point').click()
                expect(page.locator('.pl-part').filter(has_text=DESCRIPCION_COMPONENTE)).to_have_attribute('aria-pressed', 'true')
                page.get_by_role('button', name='Agregar al diagnóstico', exact=True).click()
                page.locator('#pl-quantity').fill('2')
                page.locator('#pl-condition').fill('Daño de prueba QA')
                page.locator('#pl-action').select_option('EVALUAR')
                page.locator('#pl-submit').click()
                expect(page.locator('#pl-count')).to_have_text('1')
                page.reload()
                expect(page.locator('#pl-app')).to_have_attribute('data-ready', 'true')
                expect(page.locator('#pl-count')).to_have_text('1')
                expect(page.locator('.pl-point')).to_have_class(re.compile('is-selected'))
                if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                    from pathlib import Path
                    page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'marcado-persistido.png'), full_page=True)
            finally:
                browser.close()
        mapa = self.ref.mapas.get()
        self.assertTrue(mapa.provisional)
        self.assertEqual(mapa.posiciones.count(), 1)
        self.assertEqual(self.ref.partes.count(), 2)
        self.assertEqual(self.diag.piezas.get().posicion_plano_id, mapa.posiciones.get().pk)

    def test_taller_carga_pdf_registra_selecciona_y_envia_sin_formulario_tecnico(self):
        from playwright.sync_api import sync_playwright, expect
        from io import BytesIO
        from pathlib import Path
        from pypdf import PdfReader, PdfWriter
        self.equipo = self.crear('NAVEGADOR-PLANO')
        self.exp = self.recepcion(self.equipo)
        client = Client(); client.force_login(self.taller)
        if PDF_REAL:
            pdf = pdf_trabajo().read()
            paginas = len(PdfReader(BytesIO(pdf)).pages)
        else:
            reader = PdfReader(BytesIO(qa.pdf_plano().read()))
            writer = PdfWriter()
            writer.add_page(reader.pages[0]); writer.add_page(reader.pages[0])
            stream = BytesIO(); writer.write(stream); pdf = stream.getvalue()
            paginas = 2
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                context = browser.new_context()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,
                    value=client.cookies[settings.SESSION_COOKIE_NAME].value, url=self.live_server_url)])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('masiscam:flujo_detalle', args=[self.exp.pk]))
                page.locator('#mf-plano input[type=file]').set_input_files(dict(
                    name=Path(PDF_REAL).name if PDF_REAL else 'plano-qa.pdf', mimeType='application/pdf', buffer=pdf))
                page.get_by_role('button', name='Cargar plano PDF', exact=True).click()
                expect(page.locator('#pl-app')).to_have_attribute('data-ready', 'true', timeout=20000)
                expect(page.get_by_text('Borrador de trabajo — plano no aprobado.', exact=True)).to_be_visible()
                expect(page.locator('#pl-canvas')).to_be_visible()
                for width in (1440, 390):
                    page.set_viewport_size(dict(width=width, height=1000))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                        page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR']) / f'plano-trabajo-{width}.png'), full_page=True)
                page.set_viewport_size(dict(width=1440, height=1000))
                page.get_by_role('button', name='Ampliar zoom', exact=True).click()
                expect(page.locator('#pl-zoom')).to_have_text('125%')
                page.locator('#pl-page').select_option(str(paginas))
                expect(page.locator('#pl-page')).to_have_value(str(paginas))
                page.get_by_text('Registrar componente del plano manualmente', exact=True).click()
                component = page.locator('form[action*="/componentes/"]')
                component.locator('[name=posicion]').fill('7')
                component.locator('[name=descripcion]').fill(DESCRIPCION_COMPONENTE)
                component.locator('[name=numero_parte]').fill(CODIGO_COMPONENTE)
                component.locator('[name=unidad]').fill('unidad')
                component.get_by_role('button', name='Guardar componente en borrador').click()
                expect(page.locator('#pl-app')).to_have_attribute('data-ready', 'true')
                page.locator('#pl-search').fill('7')
                page.locator('.pl-part').click()
                page.get_by_role('button', name='Agregar al diagnóstico').click()
                page.locator('#pl-quantity').fill('2')
                page.locator('#pl-condition').fill('Daño observado QA')
                page.locator('#pl-action').select_option('EVALUAR')
                page.locator('#pl-photos').set_input_files(dict(name='pieza.png', mimeType='image/png', buffer=foto_pieza().read()))
                page.get_by_role('button', name='Guardar pieza', exact=True).click()
                expect(page.locator('#pl-count')).to_have_text('1')
                page.get_by_role('link', name='Volver al trabajo').click()
                expect(page.locator('#detalles-tecnicos')).not_to_have_attribute('open', '')
                photo = page.locator('.mf-photos img').first
                photo.scroll_into_view_if_needed()
                expect(photo).to_have_js_property('naturalWidth', 50)
                page.locator('#mf-envio').get_by_role('button', name='Enviar a OFICINA', exact=True).click()
                confirm = page.get_by_role('button', name='Sí, continuar', exact=True)
                if confirm.is_visible():
                    confirm.click()
                expect(page.get_by_text('Solicitud v1 enviada', exact=False)).to_be_visible()
            finally:
                browser.close()
        sol = SolicitudRepuestos.objects.get(expediente=self.exp)
        self.assertIsNone(sol.diagnostico.enviado_en)
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(reverse('masiscam:flujo_detalle', args=[self.exp.pk])), DESCRIPCION_COMPONENTE)


@override_settings(**FLAGS)
class PlanoTrabajoMigrationTests(TransactionTestCase):
    def test_migracion_0038_conserva_referencias_documentos_y_partes_existentes(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        self.empresa, self.oficina, self.planta, self.tecnico, self.exp = qa.preparar()
        qa.TecnicaTests.referencia(self)
        old = [('masiscam', '0037_diagnostico_borrador_inspeccion')]
        latest = [('masiscam', '0038_planos_trabajo')]
        try:
            executor = MigrationExecutor(connection); executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            before = {name: list(apps.get_model('masiscam', name).objects.values().order_by('pk'))
                for name in ('ReferenciaTecnica', 'ParteReferenciaTecnica', 'Documento', 'Diagnostico')}
            executor = MigrationExecutor(connection); executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            for name, rows in before.items():
                if rows:
                    self.assertEqual(list(apps.get_model('masiscam', name).objects.values(*rows[0]).order_by('pk')), rows)
            self.assertFalse(apps.get_model('masiscam', 'ReferenciaTecnica').objects.filter(expediente_trabajo__isnull=False).exists())
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

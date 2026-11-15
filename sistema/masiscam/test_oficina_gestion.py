import os
import uuid
from decimal import Decimal
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import flujo_services as s, operacion_services as o, tecnica_services as t
from .test_tres_roles import TresRolesFixture
from . import test_flujo as qa_flujo
from .test_operacion import presupuesto_qa, verificacion_qa
from .test_tecnica import pdf_plano
from .models import PropuestaComercial, EnvioRepuestos, RecepcionRepuesto, Auditoria

FLAGS=qa_flujo.FLAGS


class OficinaFixture(TresRolesFixture):
    def preparar(self):
        self.preparar_solicitud()
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,
            decision='APROBADO',motivo='Prueba aislada',clave=uuid.uuid4())
        self.pieza=self.diag.piezas.get()
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.oficina)

    def detalle(self,precio='12.35'):
        return dict(moneda='USD',lineas=[dict(pieza=self.pieza.pk,precio_unitario=precio)],
            factura=dict(numero='FACTURA-REFERENCIA-QA',fecha='2026-11-14'))

    def cotizar(self,**changes):
        from django.core.files.uploadedfile import SimpleUploadedFile
        if not hasattr(self,'presupuesto_bytes'):self.presupuesto_bytes=presupuesto_qa().read()
        data=dict(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,
            clave=uuid.uuid4(),referencia='COTIZACION-QA',cotizacion=self.detalle(),
            archivo=SimpleUploadedFile('presupuesto-qa.pdf',self.presupuesto_bytes,content_type='application/pdf'))
        data.update(changes)
        return s.cotizar(**data)

    def aceptar(self,propuesta):
        return o.decidir_comercial(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk,
            clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(decision='APROBADO',fecha_cliente=timezone.now(),
                representante_cliente='Cliente ficticio QA',motivo='Aceptación simulada aislada',**verificacion_qa()))


@override_settings(**FLAGS)
class OficinaGestionTests(OficinaFixture,TestCase):
    def test_precios_factura_versiones_y_reintento(self):
        self.preparar();key=uuid.uuid4()
        propuesta=self.cotizar(clave=key)
        self.assertEqual(self.cotizar(clave=key).pk,propuesta.pk)
        quote=propuesta.snapshot['cotizacion']
        self.assertEqual(quote['total'],'24.70')
        self.assertEqual(quote['factura']['numero'],'FACTURA-REFERENCIA-QA')
        self.assertEqual(PropuestaComercial.objects.count(),1)
        self.aceptar(propuesta)
        anterior=propuesta.snapshot.copy()
        nueva=self.cotizar(cotizacion=self.detalle('13.00'))
        self.assertEqual(nueva.version,2);self.assertEqual(nueva.sustituye_id,propuesta.pk)
        propuesta.refresh_from_db();self.assertEqual(propuesta.snapshot,anterior)
        with self.assertRaises(ValidationError):s.autorizacion(self.sol)
        with transaction.atomic(),self.assertRaises(IntegrityError):
            PropuestaComercial.objects.filter(pk=propuesta.pk).update(snapshot={})

    def test_cotizacion_opcional_pdf_y_guardas_servidor(self):
        self.preparar()
        for detail in (self.detalle('NaN'),self.detalle('-1'),self.detalle('1.001'),
                dict(self.detalle(),lineas=[]),dict(self.detalle(),lineas=[dict(pieza=99999,precio_unitario='1')])):
            with self.assertRaises(ValidationError):self.cotizar(cotizacion=detail)
        propuesta=self.cotizar(archivo=None)
        self.assertFalse(propuesta.evidencias.exists())
        with self.assertRaises(ValidationError):self.aceptar(propuesta)
        for user in (self.taller,self.admin,self.otro):
            with self.assertRaises(PermissionDenied):self.cotizar(usuario=user)
        response=self.client.get(self.url)
        self.assertContains(response,'Cotizado');self.assertNotContains(response,'Recepción y documentos')
        self.assertNotContains(response,'<footer class="mf-links">')

    def test_autorizacion_disponibilidad_envio_y_recepcion(self):
        self.preparar();propuesta=self.cotizar()
        with self.assertRaises(ValidationError):self.envio()
        self.aceptar(propuesta)
        self.assertFalse(self.client.get(self.url).context['oficina_envio'])
        compra,envio=self.envio()
        with self.assertRaises(ValidationError):self.cotizar(cotizacion=self.detalle('99.00'))
        self.assertEqual(s.autorizacion(self.sol).propuesta_id,propuesta.pk)
        kwargs=dict(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,numero_guia=envio.numero_guia,
            fecha=envio.fecha,lineas=[dict(adquisicion=compra.pk,cantidad='2')])
        self.assertEqual(s.enviar_repuestos(**kwargs,clave=envio.clave).pk,envio.pk)
        with self.assertRaises(ValidationError):s.enviar_repuestos(**kwargs,clave=uuid.uuid4())
        self.assertEqual(EnvioRepuestos.objects.count(),1)
        response=self.client.get(self.url);self.assertEqual(response.context['oficina_estado'],'Enviado a TALLER')
        self.client.force_login(self.taller)
        self.assertContains(self.client.get(self.url),'data-notificacion-envio')
        with self.assertRaises(ValidationError):
            o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
                datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.recibir(envio)
        self.assertEqual(RecepcionRepuesto.objects.count(),1)

    test_reintentos_http=qa_flujo.FlujoTests.test_reintentos_http_no_duplican_solicitud_envio_ni_recepcion
    test_sin_autorizacion=qa_flujo.FlujoTests.test_compras_y_reparaciones_no_se_crean_sin_autorizacion
    test_sin_repuestos=qa_flujo.FlujoTests.test_sin_repuestos_no_obliga_a_inventar_piezas_compras_o_envios
    test_recepcion_politica_retirada=qa_flujo.FlujoTests.test_recepcion_fisica_con_politica_retirada_no_autoriza_reparar
    test_aceptacion_parcial=qa_flujo.FlujoTests.test_aceptacion_parcial_no_autoriza_comprar_el_listado_completo
    def test_multiempresa(self):
        from accounts.models import Empresa
        from .test_tecnica import usuario
        self.preparar()
        otra=Empresa.objects.create(nombre='Empresa ajena QA')
        ajena=usuario(otra,'oficina-ajena-gestion','OFICINA')
        self.client.force_login(ajena)
        self.assertEqual(self.client.get(self.url).status_code,403)
        with self.assertRaises(PermissionDenied):self.cotizar(usuario=ajena,empresa=otra)
        self.client.force_login(self.taller)
        self.assertEqual(self.client.post(self.url,dict(op='cotizar',clave=uuid.uuid4(),referencia='QA',moneda='USD',
            **{f'precio_{self.pieza.pk}':'1'})).status_code,403)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador aislado explícito')
@override_settings(**FLAGS)
class OficinaGestionBrowserTests(OficinaFixture,StaticLiveServerTestCase):
    def test_cotizar_factura_autorizar_enviar_y_recibir(self):
        from playwright.sync_api import sync_playwright, expect
        self.preparar()
        taller_client=Client();taller_client.force_login(self.taller)
        taller_cookie=taller_client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.url)
                expect(page.locator('#oficina-estado')).to_have_text('Pendiente')
                expect(page.get_by_role('link',name='Recepción y documentos',exact=True)).to_have_count(0)
                expect(page.locator('footer.mf-links')).to_have_count(0)
                expect(page.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True)).to_be_disabled()
                quote=page.locator('#oficina-cotizacion')
                quote.locator('[data-precio]').fill('12.35')
                expect(page.locator('#oficina-total')).to_have_text('24.70')
                quote.locator('[name=referencia]').fill('COTIZACION-UI-QA')
                quote.locator('[name=moneda]').fill('USD')
                quote.locator('[name=factura_numero]').fill('FACTURA-UI-QA')
                quote.locator('[name=factura_fecha]').fill('2026-11-14')
                pdf=presupuesto_qa()
                quote.locator('[name=archivo]').set_input_files(dict(name=pdf.name,mimeType='application/pdf',buffer=pdf.read()))
                quote.get_by_role('button',name='Guardar cotización',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.locator('#oficina-estado')).to_have_text('Cotizado')
                expect(page.locator('[name=factura_numero]')).to_have_value('FACTURA-UI-QA')
                page.locator('#oficina-aceptacion summary').click()
                accept=page.locator('#oficina-aceptacion form')
                accept.locator('[name=decision]').select_option('APROBADO')
                accept.locator('[name=fecha_cliente]').fill('2026-11-14T12:00')
                for name,value in dict(representante_cliente='Cliente ficticio UI',motivo='Aceptación QA',
                        identidad='Identidad QA',medio_verificacion='Verificación QA',respaldo_verificacion='Respaldo QA').items():
                    accept.locator(f'[name={name}]').fill(value)
                accept.locator('[name=calidad]').select_option('TITULAR')
                accept.locator('[name=canal]').select_option('PRESENCIAL_FIRMADA')
                accept.locator('[name=verificacion_confirmada]').check()
                pdf=pdf_plano();accept.locator('[name=archivo]').set_input_files(dict(name=pdf.name,mimeType='application/pdf',buffer=pdf.read()))
                accept.get_by_role('button',name='Registrar respuesta del cliente').click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.locator('#oficina-estado')).to_have_text('Autorizado')
                expect(page.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True)).to_be_disabled()
                page.locator('#oficina-disponibles summary').click();buy=page.locator('#oficina-disponibles form')
                buy.locator('[name=pieza_id]').select_option(str(self.pieza.pk))
                for name,value in dict(cantidad='2',proveedor='Proveedor QA',referencia='FACTURA-UI-QA',fecha='2026-11-14T13:00').items():
                    buy.locator(f'[name={name}]').fill(value)
                buy.get_by_role('button',name='Guardar disponibilidad').click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                send=page.locator('#oficina-enviar');expect(send).to_be_visible()
                for width in (390,1440):
                    page.set_viewport_size(dict(width=width,height=1000))
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                        from pathlib import Path
                        page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'oficina-{width}.png'),full_page=True)
                    overflow=page.evaluate('Array.from(document.querySelectorAll("main *")).filter(e=>e.getBoundingClientRect().right>innerWidth+1 && getComputedStyle(e).display!=="none").map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,12)')
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),overflow)
                send.locator('[name=numero_guia]').fill('GUIA-UI-QA')
                send.locator('[name=fecha]').fill('2026-11-14T14:00')
                send.locator('[name^=cantidad_]').fill('2')
                send.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.locator('#oficina-estado')).to_have_text('Enviado a TALLER')
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=taller_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
                expect(page.locator('[data-notificacion-envio]')).to_be_visible()
                receive=page.locator('form').filter(has=page.locator('[name=op][value=recibir]'))
                receive.locator('[name=fecha]').fill('2026-11-14T15:00')
                receive.locator('[name^=cantidad_]').fill('2')
                receive.get_by_role('button',name='Confirmar repuestos recibidos',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.get_by_text('Recibida en taller',exact=False)).to_be_visible()
            finally:browser.close()
        propuesta=PropuestaComercial.objects.get(expediente=self.exp)
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'24.70')
        self.assertEqual(EnvioRepuestos.objects.filter(expediente=self.exp).count(),1)
        self.assertEqual(RecepcionRepuesto.objects.filter(expediente=self.exp).count(),1)

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
    def preparar(self,aprobar_tecnico=True):
        self.preparar_solicitud()
        if aprobar_tecnico:
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
        if not s.aprobacion_conjunta_vigente(propuesta):
            s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,
                diagnostico_id=propuesta.diagnostico_id,propuesta_id=propuesta.pk,decision='APROBADO',clave=uuid.uuid4())
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
        self.assertEqual(propuesta.evidencias.count(),1)
        with self.assertRaises(ValidationError):s.autorizacion(self.sol)
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
    def test_aceptacion_parcial(self):
        qa_flujo.FlujoTests.test_aceptacion_parcial_no_autoriza_comprar_el_listado_completo(self)
        parcial=PropuestaComercial.objects.filter(snapshot__has_key='propuesta_origen').get()
        with self.assertRaises(ValidationError):
            s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=parcial.pk)
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

    def test_pdf_versionado_integridad_y_permisos(self):
        from io import BytesIO
        from pathlib import Path
        from pypdf import PdfReader
        from accounts.models import Empresa
        from .test_tecnica import usuario
        self.preparar()
        propuesta=self.cotizar(archivo=None,observaciones='Observaciones de prueba',
            cotizacion=dict(self.detalle(),condiciones='Entrega acordada con el cliente.'))
        ev=s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk)
        self.assertEqual(propuesta.evidencias.count(),1)
        doc=ev.documento
        with doc.archivo.open('rb') as archivo:contenido=archivo.read()
        pdf=PdfReader(BytesIO(contenido));text='\n'.join(page.extract_text() for page in pdf.pages)
        for esperado in ('COTIZACION-QA','24.70','12.35','USD',self.exp.numero,
                propuesta.snapshot['cliente']['ruc'],'Entrega acordada','ACEPTACIÓN DEL CLIENTE'):
            self.assertIn(esperado,text)
        self.assertTrue(pdf.pages[0].images)
        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
            (Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'cotizacion-v1.pdf').write_bytes(contenido)
        original_hash=doc.hash_sha256
        nueva=self.cotizar(archivo=None,cotizacion=self.detalle('15.00'))
        self.assertEqual(nueva.version,2)
        self.assertEqual(nueva.evidencias.count(),1)
        self.assertNotEqual(nueva.evidencias.get().documento_id,doc.pk)
        doc.refresh_from_db();self.assertEqual(doc.hash_sha256,original_hash)
        t.documento_valido(doc,self.empresa)
        url=reverse('masiscam:operacion_documento',args=[self.exp.pk,doc.pk])
        for suffix,disposition in (('','attachment'),('?ver=1','inline')):
            response=self.client.get(url+suffix)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response['Content-Disposition'].startswith(disposition))
            self.assertEqual(response['Content-Type'],'application/pdf')
            self.assertEqual(b''.join(response.streaming_content),contenido)
        historica=o.proponer(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,
            diagnostico_id=self.diag.pk,clave=uuid.uuid4(),datos=dict(alcance='Alcance existente de prueba',
                presupuesto_referencia='COT-HISTORICA-QA',cotizacion=self.detalle()))
        self.assertFalse(historica.evidencias.exists())
        self.assertContains(self.client.get(self.url),'Generar PDF de esta versión')
        payload=dict(op='pdf_cotizacion',propuesta_id=historica.pk,clave=uuid.uuid4())
        for _ in range(2):self.assertEqual(self.client.post(self.url,payload).status_code,302)
        self.assertEqual(historica.evidencias.count(),1)
        self.assertEqual(PropuestaComercial.objects.count(),3)
        for user in (self.taller,usuario(Empresa.objects.create(nombre='Ajena PDF'),'oficina-pdf-ajena','OFICINA')):
            self.client.force_login(user)
            self.assertIn(self.client.get(url).status_code,(403,404))
            with self.assertRaises(PermissionDenied):
                s.generar_cotizacion_pdf(usuario=user,empresa=self.empresa,propuesta_id=propuesta.pk)

    def test_usd_fijo_calculos_y_condiciones_invalidan_aceptacion(self):
        from .flujo_forms import CotizacionForm
        self.preparar()
        self.assertNotIn('moneda',CotizacionForm(solicitud=self.sol).fields)
        with self.assertRaises(ValidationError):self.cotizar(cotizacion=dict(self.detalle(),moneda='EUR'))
        propuesta=self.cotizar(archivo=None)
        self.aceptar(propuesta)
        nueva=self.cotizar(archivo=None,cotizacion=dict(self.detalle(),condiciones='Nueva condición explícita'))
        self.assertIn('Nueva condición explícita',nueva.alcance)
        with self.assertRaises(ValidationError):s.autorizacion(self.sol)
        self.assertEqual(nueva.snapshot['cotizacion']['total'],'24.70')

    def test_pdf_extenso_conserva_detalle_y_paginas(self):
        from io import BytesIO
        from pathlib import Path
        from pypdf import PdfReader
        from .flujo_pdf import cotizacion_comercial
        contenido=cotizacion_comercial(dict(referencia='COTIZACION-EXTENSA-QA',version=1,fecha='14/11/2026',
            cliente=dict(cliente='Cliente ficticio de prueba',ruc='RUC-QA',equipo='DERAN',serie='SERIE-QA'),expediente='EXP-QA',
            lineas=[dict(posicion=str(i),codigo=f'COD-QA-{i}',descripcion='Componente registrado por TALLER. '*5,
                condicion='Desgaste registrado.',cantidad='2',precio_unitario='12.35',total='24.70') for i in range(1,36)],
            total='864.50',observaciones='Observaciones de prueba. '*30,condiciones='Condiciones aportadas por OFICINA. '*20))
        pdf=PdfReader(BytesIO(contenido))
        self.assertGreater(len(pdf.pages),1)
        text='\n'.join(page.extract_text() for page in pdf.pages)
        for value in ('COD-QA-35','864.50','ACEPTACIÓN DEL CLIENTE'):self.assertIn(value,text)
        for i,page in enumerate(pdf.pages,1):self.assertIn(f'Página {i}',page.extract_text())
        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
            (Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'cotizacion-extensa.pdf').write_bytes(contenido)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador aislado explícito')
@override_settings(**FLAGS)
class OficinaGestionBrowserTests(OficinaFixture,StaticLiveServerTestCase):
    def test_devolucion_con_motivo_sweetalert_y_destinatario(self):
        from playwright.sync_api import sync_playwright,expect
        self.preparar(aprobar_tecnico=False);self.cotizar(archivo=None)
        admin_client=Client();admin_client.force_login(self.admin)
        admin_cookie=admin_client.cookies[settings.SESSION_COOKIE_NAME].value
        oficina_cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=admin_cookie,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.url)
                expect(page.locator('#pl-preview')).to_have_attribute('data-ready','true')
                for width in (390,1440):
                    page.set_viewport_size(dict(width=width,height=1000))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                        from pathlib import Path
                        page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'admin-revision-{width}.png'),full_page=True)
                page.get_by_role('button',name='Devolver para corrección',exact=True).click()
                page.get_by_role('button',name='Devolver expediente',exact=True).click()
                expect(page.locator('.swal2-validation-message')).to_be_visible()
                page.locator('.swal2-textarea').fill('Corregir condiciones comerciales QA')
                page.locator('#revision-destino').select_option('OFICINA')
                page.get_by_role('button',name='Devolver expediente',exact=True).click()
                expect(page.get_by_text('Devuelto para corrección',exact=True)).to_be_visible()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=oficina_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
                expect(page.locator('[data-devolucion-expediente]')).to_contain_text('Corregir condiciones comerciales QA')
            finally:browser.close()

    def test_cotizar_factura_autorizar_enviar_y_recibir(self):
        from playwright.sync_api import sync_playwright, expect
        self.preparar_solicitud(enviar=False)
        self.pieza=self.diag.piezas.get()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4(),preliminar=True)
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.oficina)
        admin_client=Client();admin_client.force_login(self.admin)
        admin_cookie=admin_client.cookies[settings.SESSION_COOKIE_NAME].value
        oficina_cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
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
                expect(page.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True)).to_have_count(0)
                quote=page.locator('#oficina-cotizacion')
                expect(page.locator('[data-diagnostico-pendiente]')).to_be_visible()
                expect(quote.locator('[data-cantidad-visible]')).to_have_text('2')
                expect(quote.get_by_role('columnheader',name='Unidad registrada')).to_have_count(1)
                quote.locator('[data-precio]').fill('12.35')
                expect(page.locator('#oficina-total')).to_have_text('24.70')
                quote.locator('[name=referencia]').fill('COTIZACION-UI-QA')
                expect(quote.locator('[name=moneda]')).to_have_count(0)
                expect(quote.locator('[data-total-general]')).to_have_text('24.70')
                quote.locator('[name=factura_numero]').fill('FACTURA-UI-QA')
                quote.locator('[name=factura_fecha]').fill('2026-11-14')
                pdf=presupuesto_qa()
                quote.locator('[name=archivo]').set_input_files(dict(name=pdf.name,mimeType='application/pdf',buffer=pdf.read()))
                quote.get_by_role('button',name='Guardar cotización y enviar a ADMINISTRADOR',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.locator('#oficina-estado')).to_have_text('Cotizado')
                expect(page.locator('[name=factura_numero]')).to_have_value('FACTURA-UI-QA')
                documents=page.locator('#oficina-documentos')
                expect(documents.get_by_text('Cotización MASISCAM v1',exact=False)).to_be_visible()
                for name,disposition in (('Visualizar PDF','inline'),('Descargar PDF','attachment')):
                    link=documents.get_by_role('link',name=name,exact=True).last
                    response=context.request.get(self.live_server_url+link.get_attribute('href'))
                    self.assertEqual(response.status,200)
                    self.assertTrue(response.headers['content-disposition'].startswith(disposition))
                    self.assertTrue(response.body().startswith(b'%PDF-'))
                expect(page.locator('#oficina-aceptacion')).to_have_count(0)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=admin_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
                expect(page.get_by_role('button',name='Aprobar expediente',exact=True)).to_be_disabled()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=taller_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
                page.locator('#informacion-tecnica summary').click()
                page.get_by_role('button',name='Validar diagnóstico para ADMINISTRADOR',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.locator('#mf-diagnostico')).to_have_count(0)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=admin_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
                expect(page.locator('#revision-expediente')).to_be_visible()
                expect(page.locator('#pl-preview')).to_have_attribute('data-ready','true')
                expect(page.get_by_role('button',name='Registrar asignación',exact=True)).to_have_count(0)
                expect(page.get_by_role('button',name='Aprobar expediente',exact=True)).to_be_enabled()
                page.get_by_role('button',name='Aprobar expediente',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.get_by_text('Expediente aprobado',exact=True)).to_be_visible()
                if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                    from pathlib import Path
                    page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'administrador-expediente.png'),full_page=True)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=oficina_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
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
                expect(page.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True)).to_have_count(0)
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
                    overflow=page.evaluate('Array.from(document.querySelectorAll("body *")).filter(e=>e.getBoundingClientRect().right>innerWidth+1 && getComputedStyle(e).display!=="none" && !e.closest(".table-responsive")).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,12)')
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),overflow)
                send.locator('[name=numero_guia]').fill('GUIA-UI-QA')
                send.locator('[name=fecha]').fill('2026-11-14T14:00')
                send.locator('[name^=cantidad_]').fill('2')
                send.get_by_role('button',name='Enviar repuestos a TALLER',exact=True).click()
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

import os
import re
import uuid
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_flujo as qa, flujo_services as s
from .models import Equipo, EnvioRepuestos


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador QA explícito')
@override_settings(**qa.FLAGS)
class OficinaBrowserTests(StaticLiveServerTestCase):
    setUp=qa.FlujoTests.setUp
    crear=qa.FlujoTests.crear
    datos=qa.FlujoTests.datos
    recepcion=qa.FlujoTests.recepcion
    preparar_solicitud=qa.FlujoTests.preparar_solicitud
    comercial=qa.FlujoTests.comercial

    def preparar(self):
        self.preparar_solicitud();self.comercial()
        compra=s.adquirir(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,
            pieza_id=self.diag.piezas.get().pk,cantidad='2',proveedor='PROVEEDOR FICTICIO QA',
            referencia='COMPRA FICTICIA QA',fecha=timezone.now(),clave=uuid.uuid4())
        cookies={}
        for name,user in [('OFICINA',self.oficina),('TALLER',self.taller),('ADMIN',self.root)]:
            client=Client();client.force_login(user)
            cookies[name]=client.cookies[settings.SESSION_COOKIE_NAME].value
        return compra,cookies

    def test_oficina_taller_sweetalert_cancelacion_envio_y_tres_tamanos(self):
        from playwright.sync_api import sync_playwright,expect
        compra,cookies=self.preparar()
        # Envío histórico (ya no se crea desde la interfaz): solo se conserva su lectura.
        envio_historico=s.enviar_repuestos(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,numero_guia='GUIA QA 001',
            fecha=timezone.now(),lineas=[dict(adquisicion=compra.pk,cantidad='2')],clave=uuid.uuid4())
        output=Path(os.environ.get('MASISCAM_UI_EVIDENCE_DIR',settings.BASE_DIR/'.venv-rbac/oficina-evidencias'))
        output.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                contexts={};pages={}
                for role in ('OFICINA','TALLER'):
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    contexts[role]=context
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies[role],url=self.live_server_url)])
                    page=pages[role]=context.new_page()
                    for width in (1440,768,390):
                        page.set_viewport_size({'width':width,'height':1000})
                        self.assertEqual(page.goto(self.live_server_url+reverse('masiscam:dashboard')).status,200)
                        self.assertEqual(page.evaluate('Swal.version'),'11.26.25')
                        icon=page.locator('link[rel=icon][type=\"image/png\"]').first.get_attribute('href')
                        response=page.request.get(self.live_server_url+icon)
                        self.assertEqual(response.status,200)
                        self.assertIn('image/png',response.headers['content-type'])
                        self.assertEqual(page.locator('nav[aria-label="Migas de pan"] ol').count(),1)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                        menu=page.locator('#appMenu')
                        if width<992:
                            page.locator('.app-menu-toggle').click();expect(menu).to_be_visible()
                            expect(menu).to_have_class(re.compile(r'\bcollapse\b.*\bshow\b'))
                        if role=='OFICINA':
                            expect(menu.locator('a')).to_have_text(['Inicio','Bombas','Reductores','Clientes','Solicitudes de taller','Cotizaciones','Planos y piezas'])
                            expect(page.locator('.of-metric')).to_have_count(3)
                        else:
                            self.assertNotIn('Cotizaciones',menu.inner_text())
                            self.assertNotIn('Compras',menu.inner_text())
                        if width<992:
                            page.locator('.app-menu-toggle').click();expect(menu).not_to_be_visible()
                        page.screenshot(path=str(output/f'{role.lower()}-inicio-{width}.png'),full_page=True)
                oficina=pages['OFICINA'];oficina.set_viewport_size({'width':390,'height':1000})
                flujo=self.live_server_url+reverse('masiscam:flujo_detalle',args=[self.exp.pk])
                for retirado in ('[name=op][value=enviar]','[name=op][value=adquirir]'):
                    oficina.goto(flujo)
                    expect(oficina.locator(retirado)).to_have_count(0)
                expect(oficina.get_by_text('Enviar repuestos a TALLER')).to_have_count(0)
                oficina.goto(self.live_server_url+reverse('masiscam:oficina_seccion',args=['guias']))
                historico=oficina.request.get(oficina.url)   # lectura histórica autorizada, sin acciones de envío
                self.assertEqual(historico.status,200)
                self.assertIn(envio_historico.numero_guia,historico.text())
                self.assertIn('Guías de envío históricas',historico.text())
                oficina.goto(flujo)
                expect(oficina.get_by_role('link',name='Descargar guía',exact=True)).to_be_visible()
                taller=pages['TALLER'];taller.goto(self.live_server_url+reverse('masiscam:taller_inicio'))
                expect(taller.get_by_text('Envío histórico por confirmar',exact=True).first).to_be_visible()
                taller.screenshot(path=str(output/'taller-notificacion-repuestos-390.png'),full_page=True)
                for context in contexts.values(): context.close()
            finally: browser.close()
        self.assertEqual(EnvioRepuestos.objects.filter(expediente=self.exp).count(),1)   # solo el histórico

    def test_admin_desactiva_con_confirmacion_y_reactiva_sin_perder_historia(self):
        from playwright.sync_api import sync_playwright,expect
        _,cookies=self.preparar()
        token=self.equipo.token_publico;sha=self.exp.recepcion.confirmacion.guia.hash_sha256
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies['ADMIN'],url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+reverse('masiscam:producto_listado',args=['reductor']))
                form=page.locator('form').filter(has=page.locator(f'[name=equipo_id][value="{self.equipo.pk}"]'))
                form.get_by_role('button',name='Desactivar',exact=True).click()
                expect(page.locator('.swal2-popup')).to_be_visible()
                page.get_by_role('button',name='Cancelar',exact=True).click()
                expect(form.get_by_role('button',name='Desactivar',exact=True)).to_be_visible()
                form.get_by_role('button',name='Desactivar',exact=True).click()
                with page.expect_navigation(): page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(form.get_by_role('button',name='Activar',exact=True)).to_be_visible()
                form.get_by_role('button',name='Activar',exact=True).click()
                with page.expect_navigation(): page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(form.get_by_role('button',name='Desactivar',exact=True)).to_be_visible()
                context.close()
            finally: browser.close()
        self.equipo.refresh_from_db();self.exp.recepcion.confirmacion.guia.refresh_from_db()
        self.assertEqual(self.equipo.estado,'ACTIVO');self.assertEqual(self.equipo.token_publico,token)
        self.assertEqual(self.exp.recepcion.confirmacion.guia.hash_sha256,sha)

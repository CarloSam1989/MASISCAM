"""Optional real-browser regression against disposable QA settings only."""
import os
import re
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from . import test_taller


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS') == '1', 'Activar navegador QA explícitamente')
@override_settings(GOOGLE_DRIVE_ENABLED=False, MASISCAM_RECEPCION_LOCAL=True,
    MASISCAM_TECNICA_LOCAL=True, MASISCAM_OPERACION_LOCAL=True,
    MASISCAM_CALIBRACION_LOCAL=True, MASISCAM_CIERRE_LOCAL=True)
class TallerBrowserTests(StaticLiveServerTestCase):
    setUp = test_taller.TallerTests.setUp
    datos = test_taller.TallerTests.datos
    crear = test_taller.TallerTests.crear
    recepcion = test_taller.TallerTests.recepcion

    def test_flujo_y_plano_taller_en_tres_tamanos(self):
        import uuid
        from playwright.sync_api import sync_playwright, expect
        from . import test_tecnica as qa, plano_services as planos
        from .recepcion_confirmacion import confirmar_recepcion
        equipo = self.crear('FLUJO-UI')
        exp = self.recepcion(equipo)
        confirmar_recepcion(usuario=self.taller, empresa=self.empresa, expediente_id=exp.pk,
            clave=uuid.uuid4(), placa='SIN_PLACA', motivo_sin_placa='QA', motivo_sin_guia='QA',
            general_id=exp.recepcion.evidencias.get().pk)
        qa.inspeccion_enviada(self.empresa, self.taller, exp)
        self.exp, self.planta = exp, self.usuarios['PLANTA']
        ref, parte = qa.TecnicaTests.referencia(self)
        planos.guardar_mapa(usuario=self.planta, empresa=self.empresa, referencia_id=ref.pk,
            clave=uuid.uuid4(), base_version=0, posiciones=[dict(parte=parte.pk, pagina=1, x='.5', y='.4')])
        diag = qa.s.crear_revision(usuario=self.taller, empresa=self.empresa, expediente_id=exp.pk,
            tipo='DIAGNOSTICO', clave=uuid.uuid4())
        client = Client(); client.force_login(self.taller)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        output = Path(os.environ.get('MASISCAM_UI_EVIDENCE_DIR', settings.BASE_DIR/'.venv-rbac/taller-operativo-evidencias'))
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for width in (1440,768,390):
                    context = browser.new_context(viewport={'width':width,'height':1000})
                    context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':cookie,'url':self.live_server_url}])
                    page = context.new_page()
                    page.goto(self.live_server_url + reverse('masiscam:taller_equipo',args=[equipo.pk]))
                    expect(page.locator('ol.mf-steps > li')).to_have_count(5)
                    expect(page.locator('.mf-main-action')).to_have_count(0)  # recepción y inspección ya completas: la acción es abrir el plano
                    expect(page.get_by_text('Subir plano PDF')).to_have_count(0)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                    page.screenshot(path=str(output/f'taller-flujo-{width}.png'),full_page=True)
                    page.goto(self.live_server_url + reverse('masiscam:plano',args=[ref.pk])+'?diagnostico='+str(diag.pk))
                    expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                    page.locator('#pl-plus').click()
                    expect(page.locator('#pl-zoom')).to_have_text('125%')
                    page.locator('.pl-point').first.click()
                    if width == 1440:  # el marcador abre el formulario único; en los demás anchos la pieza ya está seleccionada
                        expect(page.locator('#pl-position-dialog')).to_be_visible()
                        page.locator('#pl-position-quantity').fill('1')
                        page.locator('#pl-position-condition').fill('Desgaste QA')
                        page.locator('#pl-position-action').select_option('EVALUAR')
                        page.locator('#pl-position-save').click()
                    expect(page.locator('#pl-position-dialog')).not_to_be_visible(timeout=15000)
                    if width < 992: page.get_by_role('tab',name=re.compile('Seleccionados')).click()
                    expect(page.locator('#pl-selected-list')).to_contain_text('Componente QA')
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                    page.screenshot(path=str(output/f'taller-plano-{width}.png'),full_page=True)
                    context.close()
            finally:
                browser.close()

    def test_menu_y_panel_en_escritorio_tablet_y_movil(self):
        from playwright.sync_api import sync_playwright, expect

        equipo = self.crear('NAVEGADOR')
        exp = self.recepcion(equipo)
        self.crear('BOMBA', tipo='BOMBA')
        self.crear('INGRESO')
        cookies = {}
        for codigo, user in {**self.usuarios, 'VACIO': self.otro}.items():
            client = Client()
            client.force_login(user)
            cookies[codigo] = client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for width in (1440, 768, 390):
                    for codigo in cookies:
                        with self.subTest(ancho=width, rol=codigo):
                            context = browser.new_context(viewport={'width': width, 'height': 900})
                            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                                'value': cookies[codigo],
                                'url': self.live_server_url}])
                            page = context.new_page()
                            page.goto(self.live_server_url + reverse('masiscam:dashboard'))
                            menu = page.locator('#appMenu')
                            toggle = page.locator('.app-menu-toggle')
                            if width < 992:
                                expect(toggle).to_be_visible()
                                toggle.click()
                                expect(menu).to_have_class(re.compile(r'\bcollapse\b.*\bshow\b'))
                            else:
                                expect(toggle).not_to_be_visible()
                            expect(menu).to_be_visible()
                            if codigo in {'TALLER', 'TECNICO', 'VACIO'}:
                                self.assertTrue(page.url.endswith('/app/taller/'))
                                # TALLER no administra planos: sin «Planos y piezas» en el menú ni carga de PDF.
                                expect(menu.locator('a')).to_have_text(['Inicio', 'Reductores', 'Equipos', 'Mis trabajos', 'Mi cuenta'])
                                expect(menu.get_by_role('link', name=re.compile('Planos'))).to_have_count(0)
                                expect(page.get_by_text('Subir plano PDF')).to_have_count(0)
                                expect(page.locator('.mc-logout').get_by_role('button', name='Salir')).to_be_visible()
                                action = page.get_by_role('button', name='Registrar nuevo equipo', exact=True)
                                expect(action).to_have_count(1)
                                if codigo == 'TALLER':
                                    expect(page.locator('main')).to_contain_text(exp.numero)
                                    expect(page.locator('section[aria-labelledby=trabajos-titulo] tbody tr')).to_have_count(3)  # las tarjetas pasan a filas de tabla
                                    expect(page.locator('[data-metrica="equipos"]')).to_have_text('3')
                                else:
                                    expect(page.locator('.tw-empty')).to_be_visible()
                                    expect(page.locator('[data-metrica="equipos"]')).to_have_text('0')
                                    expect(page.locator('section[aria-labelledby=trabajos-titulo] table')).to_have_count(0)
                                if width < 992:
                                    toggle.click()
                                    expect(menu).not_to_be_visible()
                                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                                evidence = os.environ.get('MASISCAM_UI_EVIDENCE_DIR')
                                if evidence:
                                    output = Path(evidence)
                                    output.mkdir(parents=True, exist_ok=True)
                                    page.screenshot(path=str(output / f'taller-{codigo.lower()}-{width}.png'), full_page=True)
                                action.click()
                                modal = page.locator('#tipoEquipo')
                                expect(modal).to_be_visible()
                                expect(modal.locator('.tw-type-option')).to_have_count(2)
                                expect(modal.locator('.modal-dialog')).to_have_css('transform', 'none')
                                page.keyboard.press('Escape')
                                expect(modal).not_to_be_visible()
                                action.click()
                                modal.locator('a[href$="tipo=BOMBA"]').click()
                                expect(page.locator('#id_ruc')).to_be_visible()
                                expect(page.locator('#cliente-crear')).to_have_count(0)
                                if evidence and codigo == 'TALLER':
                                    page.screenshot(path=str(output / f'taller-formulario-{width}.png'), full_page=True)
                            elif codigo in {'OFICINA', 'CLIENTE', 'CONSULTA'}:
                                # CLIENTE y CONSULTA son alias del rol OFICINA: administra catálogo (Planos y piezas), no expedientes.
                                labels = menu.locator('a').all_text_contents()
                                self.assertEqual(labels, ['Inicio', 'Bombas', 'Reductores', 'Clientes', 'Solicitudes de taller', 'Cotizaciones', 'Planos y piezas'])
                            else:
                                labels = menu.locator('a').all_text_contents()
                                self.assertEqual(labels[:5], ['Inicio', 'Bombas', 'Reductores', 'Clientes', 'Proyectos'])
                                self.assertIn('Planos y piezas', labels)
                                self.assertNotIn('Mis expedientes', labels)
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                            context.close()
            finally:
                browser.close()

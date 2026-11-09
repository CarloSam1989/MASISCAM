"""Optional real-browser regression against disposable QA settings only."""
import os
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

    def test_menu_y_panel_en_escritorio_tablet_y_movil(self):
        from playwright.sync_api import sync_playwright, expect

        equipo = self.crear('NAVEGADOR')
        exp = self.recepcion(equipo)
        cookies = {}
        for codigo, user in self.usuarios.items():
            client = Client()
            client.force_login(user)
            cookies[codigo] = client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for width in (1440, 768, 390):
                    for codigo, user in self.usuarios.items():
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
                            else:
                                expect(toggle).not_to_be_visible()
                            expect(menu).to_be_visible()
                            if codigo == 'TALLER':
                                self.assertTrue(page.url.endswith('/app/taller/'))
                                expect(menu.locator('a')).to_have_text(['Inicio',
                                    'Registrar equipo / recepción', 'Mis expedientes',
                                    'Estado del mantenimiento', 'Mi cuenta'])
                                expect(menu.get_by_role('button', name='Cerrar sesión')).to_be_visible()
                                expect(page.get_by_role('link', name='Registrar equipo', exact=True)).to_be_visible()
                                expect(page.locator('main')).to_contain_text(exp.numero)
                                expect(page.locator('main')).to_contain_text('Actividad pendiente:')
                                page.get_by_role('link', name='Registrar equipo', exact=True).click()
                                expect(page.locator('#id_ruc')).to_be_visible()
                                expect(page.locator('#cliente-crear')).to_have_count(0)
                            elif codigo == 'CLIENTE':
                                expect(menu.locator('a')).to_have_text(['Mis productos'])
                            else:
                                labels = menu.locator('a').all_text_contents()
                                self.assertEqual(labels[:5], ['Panel', 'Reductores', 'Bombas', 'Clientes', 'Proyectos'])
                                self.assertNotIn('Mis expedientes', labels)
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                            context.close()
            finally:
                browser.close()

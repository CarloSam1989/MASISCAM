import os
import sys
import threading
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
import django
django.setup()
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.test.utils import setup_test_environment
from django.test import Client, override_settings
from django.urls import reverse
from masiscam.test_registro_archivos import RegistroUploadTests
from masiscam.models import RegistroEquipo
call_command('migrate', verbosity=0, interactive=False)
setup_test_environment()
override_settings(GOOGLE_DRIVE_ENABLED=False).enable()
RegistroUploadTests.setUpTestData()
case = RegistroUploadTests()
case.client = Client()
case.setUp()
with override_settings(GOOGLE_DRIVE_ENABLED=True):
    case.crear([case.archivo('Informe_de_mantenimiento_con_un_nombre_extenso_para_verificar_el_ajuste_responsive_del_documento_2026.pdf'), case.archivo('Acta.pdf', b'%PDF-1.7 acta')])
RegistroEquipo.objects.create(equipo=case.equipo, tipo='GARANTIA', fecha='2026-09-26', drive_error='Error de Drive')
pages = {}
with override_settings(GOOGLE_DRIVE_ENABLED=False):
    for role, user in [('admin', case.admin), ('cliente', case.cliente_user)]:
        case.login_as(user)
        pages[f'/{role}.html'] = case.client.get(reverse('masiscam:ficha_detalle', args=[case.equipo.pk])).content
        pages[f'/{role}-informe.html'] = case.client.get(reverse('masiscam:equipo_informe', args=[case.equipo.pk])).content
    RegistroEquipo.objects.filter(equipo=case.equipo).update(drive_error='Error')
    pages['/cliente-vacio.html'] = case.client.get(reverse('masiscam:ficha_detalle', args=[case.equipo.pk])).content

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in pages:
            body, mime = pages[path], 'text/html; charset=utf-8'
        elif path.startswith('/static/'):
            filename = finders.find(path[len('/static/'):])
            if not filename:
                self.send_error(404)
                return
            import mimetypes
            body = Path(filename).read_bytes()
            mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
from playwright.sync_api import sync_playwright
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page = browser.new_page()
    base = f'http://127.0.0.1:{server.server_port}'
    for width in (1440, 768, 390, 320):
        page.set_viewport_size({'width': width, 'height': 1000})
        for role in ('admin', 'cliente'):
            page.goto(base + f'/{role}.html')
            page.wait_for_load_state('networkidle')
            table = page.locator('#historial-registros')
            assert table.count() == 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (role, width, 'overflow')
            assert page.locator('.registro-documentos a[download]').count() >= 1
            if role == 'cliente':
                assert page.locator('.registro-acciones').count() == 0
                assert table.get_by_text('Sin documentos', exact=True).count() == 0
            if width in (1440, 390):
                table.screenshot(path=str(Path(__file__).parent / f'{role}-{width}.png'))
            results.append({'role': role, 'width': width, 'overflow': False})
            if role == 'admin':
                page.get_by_role('button', name='Agregar registro', exact=True).click()
                modal = page.locator('#registro-modal')
                assert modal.is_visible()
                assert modal.locator('input[type=file][multiple]').count() == 1
                assert modal.locator('form').get_attribute('enctype') == 'multipart/form-data'
                assert modal.locator('select option').count() == 6
                modal.locator('input[type=file]').set_input_files([
                    {'name': 'uno.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.7 uno'},
                    {'name': 'dos.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.7 dos'},
                ])
                assert modal.locator('input[type=file]').evaluate('(el) => el.files.length') == 2
                assert modal.bounding_box()['x'] >= 0
                if width == 390:
                    modal.screenshot(path=str(Path(__file__).parent/'modal-390.png'))
                modal.get_by_role('button', name='Cancelar').click()
    for width in (1440, 390):
        page.set_viewport_size({'width': width, 'height': 1000})
        for role in ('admin', 'cliente'):
            page.goto(base + f'/{role}-informe.html')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.registro-acciones').count() == 0
            assert page.locator('.registro-documentos a[download]').count() >= 1
            results.append({'role': role + '-informe', 'width': width, 'overflow': False})
    page.goto(base + '/cliente-vacio.html')
    assert page.locator('#historial-registros').count() == 0
    browser.close()
server.shutdown()
case.doCleanups()
print(json.dumps(results))

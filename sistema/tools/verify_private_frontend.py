"""QA local opcional: requiere playwright y su Chromium, sin datos/Drive de produccion."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
# Playwright mantiene un loop local; esta QA secuencial usa una base desechable.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
from django.conf import settings
from playwright.sync_api import sync_playwright


class QuietHandler(WSGIRequestHandler):
    def finish(self):
        try:
            super().finish()
        finally:
            from django.db import connections
            connections.close_all()

    def log_message(self, *args):
        pass


def run():
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    resultados = []
    with tempfile.TemporaryDirectory(dir=output) as temporal:
        settings.GOOGLE_DRIVE_ENABLED = False
        settings.DATABASES["default"]["NAME"] = str(Path(temporal) / "qa.sqlite3")
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.wsgi import get_wsgi_application
        from accounts.models import Empresa, Perfil
        from masiscam.models import Cliente, RolMasiscam
        call_command("migrate", interactive=False, verbosity=0)
        user = get_user_model().objects.create_user(username="QA.MASISCAM", password="QA-only-password-123")
        empresa = Empresa.objects.create(nombre="Organizacion QA")
        perfil = Perfil.objects.create(user=user, empresa=empresa)
        RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.ADMINISTRADOR)
        Cliente.objects.create(empresa=empresa, nombre_comercial="LOS TIGRES", razon_social="EMPRESA CAMARONERA XYZ", ruc="0991234567001")
        servidor = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
        thread = threading.Thread(target=servidor.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{servidor.server_port}"
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Exception:
                    browser = pw.chromium.launch(channel="msedge", headless=True)
                from verify_private_ui import verify
                for ancho in (375, 768, 1366):
                    context = browser.new_context(viewport={"width": ancho, "height": 900})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "400 (Bad Request)" not in message.text else None)
                    page.on("response", lambda response: errors.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 and response.status != 400 else None)
                    verify(page, base, ancho, output)
                    assert not errors, errors
                    resultados.append({"width": ancho, "private_ui": "OK", "js_errors": errors})
                    context.close()
                browser.close()
        finally:
            servidor.shutdown()
            servidor.server_close()
            thread.join(timeout=5)
            from django.db import connections
            connections.close_all()
    (output / "private-browser-results.json").write_text(json.dumps(resultados, indent=2), encoding="utf-8")
    print(json.dumps(resultados, indent=2))


if __name__ == "__main__":
    run()

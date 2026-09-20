"""QA local opcional: requiere playwright y su Chromium, sin datos/Drive de produccion."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
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
        from masiscam.models import Cliente, Equipo, RolMasiscam
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
                for ancho in (375, 390, 768, 1366, 1920):
                    context = browser.new_context(viewport={"width": ancho, "height": {375:812,390:844,768:1024,1366:768,1920:1080}[ancho]})
                    context.add_init_script("window.print = () => {}")
                    page = context.new_page()
                    errors = []
                    bad_responses = []
                    page.on("response", lambda response: bad_responses.append({"url":response.url,"status":response.status}) if response.status >= 400 else None)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                    page.goto(base + "/", wait_until="networkidle")
                    assert page.locator(".brand img").evaluate("img => img.complete && img.naturalWidth > 0")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Web con scroll horizontal a {ancho}"
                    page.screenshot(path=str(output / f"web-{ancho}.png"), full_page=True)
                    page.goto(base + "/app/cuentas/login/")
                    assert page.locator(".login-brand img").evaluate("img => img.complete && img.naturalWidth > 0")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Login con scroll horizontal a {ancho}"
                    assert page.locator("#id_username").get_attribute("autocomplete") == "username"
                    assert page.locator("#id_password").get_attribute("autocomplete") == "current-password"
                    page.screenshot(path=str(output / f"login-{ancho}.png"), full_page=True)
                    page.fill("#id_username", "QA.MASISCAM")
                    page.fill("#id_password", "QA-only-password-123")
                    page.locator('button:has-text("Ingresar")').click()
                    page.wait_for_url(base + "/app/")
                    page.screenshot(path=str(output / f"app-{ancho}.png"), full_page=True)
                    page.goto(base + "/app/equipos/nuevo/")
                    page.fill("#id_ruc", "0991234567001")
                    page.locator("#cliente-resultados button").first.click()
                    for campo, valor in {"camaronera": "LOS TIGRES", "estacion": "E1", "sector": "S1", "numero_equipo": f"QA-{ancho}", "marca": "Marca", "modelo": "M1", "numero_serie": f"SER-{ancho}", "potencia": "20 HP", "ratio": "7:2", "tipo_aceite": "ISO VG 220"}.items():
                        page.fill("#id_" + campo, valor)
                    page.check("#id_consulta_publica_activa")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Formulario con scroll horizontal a {ancho}"
                    page.screenshot(path=str(output / f"form-{ancho}.png"), full_page=True)
                    with patch("masiscam.signals.encolar_carpeta_equipo"):
                        page.locator('button:has-text("Guardar reductor")').click()
                        page.wait_for_url("**/app/equipos/*/")
                    equipo = Equipo.objects.get(nombre=f"QA-{ancho}")
                    from django.db import connections
                    connections.close_all()
                    page.screenshot(path=str(output / f"ficha-{ancho}.png"), full_page=True)
                    page.get_by_role("link", name="Editar ficha").click()
                    assert page.input_value("#id_ratio") == "7:2"
                    assert page.input_value("#id_tipo_aceite") == "ISO VG 220"
                    page.fill("#id_ratio", "15:1")
                    page.fill("#id_camaronera", "LOS TIGRES 2")
                    page.locator('button:has-text("Guardar reductor")').click()
                    page.wait_for_url("**/app/equipos/*/")
                    with page.expect_popup() as popup:
                        page.get_by_role("link", name="Imprimir etiqueta").click()
                    etiqueta = popup.value
                    etiqueta.wait_for_load_state()
                    assert etiqueta.locator("dl").inner_text().find("15:1") >= 0
                    etiqueta.emulate_media(media="print")
                    dimensiones = etiqueta.locator(".etiqueta-equipo").bounding_box()
                    assert abs(dimensiones["width"] - 100 * 96 / 25.4) < 2
                    assert abs(dimensiones["height"] - 100 * 96 / 25.4) < 2, dimensiones
                    etiqueta.screenshot(path=str(output / f"etiqueta-{ancho}.png"), full_page=True)
                    page.goto(base + "/consulta/?code=" + equipo.token_publico)
                    page.wait_for_url("**/consulta/equipo/*/")
                    assert page.locator("main").inner_text().find("15:1") >= 0
                    for path, label in [("/consulta/", "consulta"), ("/app/clientes/", "clientes"), ("/app/proyectos/", "proyectos"), (f"/app/proyectos/{equipo.proyecto_id}/", "documentos"), (f"/app/proyectos/{equipo.proyecto_id}/historial/", "historial"), ("/terminos/", "terminos"), ("/privacidad/", "privacidad")]:
                        response = page.goto(base + path, wait_until="networkidle")
                        assert response.status == 200, (path, response.status)
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (label, ancho)
                        page.screenshot(path=str(output / f"{label}-{ancho}.png"), full_page=True)
                    assert not errors, errors
                    assert not bad_responses, bad_responses
                    resultados.append({"width": ancho, "web": "OK", "login": "OK", "crear_editar_precarga": "OK", "etiqueta_pestana_ratio_10x10": "OK", "consulta": "OK", "errores_js": errors})
                    context.close()
                browser.close()
        finally:
            servidor.shutdown()
            servidor.server_close()
            thread.join(timeout=5)
            from django.db import connections
            connections.close_all()
    (output / "browser-results.json").write_text(json.dumps(resultados, indent=2), encoding="utf-8")
    print(json.dumps(resultados, indent=2))


if __name__ == "__main__":
    run()

"""Acceptance/UI evidence, only a new in-memory database and synthetic media."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.recepcion_test_settings"
os.environ.pop("MASISCAM_TEST_POSTGRES", None)
import django
from django.conf import settings
settings.GOOGLE_DRIVE_ENABLED = False
settings.MASISCAM_RECEPCION_LOCAL = True
assert settings.DATABASES["default"]["NAME"] == ":memory:"
django.setup()
from django.core.management import call_command
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from masiscam.models import Auditoria, ConfirmacionRecepcion, RegistroEquipo
from masiscam.recepcion_services import abrir_recepcion
from masiscam.test_recepcion import argumentos, fixtures, foto
from masiscam.test_recepcion_confirmacion import placa_foto

out = ROOT / "artifacts" / "recepcion-qa" / "activacion-local"
out.mkdir(parents=True, exist_ok=True)
call_command("migrate", verbosity=0)
empresa, user, rol, cliente, equipo = fixtures()
token = equipo.token_publico
old = RegistroEquipo.objects.create(equipo=equipo, tipo="MANTENIMIENTO", fecha="2026-11-01", secuencia=1)
exp = abrir_recepcion(**argumentos(empresa, user, cliente, equipo, fotografias=[foto(), placa_foto()]))
general, placa = exp.recepcion.evidencias.order_by("pk")
client = Client()
client.force_login(user)
url = reverse("masiscam:expediente_detalle", args=[exp.pk])
pages = {"provisional": client.get(url).content.decode()}
response = client.post(reverse("masiscam:recepcion_finalizar", args=[exp.pk]),
    dict(clave=str(uuid.uuid4()), general_id=general.pk, placa_id=placa.pk, placa="CON_PLACA", motivo_sin_guia="QA ficticia: entrega sin guía."))
assert response.status_code == 302
result = ConfirmacionRecepcion.objects.get(recepcion=exp.recepcion)
pages["confirmada"] = client.get(url).content.decode()
assert "Recepción confirmada" in pages["confirmada"]
assert "Recepción confirmada" in client.get(reverse("masiscam:expedientes")).content.decode()
with result.guia.documento.archivo.open("rb") as stream:
    pdf = stream.read()
assert hashlib.sha256(pdf).hexdigest() == result.guia.hash_sha256
(out / "guia-confirmada-qa.pdf").write_bytes(pdf)
equipo.refresh_from_db()
old.refresh_from_db()
assert equipo.token_publico == token and old.secuencia == 1

def inline_assets(html):
    def css(match):
        name = match.group(1).removeprefix("/static/")
        path = finders.find(name)
        return "<style>" + Path(path).read_text(encoding="utf-8") + "</style>" if path else ""
    html = re.sub(r'<link[^>]*href="(/static/[^\"]+\.css)"[^>]*>', css, html)
    html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
    def img(match):
        path = finders.find(match.group(1).removeprefix("/static/"))
        return 'src="data:image/png;base64,' + base64.b64encode(Path(path).read_bytes()).decode() + '"' if path else match.group(0)
    return re.sub(r'src="(/static/[^\"]+)"', img, html)

from playwright.sync_api import sync_playwright
viewports = []
with sync_playwright() as playwright:
    if Path(playwright.chromium.executable_path).is_file():
        browser = playwright.chromium.launch(headless=True)
    else:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
    try:
        for state, html in pages.items():
            for label, width in (("escritorio", 1400), ("tablet", 768), ("movil", 390)):
                page = browser.new_page(viewport={"width": width, "height": 1000})
                page.route("**/*", lambda route: route.abort())
                page.set_content(inline_assets(html))
                metrics = page.locator("main").evaluate("e => ({width:e.clientWidth, scroll:e.scrollWidth})")
                assert metrics["scroll"] <= width, (state, label, metrics)
                page.screenshot(path=str(out / f"recepcion-{state}-{label}.png"), full_page=True)
                viewports.append(dict(state=state, viewport=label, **metrics))
                page.close()
    finally:
        browser.close()
report = dict(synthetic=True, database=":memory:", media_root=str(settings.MEDIA_ROOT),
    confirmed=True, numero=exp.numero, guia_version=result.guia.version, sha256=result.guia.hash_sha256,
    audit=Auditoria.objects.filter(expediente=exp, accion="RECEPCION_CONFIRMADA").count(),
    qr_unchanged=True, historical_record_unchanged=True, drive_real=False, viewports=viewports)
(out / "aceptacion-visual.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True, indent=2))

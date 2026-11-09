"""Synthetic migration backup/upgrade and PDF QA; NEVER opens project databases.

Run with Python 3.12 + requirements and optional pypdf/PyMuPDF QA tools.
All SQLite/media data live in a freshly created temporary directory.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.recepcion_test_settings"

from django.conf import settings

work = Path(tempfile.mkdtemp(prefix="masiscam-recepcion-synthetic-"))
settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(work / "qa.sqlite3")}}
settings.MEDIA_ROOT = work / "media"
settings.GOOGLE_DRIVE_ENABLED = False

import django
django.setup()

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

assert Path(connection.settings_dict["NAME"]).resolve().parent == work.resolve()
executor = MigrationExecutor(connection)
old_target = [("masiscam", "0014_orden_cronologico_registros")]
executor.migrate(old_target)
apps = executor.loader.project_state(old_target).apps
User = apps.get_model("auth", "User")
Empresa = apps.get_model("accounts", "Empresa")
Cliente = apps.get_model("masiscam", "Cliente")
Proyecto = apps.get_model("masiscam", "Proyecto")
Equipo = apps.get_model("masiscam", "Equipo")
Registro = apps.get_model("masiscam", "RegistroEquipo")
Documento = apps.get_model("masiscam", "Documento")
u = User.objects.create(username="qa-synthetic", password="!")
e = Empresa.objects.create(nombre="QA sintética")
c = Cliente.objects.create(empresa=e, nombre_comercial="QA", razon_social="QA", ruc="QA-ONLY")
p = Proyecto.objects.create(empresa=e, codigo="QA", nombre="QA", responsable="QA", cliente="QA", fecha_inicio="2026-11-05", creado_por=u)
equipo = Equipo.objects.create(proyecto=p, cliente=c, nombre="QA", numero_serie="SERIE-QA", token_publico="TOKEN-QA-NO-REAL", drive_folder_id="DRIVE-QA-NO-REAL")
r = Registro.objects.create(equipo=equipo, tipo="MANTENIMIENTO", fecha="2026-11-05", secuencia=1, drive_folder_id="REGISTRO-QA-NO-REAL")
d = Documento.objects.create(proyecto=p, equipo=equipo, categoria="INFORMES", titulo="QA", archivo="qa-existente.pdf", nombre_original="qa-existente.pdf", hash_sha256="a"*64, subido_por=u)

def inventory():
    with connection.cursor() as cursor:
        result = {}
        for table in ["masiscam_cliente", "masiscam_equipo", "masiscam_registroequipo", "masiscam_documento"]:
            cursor.execute(f"SELECT * FROM {table} ORDER BY id")
            result[table] = [list(map(str, row)) for row in cursor.fetchall()]
        return result

before = inventory()
with sqlite3.connect(work / "backup-0014.sqlite3") as backup:
    connection.connection.backup(backup)
executor = MigrationExecutor(connection)
executor.migrate([("masiscam", "0018_confirmacion_guardas")])
assert inventory() == before, "Historical fixture changed during additive migration"
with connection.cursor() as cursor:
    cursor.execute("SELECT COUNT(*) FROM masiscam_expedientemantenimiento")
    assert cursor.fetchone()[0] == 0, "Migration created synthetic expedientes unexpectedly"

from io import BytesIO
from PIL import Image
from masiscam.recepcion_pdf import construir_guia
from pypdf import PdfReader

b = BytesIO()
Image.new("RGB", (900, 600), "#315d87").save(b, "PNG")
snapshot = dict(numero="EXP-E1-00000001", version=1, empresa="MASISCAM QA", cliente="Cliente de prueba",
    ruc="QA-NO-REAL", equipo="Reductor de prueba", tipo_producto="REDUCTOR", marca="Marca QA", modelo="Modelo QA",
    serie="Serie QA", fecha="2026-11-05T10:30:00-05:00", responsable="Oficina QA", tipo="MANTENIMIENTO",
    condicion="Condición visible recibida.\n" * 25, observaciones="Observación de prueba con caracteres españoles: áéíóú ñ.\n" * 80,
    guia="GUÍA-QA", evidencias=[])
pdf = construir_guia(snapshot, [b.getvalue()])
out = ROOT / "artifacts" / "recepcion-qa"
out.mkdir(parents=True, exist_ok=True)
(out / "guia-muestra.pdf").write_bytes(pdf)
reader = PdfReader(BytesIO(pdf))
assert len(reader.pages) > 1
assert snapshot["numero"] in "\n".join(p.extract_text() for p in reader.pages)
assert any(page.images for page in reader.pages)
rendered = []
try:
    import pymupdf
    document = pymupdf.open(stream=pdf, filetype="pdf")
    for index, page in enumerate(document):
        name = f"guia-pagina-{index+1}.png"
        page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(str(out / name))
        rendered.append(name)
except ImportError:
    pass
result = dict(synthetic_database=str(work), backup=str(work / "backup-0014.sqlite3"),
    historical_inventory_unchanged=True, pages=len(reader.pages), pdf_sha256=hashlib.sha256(pdf).hexdigest(), rendered=rendered)
(out / "validacion-local.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
connection.close()

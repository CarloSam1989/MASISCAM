"""Two-phase PostgreSQL additive upgrade rehearsal on the disposable QA base.

Requires explicit MASISCAM_TEST_POSTGRES, config.recepcion_test_settings and the
exact database masiscam_qa_activacion. --seed creates synthetic 0016 rows only;
--upgrade compares every historical column and file after 0017/0018. Back up the
seeded disposable base between these phases. Never invoke against real data.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.recepcion_test_settings"
assert os.environ.get("MASISCAM_TEST_POSTGRES"), "Explicit QA URL required"
import django
from django.conf import settings
settings.GOOGLE_DRIVE_ENABLED = False
assert settings.DATABASES["default"]["NAME"] == "masiscam_qa_activacion"
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

out = ROOT / "artifacts" / "recepcion-qa" / "activacion-local"
out.mkdir(parents=True, exist_ok=True)
archivo = out / "documento-historico-sintetico.bin"
tables = ["accounts_empresa", "masiscam_cliente", "masiscam_equipo", "masiscam_registroequipo",
          "masiscam_documento", "masiscam_expedientemantenimiento", "masiscam_recepcionequipo",
          "masiscam_evidenciarecepcion", "masiscam_emisionguiaingreso", "masiscam_auditoria"]


def inventory():
    result = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"SELECT * FROM {table} ORDER BY id")
            result[table] = [list(map(str, row)) for row in cursor.fetchall()]
    return result


executor = MigrationExecutor(connection)
old = [("masiscam", "0016_recepcion_guardas")]
if "--seed" in sys.argv:
    assert not executor.loader.applied_migrations, "QA base must be empty before seeding"
    executor.migrate(old)
    apps = executor.loader.project_state(old).apps
    model = lambda name: apps.get_model("masiscam", name)
    user = apps.get_model("auth", "User").objects.create(username="QA-UPGRADE", password="!")
    empresa = apps.get_model("accounts", "Empresa").objects.create(nombre="QA UPGRADE FICTICIA")
    cliente = model("Cliente").objects.create(empresa=empresa, ruc="QA-UPGRADE", razon_social="QA", nombre_comercial="QA")
    proyecto = model("Proyecto").objects.create(empresa=empresa, codigo="QA", nombre="QA", responsable="QA", fecha_inicio="2026-11-06", creado_por=user)
    equipo = model("Equipo").objects.create(proyecto=proyecto, cliente=cliente, nombre="QA", token_publico="QR-QA-NO-REAL", drive_folder_id="DRIVE-QA-NO-REAL")
    registro = model("RegistroEquipo").objects.create(equipo=equipo, tipo="MANTENIMIENTO", fecha="2026-11-06", secuencia=1)
    exp = model("ExpedienteMantenimiento").objects.create(registro=registro, empresa=empresa, cliente_contractual=cliente,
        correlativo=1, numero="EXP-QA-UPGRADE-0001", huella_solicitud="a" * 64, creado_por=user)
    model("ContadorExpediente").objects.create(empresa=empresa, ultimo_numero=1)
    recepcion = model("RecepcionEquipo").objects.create(expediente=exp, fecha="2026-11-06T10:00:00-05:00", responsable=user,
        condicion_ingreso="QA histórica", snapshot={"numero": exp.numero, "pendiente_validacion": True})
    archivo.write_bytes(b"QA SYNTHETIC HISTORICAL FILE - NO REAL DATA")
    doc = model("Documento").objects.create(proyecto=proyecto, equipo=equipo, titulo="QA histórico", categoria="INFORMES",
        archivo="qa/" + archivo.name, nombre_original=archivo.name, tipo_mime="application/octet-stream", tamano=archivo.stat().st_size,
        hash_sha256=hashlib.sha256(archivo.read_bytes()).hexdigest(), subido_por=user, drive_file_id="FILE-QA-NO-REAL")
    model("EmisionGuiaIngreso").objects.create(expediente=exp, version=1, clave=uuid.uuid4(), snapshot=recepcion.snapshot,
        documento=doc, hash_sha256=doc.hash_sha256, estado="GENERADA", creado_por=user)
    before = dict(rows=inventory(), file_sha256=hashlib.sha256(archivo.read_bytes()).hexdigest())
    (out / "migracion-postgres-antes.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    print("Seed 0016 complete; back up this synthetic QA base before --upgrade.")
elif "--upgrade" in sys.argv:
    before = json.loads((out / "migracion-postgres-antes.json").read_text(encoding="utf-8"))
    executor.migrate([("masiscam", "0018_confirmacion_guardas")])
    assert inventory() == before["rows"], "Historical columns changed"
    assert hashlib.sha256(archivo.read_bytes()).hexdigest() == before["file_sha256"]
    with connection.cursor() as cursor:
        for table in ("masiscam_confirmacionrecepcion", "masiscam_fotorecepcionclasificada"):
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            assert cursor.fetchone()[0] == 0
    report = dict(postgresql=16, from_migration="0016", to_migration="0018", historical_columns_unchanged=True,
                  historical_file_unchanged=True, no_automatic_confirmation=True, drive_real=False)
    (out / "migracion-postgres-resultados.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
else:
    raise SystemExit("Use --seed or --upgrade")
connection.close()

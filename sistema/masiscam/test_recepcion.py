import hashlib
import importlib.util
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Empresa, Perfil
from .models import (Auditoria, Cliente, ContadorExpediente, Documento, Equipo,
    ExpedienteMantenimiento, Proyecto, RegistroEquipo, RolMasiscam)
from .recepcion_forms import RecepcionForm
from .recepcion_services import abrir_recepcion, generar_guia, reservar_guia


def fixtures():
    empresa = Empresa.objects.create(nombre="Empresa QA")
    user = get_user_model().objects.create_user(username="oficina-qa", password="QA-unicamente-123")
    perfil = Perfil.objects.create(user=user, empresa=empresa)
    rol = RolMasiscam.objects.create(perfil=perfil, rol="OFICINA")
    cliente = Cliente.objects.create(empresa=empresa, nombre_comercial="Cliente", razon_social="Cliente SA", ruc="099QA")
    proyecto = Proyecto.objects.create(empresa=empresa, codigo="QA", nombre="Camaronera", cliente="",
        responsable="QA", fecha_inicio=timezone.localdate(), creado_por=user)
    equipo = Equipo.objects.create(proyecto=proyecto, cliente=cliente, nombre="EQ1", marca="Marca",
        modelo="Modelo", numero_serie="SERIE", potencia_hp="10", ubicacion="E", sector="S")
    return empresa, user, rol, cliente, equipo


def argumentos(empresa, user, cliente, equipo, **extra):
    data = dict(usuario=user, empresa=empresa, cliente_id=cliente.pk, equipo_id=equipo.pk,
        clave=uuid.uuid4(), tipo="MANTENIMIENTO", fecha=timezone.now(), condicion_ingreso="Condición recibida", observaciones="Sin diagnóstico")
    data.update(extra)
    return data


def foto():
    b = BytesIO()
    Image.new("RGB", (40, 30), "blue").save(b, "PNG")
    return SimpleUploadedFile("foto.png", b.getvalue(), content_type="image/png")


@override_settings(GOOGLE_DRIVE_ENABLED=False)
class RecepcionTests(TestCase):
    def setUp(self):
        self.empresa, self.user, self.rol, self.cliente, self.equipo = fixtures()
        self.args = argumentos(self.empresa, self.user, self.cliente, self.equipo)
        self.client.force_login(self.user)

    def test_numeracion_idempotente_y_conflicto(self):
        exp = abrir_recepcion(**self.args)
        self.assertEqual(abrir_recepcion(**self.args).pk, exp.pk)
        self.assertEqual(exp.correlativo, 1)
        self.assertEqual(ContadorExpediente.objects.get().ultimo_numero, 1)
        with self.assertRaises(ValidationError):
            abrir_recepcion(**{**self.args, "observaciones": "otro payload"})
        otro = abrir_recepcion(**{**self.args, "clave": uuid.uuid4()})
        self.assertEqual(otro.correlativo, 2)
        self.assertEqual(RegistroEquipo.objects.count(), 2)

    def test_no_modifica_historia_ni_qr_ni_contacta_drive(self):
        viejo = RegistroEquipo.objects.create(equipo=self.equipo, tipo="MANTENIMIENTO", fecha=timezone.localdate(), secuencia=1)
        token = self.equipo.token_publico
        with patch("masiscam.services.GoogleDriveService") as drive:
            abrir_recepcion(**self.args)
            drive.assert_not_called()
        viejo.refresh_from_db()
        self.equipo.refresh_from_db()
        self.assertEqual(viejo.secuencia, 1)
        self.assertEqual(self.equipo.token_publico, token)

    def test_renumeracion_legacy_excluye_expedientes(self):
        from .registro_orden import renumerar
        viejo = RegistroEquipo.objects.create(equipo=self.equipo, tipo="MANTENIMIENTO", fecha=timezone.localdate(), secuencia=1)
        exp = abrir_recepcion(**self.args)
        renumerar(self.equipo.pk, "MANTENIMIENTO")
        viejo.refresh_from_db()
        exp.registro.refresh_from_db()
        self.assertEqual(viejo.secuencia, 1)
        self.assertIsNone(exp.registro.secuencia)

    def test_contadores_independientes_por_empresa(self):
        exp = abrir_recepcion(**self.args)
        empresa2 = Empresa.objects.create(nombre="Segunda")
        p = Perfil.objects.create(user=self.user, empresa=empresa2)
        RolMasiscam.objects.create(perfil=p, rol="OFICINA")
        cliente2 = Cliente.objects.create(empresa=empresa2, razon_social="Otra", nombre_comercial="Otra", ruc="MISMO-RUC")
        proyecto2 = Proyecto.objects.create(empresa=empresa2, codigo="P", nombre="P", responsable="R", fecha_inicio=timezone.localdate(), creado_por=self.user)
        equipo2 = Equipo.objects.create(proyecto=proyecto2, cliente=cliente2, nombre="E2")
        otro = abrir_recepcion(**argumentos(empresa2, self.user, cliente2, equipo2))
        self.assertEqual(otro.correlativo, exp.correlativo)
        self.assertNotEqual(otro.numero, exp.numero)

    def test_endpoint_post_recepcion_y_descarga_ajena(self):
        datos = dict(cliente=self.cliente.pk, equipo=self.equipo.pk, clave=uuid.uuid4(),
            fecha="2026-11-05T10:30", tipo="MANTENIMIENTO", condicion_ingreso="Recepción QA")
        r = self.client.post(reverse("masiscam:recepcion_crear"), datos)
        self.assertEqual(r.status_code, 302)
        exp = ExpedienteMantenimiento.objects.get()
        empresa2 = Empresa.objects.create(nombre="Otra")
        p = Perfil.objects.create(user=self.user, empresa=empresa2)
        RolMasiscam.objects.create(perfil=p, rol="OFICINA")
        session = self.client.session
        session["empresa_activa_id"] = empresa2.pk
        session.save()
        self.assertEqual(self.client.get(reverse("masiscam:expediente_detalle", args=[exp.pk])).status_code, 404)

    def test_fallo_pdf_conserva_emision_y_numero(self):
        from .recepcion_tasks import generar_guia_ingreso
        exp = abrir_recepcion(**self.args)
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        with patch("masiscam.recepcion_pdf.construir_guia", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                generar_guia_ingreso(guia.pk)
        guia.refresh_from_db()
        self.assertEqual(guia.estado, "ERROR")
        self.assertEqual(ExpedienteMantenimiento.objects.get().numero, exp.numero)

    def test_roles_sin_bypass_superusuario(self):
        for rol in ["TECNICO", "PLANTA", "GERENCIA", "ADMIN", "CONSULTA"]:
            self.rol.rol = rol
            self.rol.save()
            with self.assertRaises(PermissionDenied):
                abrir_recepcion(**self.args)
        self.user.is_superuser = True
        self.user.save()
        with self.assertRaises(PermissionDenied):
            abrir_recepcion(**self.args)
        self.assertEqual(ExpedienteMantenimiento.objects.count(), 0)

    def test_empresa_cliente_ajenos_e_inactivos(self):
        empresa2 = Empresa.objects.create(nombre="Otro")
        cliente2 = Cliente.objects.create(empresa=empresa2, nombre_comercial="Otro", razon_social="Otro", ruc="OTRO")
        with self.assertRaises(PermissionDenied):
            abrir_recepcion(**{**self.args, "cliente_id": cliente2.pk})
        self.rol.activo = False
        self.rol.save()
        with self.assertRaises(PermissionDenied):
            abrir_recepcion(**self.args)

    def test_rollback_no_consume_numero(self):
        with patch("masiscam.recepcion_services.evento", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                abrir_recepcion(**self.args)
        self.assertFalse(RegistroEquipo.objects.exists())
        self.assertEqual(abrir_recepcion(**self.args).correlativo, 1)

    def test_evidencia_persistente_y_validacion(self):
        exp = abrir_recepcion(**{**self.args, "fotografias": [foto()]})
        doc = exp.recepcion.evidencias.get().documento
        with doc.archivo.open("rb") as stream:
            self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), doc.hash_sha256)
        self.assertFalse(doc.publico)
        self.assertEqual(doc.equipo_id, self.equipo.pk)
        with self.assertRaises(ValidationError):
            abrir_recepcion(**{**self.args, "clave": uuid.uuid4(), "fotografias": [SimpleUploadedFile("a.png", b"fake", content_type="image/png")]})

    def test_form_cliente_equipo_y_campos_obligatorios(self):
        f = RecepcionForm(data={}, empresa=self.empresa)
        self.assertFalse(f.is_valid())
        for key in ["cliente", "equipo", "fecha", "condicion_ingreso"]:
            self.assertIn(key, f.errors)
        self.assertFalse(RecepcionForm(empresa=self.empresa).fields["guia_remision"].required)

    def test_guardas_orm_sql_y_cascada(self):
        exp = abrir_recepcion(**self.args)
        with self.assertRaises((ProtectedError, PermissionDenied)):
            exp.registro.delete()
        with self.assertRaises(PermissionDenied):
            exp.numero = "cambio"
            exp.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExpedienteMantenimiento.objects.filter(pk=exp.pk).update(numero="cambio")
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM masiscam_expedientemantenimiento WHERE id=%s", [exp.pk])
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContadorExpediente.objects.update(ultimo_numero=0)
        with self.assertRaises((ProtectedError, PermissionDenied)):
            self.equipo.delete()
        self.assertTrue(Auditoria.objects.filter(expediente_id=exp.pk).exists())

    def test_borrado_legacy_no_llama_drive(self):
        exp = abrir_recepcion(**self.args)
        self.rol.rol = "ADMIN"
        self.rol.save()
        with patch("masiscam.views.GoogleDriveService") as drive:
            r = self.client.post(reverse("masiscam:registro_eliminar", args=[self.equipo.pk, exp.registro_id]), {"confirmar": "eliminar"})
        self.assertEqual(r.status_code, 403)
        drive.assert_not_called()

    def test_rutas_get_sin_mutacion_y_csrf(self):
        self.assertEqual(self.client.get(reverse("masiscam:recepcion_crear")).status_code, 200)
        exp = abrir_recepcion(**self.args)
        with patch("masiscam.services.GoogleDriveService") as drive:
            self.assertEqual(self.client.get(reverse("masiscam:expediente_detalle", args=[exp.pk])).status_code, 200)
            drive.assert_not_called()
        self.assertEqual(self.client.get(reverse("masiscam:guia_emitir", args=[exp.pk])).status_code, 405)
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        self.assertEqual(c.post(reverse("masiscam:guia_emitir", args=[exp.pk]), {"clave": uuid.uuid4()}).status_code, 403)

    def test_guia_reserva_version_idempotente_worker_caido(self):
        exp = abrir_recepcion(**self.args)
        clave = uuid.uuid4()
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=clave)
        self.assertEqual(reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=clave).pk, guia.pk)
        with patch("masiscam.recepcion_tasks.generar_guia_ingreso.apply_async", side_effect=ConnectionError):
            r = self.client.post(reverse("masiscam:guia_emitir", args=[exp.pk]), {"clave": clave})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(exp.guias.count(), 1)
        self.assertTrue(guia.snapshot["pendiente_validacion"])

    def test_recuperacion_comando_solo_lectura_por_defecto(self):
        from django.core.management import call_command
        exp = abrir_recepcion(**self.args)
        reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        with patch("masiscam.recepcion_tasks.generar_guia_ingreso.apply_async") as cola:
            call_command("reintentar_guias_ingreso", empresa=self.empresa.pk, stdout=__import__("io").StringIO())
            cola.assert_not_called()

    def test_guia_documental_y_evidencia_rechazan_mime_falso(self):
        with self.assertRaises(ValidationError):
            abrir_recepcion(**{**self.args, "guia_remision": "G", "guia_archivo": SimpleUploadedFile("guia.pdf", b"fake", content_type="application/pdf")})
        self.assertFalse(ExpedienteMantenimiento.objects.exists())

    def test_no_autoasignacion_rol_operativo(self):
        self.rol.rol = "ADMIN"
        self.rol.save()
        r = self.client.post(reverse("masiscam:usuario_editar", args=[self.rol.pk]),
            {"username": self.user.username, "first_name": "Oficina", "email": "", "rol": "OFICINA", "cliente": ""})
        self.assertEqual(r.status_code, 200)
        self.rol.refresh_from_db()
        self.assertEqual(self.rol.rol, "ADMIN")

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_pdf_hash_snapshot_version_descarga_y_proteccion(self):
        exp = abrir_recepcion(**{**self.args, "fotografias": [foto()]})
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        doc_id = generar_guia(guia.pk)
        self.assertEqual(generar_guia(guia.pk), doc_id)
        guia.refresh_from_db()
        doc = guia.documento
        with doc.archivo.open("rb") as stream:
            contenido = stream.read()
        self.assertTrue(contenido.startswith(b"%PDF-"))
        self.assertEqual(hashlib.sha256(contenido).hexdigest(), guia.hash_sha256)
        if importlib.util.find_spec("pypdf"):
            from pypdf import PdfReader
            pdf = PdfReader(BytesIO(contenido))
            texto = "\n".join(page.extract_text() for page in pdf.pages)
            self.assertIn(exp.numero, texto)
            self.assertIn("PROVISIONAL", texto)
            self.assertIn("Condición de ingreso", texto)
            self.assertTrue(any(page.images for page in pdf.pages))
        self.equipo.nombre = "Nuevo nombre"
        self.equipo.save()
        self.assertEqual(guia.snapshot["equipo"], "EQ1")
        for fields in [dict(publico=True), dict(estado_sincronizacion="ARCHIVADO"), dict(archivo="otro.pdf")]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Documento.objects.filter(pk=doc.pk).update(**fields)
        url = reverse("masiscam:recepcion_documento", args=[exp.pk, doc.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        # Consume through Django's streaming wrapper. Calling close() directly
        # emits request_finished outside the wrapper and closes a PostgreSQL
        # TestCase transaction (SQLite in-memory masks that harness error).
        self.assertEqual(b"".join(r.streaming_content), contenido)
        self.assertEqual(self.client.get(reverse("masiscam:documento_privado", args=[doc.proyecto_id, doc.pk])).status_code, 403)
        # Sync metadata remains mutable, content and visibility do not.
        Documento.objects.filter(pk=doc.pk).update(estado_sincronizacion="SUBIENDO")
        guia2 = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk,
                              clave=uuid.uuid4(), nueva_version=True)
        doc2 = generar_guia(guia2.pk)
        self.assertNotEqual(doc_id, doc2)
        self.assertEqual(guia2.version, 2)
        with doc.archivo.open("rb") as stream:
            self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), guia.hash_sha256)
        from .drive_documents import EquipoDriveDocuments, DocumentUnavailable
        Documento.objects.filter(pk=doc.pk).update(drive_file_id="protegido-qa")
        visor = object.__new__(EquipoDriveDocuments)
        with self.assertRaises(DocumentUnavailable):
            visor.authorize("protegido-qa")
        self.rol.rol, self.rol.cliente = "CLIENTE", self.cliente
        self.rol.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_recargar_solicitud_no_reserva_otra_emision(self):
        exp = abrir_recepcion(**self.args)
        g1 = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        g2 = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        self.assertEqual(g1.pk, g2.pk)
        self.assertEqual(exp.guias.count(), 1)
        # Even explicit new-version intent reuses the existing pending job.
        g3 = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk,
                            clave=uuid.uuid4(), nueva_version=True)
        self.assertEqual(g1.pk, g3.pk)

    def test_borrador_permite_pendientes_y_confirmacion_local_desactivada(self):
        # Existing incomplete master data can be snapshotted without fabricating
        # technical values, while finalization remains unavailable.
        self.equipo.ratio, self.equipo.potencia_hp, self.equipo.numero_serie = "", "", ""
        self.equipo.save()
        exp = abrir_recepcion(**self.args)
        snapshot = exp.recepcion.snapshot
        self.assertEqual(snapshot["serie"], "")
        self.assertIn("serie", snapshot["datos_tecnicos_pendientes"])
        self.assertIn("potencia", snapshot["datos_tecnicos_pendientes"])
        form = RecepcionForm(empresa=self.empresa)
        self.assertFalse(form.fields["fotografias"].required)
        self.assertFalse(form.fields["guia_remision"].required)
        self.assertEqual(self.client.post(f"/app/expedientes/{exp.pk}/finalizar/").status_code, 404)

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_pdf_persistido_no_activa_entorno_local_y_post_repetido_no_muta(self):
        from .models import EmisionGuiaIngreso, EvidenciaRecepcion
        exp = abrir_recepcion(**{**self.args, "fotografias": [foto()]})
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        doc_id = generar_guia(guia.pk)
        guia.refresh_from_db()
        self.assertEqual(guia.estado, "GENERADA")
        self.assertTrue(guia.snapshot["pendiente_validacion"])
        counts = lambda: (ExpedienteMantenimiento.objects.count(), RegistroEquipo.objects.count(),
            Documento.objects.count(), EmisionGuiaIngreso.objects.count(),
            EvidenciaRecepcion.objects.count(), Auditoria.objects.count(),
            ContadorExpediente.objects.get(empresa=self.empresa).ultimo_numero)
        before = counts()
        qr = self.equipo.token_publico
        for rol in ("OFICINA", "ADMIN", "PLANTA", "GERENCIA", "TECNICO", "CONSULTA"):
            self.rol.rol = rol
            self.rol.save()
            for _ in range(2):
                response = self.client.post(f"/app/expedientes/{exp.pk}/finalizar/",
                    {"politicas_aprobadas": "1", "estado": "FINALIZADO", "documento": doc_id})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(counts(), before)
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.token_publico, qr)
        guia.refresh_from_db()
        self.assertEqual(guia.documento_id, doc_id)
        self.assertTrue(guia.snapshot["pendiente_validacion"])

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_fallo_almacenamiento_no_emite_y_reintento_no_duplica(self):
        from django.core.files.storage import FileSystemStorage
        from .recepcion_tasks import generar_guia_ingreso
        exp = abrir_recepcion(**self.args)
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        with patch.object(FileSystemStorage, "save", side_effect=OSError("QA disco no disponible")):
            with self.assertRaises(OSError):
                generar_guia_ingreso(guia.pk)
        guia.refresh_from_db()
        self.assertEqual(guia.estado, "ERROR")
        self.assertIsNone(guia.documento_id)
        self.assertFalse(Documento.objects.exists())
        self.assertFalse(Auditoria.objects.filter(expediente=exp, accion="GUIA_GENERADA").exists())
        doc = generar_guia_ingreso(guia.pk)
        self.assertEqual(generar_guia_ingreso(guia.pk), doc)
        self.assertEqual(exp.guias.count(), 1)
        self.assertEqual(ContadorExpediente.objects.get().ultimo_numero, 1)

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_storage_reporta_exito_sin_archivo_es_rechazado(self):
        from django.core.files.storage import FileSystemStorage
        from .recepcion_tasks import generar_guia_ingreso
        exp = abrir_recepcion(**self.args)
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        with patch.object(FileSystemStorage, "_save", side_effect=lambda name, content: name):
            with self.assertRaises(ValidationError):
                generar_guia_ingreso(guia.pk)
        guia.refresh_from_db()
        self.assertEqual(guia.estado, "ERROR")
        self.assertIsNone(guia.documento_id)
        self.assertFalse(Documento.objects.exists())

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_copia_corrupta_no_se_da_por_generada_ni_sincroniza(self):
        from .recepcion_tasks import generar_guia_ingreso
        exp = abrir_recepcion(**self.args)
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        doc = Documento.objects.get(pk=generar_guia(guia.pk))
        original = doc.hash_sha256
        # Corrupt only the new synthetic test file, never a project document.
        with doc.archivo.storage.open(doc.archivo.name, "wb") as stream:
            stream.write(b"QA corrupted copy")
        with override_settings(GOOGLE_DRIVE_ENABLED=True), patch("masiscam.services.encolar_drive") as cola:
            with self.assertRaises(ValidationError):
                generar_guia_ingreso(guia.pk)
            cola.assert_not_called()
        guia.refresh_from_db()
        self.assertEqual(guia.hash_sha256, original)
        self.assertEqual(exp.guias.count(), 1)
        self.assertEqual(self.client.get(reverse("masiscam:recepcion_documento", args=[exp.pk, doc.pk])).status_code, 503)

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_plantilla_a4_firmas_y_compatibilidad_v1(self):
        from .recepcion_pdf import construir_guia
        exp = abrir_recepcion(**self.args)
        guia = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        self.assertEqual(guia.snapshot["plantilla"], "recepcion-v2")
        old = {**guia.snapshot, "plantilla": "recepcion-v1"}
        self.assertTrue(construir_guia(old).startswith(b"%PDF-"))
        if importlib.util.find_spec("pypdf"):
            from pypdf import PdfReader
            pdf = PdfReader(BytesIO(construir_guia({**guia.snapshot, "demostrativo": True})))
            text = "\n".join(p.extract_text() for p in pdf.pages)
            self.assertIn("DATOS FICTICIOS", text)
            self.assertIn("ENTREGA", text)
            self.assertIn("RECEPCIÓN", text)
            self.assertIn("Firma:", text)
            for page in pdf.pages:
                self.assertAlmostEqual(float(page.mediabox.width), 595.276, places=2)
                self.assertAlmostEqual(float(page.mediabox.height), 841.89, places=2)


@skipUnless(connection.vendor == "postgresql", "Requiere PostgreSQL real aislado; SQLite no prueba locks")
@override_settings(GOOGLE_DRIVE_ENABLED=False)
class RecepcionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.empresa, self.user, self.rol, self.cliente, self.equipo = fixtures()

    def test_conexiones_independientes_misma_empresa_y_doble_envio(self):
        from threading import Barrier
        for misma_clave in [False, True]:
            barrier = Barrier(2)
            clave, fecha = uuid.uuid4(), timezone.now()
            def crear(_):
                connections.close_all()
                try:
                    user = get_user_model().objects.get(pk=self.user.pk)
                    empresa = Empresa.objects.get(pk=self.empresa.pk)
                    barrier.wait(timeout=15)
                    return abrir_recepcion(**argumentos(empresa, user, self.cliente, self.equipo,
                        clave=clave if misma_clave else uuid.uuid4(), fecha=fecha)).pk
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                ids = list(pool.map(crear, range(2)))
            self.assertEqual(len(set(ids)), 1 if misma_clave else 2)
        self.assertEqual(ExpedienteMantenimiento.objects.count(), 3)

    def test_empresas_distintas_no_comparten_correlativo(self):
        from threading import Barrier
        empresa2 = Empresa.objects.create(nombre="QA segunda empresa")
        perfil = Perfil.objects.create(user=self.user, empresa=empresa2)
        RolMasiscam.objects.create(perfil=perfil, rol="OFICINA")
        cliente2 = Cliente.objects.create(empresa=empresa2, ruc="QA2", razon_social="QA2", nombre_comercial="QA2")
        proyecto2 = Proyecto.objects.create(empresa=empresa2, codigo="QA2", nombre="QA2", responsable="QA", cliente="QA2", fecha_inicio=timezone.localdate(), creado_por=self.user)
        equipo2 = Equipo.objects.create(proyecto=proyecto2, cliente=cliente2, nombre="QA2")
        barrier = Barrier(2)
        def crear(item):
            empresa_id, cliente_id, equipo_id = item
            connections.close_all()
            try:
                empresa = Empresa.objects.get(pk=empresa_id)
                user = get_user_model().objects.get(pk=self.user.pk)
                barrier.wait(timeout=15)
                exp = abrir_recepcion(usuario=user, empresa=empresa, equipo_id=equipo_id,
                    cliente_id=cliente_id, clave=uuid.uuid4(), tipo="MANTENIMIENTO",
                    fecha=timezone.now(), condicion_ingreso="QA")
                return exp.correlativo, exp.numero
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(crear, [(self.empresa.pk, self.cliente.pk, self.equipo.pk),
                (empresa2.pk, cliente2.pk, equipo2.pk)]))
        self.assertEqual([r[0] for r in results], [1, 1])
        self.assertNotEqual(results[0][1], results[1][1])

    @skipUnless(importlib.util.find_spec("reportlab"), "ReportLab aún no instalado")
    def test_emision_y_generacion_concurrente_no_duplican(self):
        from threading import Barrier
        exp = abrir_recepcion(**argumentos(self.empresa, self.user, self.cliente, self.equipo))
        barrier = Barrier(2)
        def reservar(_):
            connections.close_all()
            try:
                usuario = get_user_model().objects.get(pk=self.user.pk)
                empresa = Empresa.objects.get(pk=self.empresa.pk)
                barrier.wait(timeout=20)
                return reservar_guia(usuario=usuario, empresa=empresa, expediente_id=exp.pk, clave=uuid.uuid4()).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(reservar, range(2)))
        self.assertEqual(len(set(ids)), 1)
        barrier = Barrier(2)
        def generar(_):
            connections.close_all()
            try:
                barrier.wait(timeout=20)
                return generar_guia(ids[0])
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            documentos = list(pool.map(generar, range(2)))
        self.assertEqual(len(set(documentos)), 1)
        self.assertEqual(exp.guias.count(), 1)
        self.assertEqual(Documento.objects.count(), 1)
        self.assertEqual(Auditoria.objects.filter(expediente=exp, accion="GUIA_GENERADA").count(), 1)
        self.assertEqual(ContadorExpediente.objects.get(empresa=self.empresa).ultimo_numero, 1)

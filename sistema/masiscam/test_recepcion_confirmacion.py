import hashlib
import importlib.util
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import Empresa
from .models import (Auditoria, ConfirmacionRecepcion, ContadorExpediente, Documento,
    EmisionGuiaIngreso, EvidenciaRecepcion, FotoRecepcionClasificada)
from .recepcion_confirmacion import confirmar_recepcion
from .recepcion_services import abrir_recepcion, generar_guia, reservar_guia
from .test_recepcion import argumentos, fixtures, foto


def placa_foto():
    stream = BytesIO()
    Image.new("RGB", (60, 40), "orange").save(stream, "PNG")
    return SimpleUploadedFile("placa.png", stream.getvalue(), content_type="image/png")


@skipUnless(importlib.util.find_spec("reportlab"), "Requiere ReportLab")
@override_settings(MASISCAM_RECEPCION_LOCAL=True, GOOGLE_DRIVE_ENABLED=False)
class ConfirmacionTests(TestCase):
    def setUp(self):
        self.empresa, self.user, self.rol, self.cliente, self.equipo = fixtures()
        self.exp = abrir_recepcion(**argumentos(self.empresa, self.user, self.cliente, self.equipo,
            fotografias=[foto(), placa_foto()]))
        self.general, self.placa = self.exp.recepcion.evidencias.order_by("pk")
        self.args = dict(usuario=self.user, empresa=self.empresa, expediente_id=self.exp.pk,
            clave=uuid.uuid4(), placa="CON_PLACA", general_id=self.general.pk,
            placa_id=self.placa.pk, motivo_sin_guia="Cliente entregó el equipo sin guía.")
        self.client.force_login(self.user)

    def test_recepcion_completa_persistencia_snapshot_auditoria(self):
        token = self.equipo.token_publico
        result = confirmar_recepcion(**self.args)
        self.assertEqual(result.guia.estado, "GENERADA")
        self.assertEqual(result.guia.version, 1)
        self.assertFalse(result.guia.snapshot["pendiente_validacion"])
        self.assertEqual(result.responsable, self.user)
        with result.guia.documento.archivo.open("rb") as stream:
            data = stream.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertEqual(hashlib.sha256(data).hexdigest(), result.guia.hash_sha256)
        self.assertEqual(FotoRecepcionClasificada.objects.count(), 2)
        self.assertEqual(Auditoria.objects.filter(accion="RECEPCION_CONFIRMADA").count(), 1)
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.token_publico, token)
        if importlib.util.find_spec("pypdf"):
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
            self.assertIn("RECEPCIÓN CONFIRMADA", text)
            self.assertNotIn("CONSTANCIA PROVISIONAL", text)
            self.assertIn(self.args["motivo_sin_guia"], text)

    def test_sin_general_bloquea(self):
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "general_id": None})
        self.assertFalse(ConfirmacionRecepcion.objects.exists())
        self.assertFalse(self.exp.guias.exists())

    def test_con_placa_sin_foto_bloquea(self):
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "placa_id": None})
        self.assertFalse(FotoRecepcionClasificada.objects.exists())

    def test_sin_placa_requiere_justificacion(self):
        args = {**self.args, "placa": "SIN_PLACA", "placa_id": None}
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**args)
        result = confirmar_recepcion(**{**args, "motivo_sin_placa": "Equipo recibido sin placa física."})
        self.assertEqual(result.placa, "SIN_PLACA")
        self.assertEqual(FotoRecepcionClasificada.objects.count(), 1)

    def test_ausencia_guia_exige_motivo(self):
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "motivo_sin_guia": "   "})
        self.assertFalse(ConfirmacionRecepcion.objects.exists())

    def test_guia_existente_no_requiere_excepcion(self):
        exp = abrir_recepcion(**argumentos(self.empresa, self.user, self.cliente, self.equipo, guia_remision="G-QA"))
        result = confirmar_recepcion(usuario=self.user, empresa=self.empresa, expediente_id=exp.pk,
            clave=uuid.uuid4(), placa="CON_PLACA", foto_general=SimpleUploadedFile("general2.png", self._image("green"), content_type="image/png"),
            foto_placa=SimpleUploadedFile("placa2.png", self._image("red"), content_type="image/png"))
        self.assertEqual(result.guia.snapshot["guia"], "G-QA")

    def _image(self, color):
        stream = BytesIO()
        Image.new("RGB", (50, 35), color).save(stream, "PNG")
        return stream.getvalue()

    def test_validacion_foto_y_evidencia_ajena(self):
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "general_id": None,
                "foto_general": SimpleUploadedFile("fake.png", b"fake", content_type="image/png")})
        exp = abrir_recepcion(**argumentos(self.empresa, self.user, self.cliente, self.equipo))
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "expediente_id": exp.pk})
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "placa_id": self.general.pk})

    def test_idempotencia_conflicto_y_numeracion(self):
        first = confirmar_recepcion(**self.args)
        self.assertEqual(confirmar_recepcion(**self.args).pk, first.pk)
        self.assertEqual(confirmar_recepcion(**{**self.args, "clave": uuid.uuid4()}).pk, first.pk)
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "motivo_sin_guia": "Otro motivo"})
        self.assertEqual(self.exp.guias.count(), 1)
        self.assertEqual(ContadorExpediente.objects.get().ultimo_numero, 1)
        self.assertEqual(Auditoria.objects.filter(accion="RECEPCION_CONFIRMADA").count(), 1)

    def test_fallo_storage_rollback_y_reintento(self):
        before = Documento.objects.count()
        with patch.object(FileSystemStorage, "save", side_effect=OSError("QA almacenamiento caído")):
            with self.assertRaises(OSError):
                confirmar_recepcion(**self.args)
        self.assertFalse(ConfirmacionRecepcion.objects.exists())
        self.assertFalse(self.exp.guias.exists())
        self.assertEqual(Documento.objects.count(), before)
        self.assertFalse(FotoRecepcionClasificada.objects.exists())
        self.assertFalse(Auditoria.objects.filter(accion="RECEPCION_CONFIRMADA").exists())
        confirmar_recepcion(**self.args)
        self.assertEqual(self.exp.guias.count(), 1)

    def test_storage_falso_exito_no_confirma(self):
        with patch.object(FileSystemStorage, "_save", side_effect=lambda name, content: name):
            with self.assertRaises(ValidationError):
                confirmar_recepcion(**self.args)
        self.assertFalse(ConfirmacionRecepcion.objects.exists())

    def test_version_preserva_pdf_anterior(self):
        provisional = reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=self.exp.pk, clave=uuid.uuid4())
        old = Documento.objects.get(pk=generar_guia(provisional.pk))
        result = confirmar_recepcion(**self.args)
        self.assertEqual(result.guia.version, 2)
        provisional.refresh_from_db()
        self.assertTrue(provisional.snapshot["pendiente_validacion"])
        with old.archivo.open("rb") as stream:
            self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), old.hash_sha256)
        self.assertEqual(reservar_guia(usuario=self.user, empresa=self.empresa, expediente_id=self.exp.pk,
            clave=uuid.uuid4(), nueva_version=True).pk, result.guia.pk)

    def test_roles_y_empresa_sin_bypass(self):
        for rol in ("ADMIN", "TECNICO", "PLANTA", "GERENCIA", "CONSULTA"):
            self.rol.rol = rol
            self.rol.save()
            with self.assertRaises(PermissionDenied):
                confirmar_recepcion(**self.args)
        self.user.is_superuser = True
        self.user.save()
        with self.assertRaises(PermissionDenied):
            confirmar_recepcion(**self.args)
        self.rol.rol = "OFICINA"
        self.rol.save()
        empresa2 = Empresa.objects.create(nombre="QA ajena")
        with self.assertRaises(PermissionDenied):
            confirmar_recepcion(**{**self.args, "empresa": empresa2})
        self.assertFalse(ConfirmacionRecepcion.objects.exists())

    def test_post_manipulado_csrf_y_estado_servidor(self):
        url = reverse("masiscam:recepcion_finalizar", args=[self.exp.pk])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url, {}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"estado": "FINALIZADO", "politicas_aprobadas": "1"}).status_code, 400)
        data = {k: v for k, v in self.args.items() if k not in {"usuario", "empresa", "expediente_id"}}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("masiscam:expediente_detalle", args=[self.exp.pk]))
        self.assertContains(response, "Recepción confirmada")
        self.assertNotContains(response, "Validar, generar guía y confirmar recepción")

    def test_guardas_sql_y_documento_confirmado(self):
        result = confirmar_recepcion(**self.args)
        for model, fields in ((ConfirmacionRecepcion, {"motivo_sin_guia": "alterado"}),
                              (FotoRecepcionClasificada, {"clase": "PLACA"})):
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.all().update(**fields)
        with self.assertRaises(PermissionDenied), transaction.atomic():
            result.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EvidenciaRecepcion.objects.create(recepcion=self.exp.recepcion, documento=Documento.objects.create(
                proyecto=self.equipo.proyecto, equipo=self.equipo, titulo="QA", subido_por=self.user), tipo="FOTO")

    def test_clave_no_se_reutiliza_entre_expedientes(self):
        exp = abrir_recepcion(**argumentos(self.empresa, self.user, self.cliente, self.equipo))
        confirmar_recepcion(**self.args)
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**{**self.args, "expediente_id": exp.pk})
        self.assertFalse(exp.guias.exists())

    def test_fallo_storage_endpoint_mantiene_estado_provisional(self):
        url = reverse("masiscam:recepcion_finalizar", args=[self.exp.pk])
        data = {k: v for k, v in self.args.items() if k not in {"usuario", "empresa", "expediente_id"}}
        with patch.object(FileSystemStorage, "save", side_effect=OSError("QA disco caído")):
            response = self.client.post(url, data)
        self.assertContains(response, "Recepción sin finalizar", status_code=400)
        self.assertNotContains(response, "Recepción confirmada", status_code=400)
        self.assertFalse(ConfirmacionRecepcion.objects.exists())

    def test_foto_corrupta_bloquea_confirmacion(self):
        doc = self.general.documento
        with doc.archivo.storage.open(doc.archivo.name, "wb") as stream:
            stream.write(b"QA foto corrupta")
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**self.args)
        self.assertFalse(ConfirmacionRecepcion.objects.exists())
        self.assertFalse(self.exp.guias.exists())

    def test_no_se_confirma_con_documento_de_otro_expediente(self):
        result = confirmar_recepcion(**self.args)
        exp = abrir_recepcion(**argumentos(self.empresa, self.user, self.cliente, self.equipo))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfirmacionRecepcion.objects.create(recepcion=exp.recepcion, guia=result.guia, clave=uuid.uuid4(),
                huella="a" * 64, placa="SIN_PLACA", motivo_sin_placa="QA", motivo_sin_guia="QA",
                politica=result.politica, responsable=self.user, confirmado_en=result.confirmado_en)

    def test_datos_incompletos_y_documento_corrupto(self):
        self.assertEqual(self.exp.recepcion.snapshot["ratio"], "")
        result = confirmar_recepcion(**self.args)
        self.assertEqual(result.guia.snapshot["ratio"], "")
        with result.guia.documento.archivo.storage.open(result.guia.documento.archivo.name, "wb") as stream:
            stream.write(b"QA corrupto")
        with self.assertRaises(ValidationError):
            confirmar_recepcion(**self.args)
        self.assertEqual(ConfirmacionRecepcion.objects.count(), 1)
        self.assertEqual(self.exp.guias.count(), 1)

    def test_drive_caido_no_revierte_confirmacion(self):
        with self.captureOnCommitCallbacks(execute=True), patch("masiscam.recepcion_tasks.generar_guia_ingreso", side_effect=OSError("QA broker caído")):
            result = confirmar_recepcion(**self.args)
        self.assertTrue(ConfirmacionRecepcion.objects.filter(pk=result.pk).exists())
        self.assertTrue(result.guia.documento.archivo.storage.exists(result.guia.documento.archivo.name))

    @override_settings(MASISCAM_RECEPCION_LOCAL=False)
    def test_desactivado_por_defecto(self):
        with self.assertRaises(PermissionDenied):
            confirmar_recepcion(**self.args)
        self.assertEqual(self.client.post(reverse("masiscam:recepcion_finalizar", args=[self.exp.pk]), {}).status_code, 404)


@skipUnless(connection.vendor == "postgresql" and importlib.util.find_spec("reportlab"), "Requiere PostgreSQL real y ReportLab")
@override_settings(MASISCAM_RECEPCION_LOCAL=True, GOOGLE_DRIVE_ENABLED=False)
class ConfirmacionConcurrencyTests(TransactionTestCase):
    def test_confirmar_simultaneo_misma_y_distinta_clave(self):
        empresa, user, rol, cliente, equipo = fixtures()
        for misma_clave in (True, False):
            exp = abrir_recepcion(**argumentos(empresa, user, cliente, equipo))
            # Different image bytes per reception to respect existing deduplication.
            stream = BytesIO()
            Image.new("RGB", (40 + exp.pk, 30), "blue").save(stream, "PNG")
            contenido = stream.getvalue()
            barrier, clave = Barrier(2), uuid.uuid4()
            def confirmar(_):
                connections.close_all()
                try:
                    barrier.wait(timeout=20)
                    return confirmar_recepcion(usuario=get_user_model().objects.get(pk=user.pk),
                        empresa=Empresa.objects.get(pk=empresa.pk), expediente_id=exp.pk,
                        clave=clave if misma_clave else uuid.uuid4(), placa="SIN_PLACA", motivo_sin_placa="Sin placa física",
                        motivo_sin_guia="Sin guía", foto_general=SimpleUploadedFile("general.png", contenido, content_type="image/png")).pk
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                ids = list(pool.map(confirmar, range(2)))
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(exp.guias.count(), 1)
            self.assertEqual(Auditoria.objects.filter(expediente=exp, accion="RECEPCION_CONFIRMADA").count(), 1)

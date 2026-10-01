import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, models, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from googleapiclient.errors import HttpError
from httplib2 import Response

from . import test_registros
from .models import ArchivoRegistro, RegistroEquipo
from .tasks import revisar_archivos_registro


@override_settings(GOOGLE_DRIVE_ENABLED=True, GOOGLE_DRIVE_ROOT_FOLDER_ID="configured-root")
class RegistroUploadTests(TestCase):
    setUpTestData = classmethod(test_registros.RegistroActionsTests.setUpTestData.__func__)
    login_as = test_registros.RegistroActionsTests.login_as

    def setUp(self):
        test_registros.RegistroActionsTests.setUp(self)
        self.reservados = 0
        self.contenidos = {}
        self.fallar = None
        self.perder_respuesta = False
        for node in self.nodes.values():
            node.setdefault("name", node["id"])
        def get(fileId, **kwargs):
            def execute():
                if fileId not in self.nodes:
                    raise HttpError(Response({"status": "404"}), b"missing")
                return self.nodes[fileId].copy()
            return MagicMock(execute=execute)
        def reserve(**kwargs):
            self.reservados += 1
            return MagicMock(execute=lambda: {"ids": [f"upload-{self.reservados}"]})
        def create(body, media_body=None, **kwargs):
            def execute(**options):
                if media_body and body["name"] == self.fallar and not self.perder_respuesta:
                    raise TimeoutError("private provider details")
                id = body.get("id", f"folder-{len(self.nodes)}")
                if id in self.nodes:
                    raise HttpError(Response({"status": "409"}), b"already exists")
                node = dict(body, id=id)
                if media_body:
                    content = media_body.getbytes(0, media_body.size())
                    self.contenidos[id] = content
                    node.update(size=str(len(content)), mimeType=media_body.mimetype(), capabilities={"canDownload": True})
                self.nodes[id] = node
                if media_body and body["name"] == self.fallar and self.perder_respuesta:
                    raise TimeoutError("response lost after successful upload")
                return node.copy()
            return MagicMock(execute=execute)
        self.api.files().get.side_effect = get
        self.api.files().generateIds.side_effect = reserve
        self.api.files().create.side_effect = create
        for path in ("masiscam.registro_archivos.GoogleDriveService", "masiscam.services.GoogleDriveService"):
            patcher = patch(path, return_value=self.service)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("masiscam.tasks.revisar_archivos_registro.apply_async")
        self.queue = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def archivo(nombre="uno.pdf", contenido=b"%PDF-1.7 uno", mime="application/pdf"):
        return SimpleUploadedFile(nombre, contenido, content_type=mime)

    def crear(self, archivos=(), clave=None, **extra):
        data = {"tipo": "MANTENIMIENTO", "fecha": "2026-09-21", "clave_creacion": clave or str(uuid.uuid4()), "archivos": list(archivos)}
        data.update(extra)
        return self.client.post(reverse("masiscam:registro_crear", args=[self.equipo.pk]), data)

    def ultimo(self):
        return RegistroEquipo.objects.order_by("-pk").first()

    def retry_url(self, registro):
        return reverse("masiscam:registro_reintentar", args=[self.equipo.pk, registro.pk])

    def test_multiple_upload_only_drive_and_metadata_no_permanent_local_copy(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)/"media",
                FILE_UPLOAD_TEMP_DIR=directory, FILE_UPLOAD_MAX_MEMORY_SIZE=1), patch("django.core.files.storage.FileSystemStorage.save") as save:
            response = self.crear([self.archivo(), self.archivo("dos.pdf", b"%PDF-1.7 dos")])
            self.assertEqual(response.status_code, 302)
            response.close()
            self.assertEqual(list(Path(directory).rglob("*")), [])
            save.assert_not_called()
        registro = self.ultimo()
        self.assertEqual(registro.archivos.count(), 2)
        self.assertFalse(any(isinstance(f, models.FileField) for f in ArchivoRegistro._meta.fields))
        self.assertEqual(set(self.contenidos.values()), {b"%PDF-1.7 uno", b"%PDF-1.7 dos"})
        self.assertEqual(self.nodes[registro.drive_folder_id]["name"], "2026-09-21 - Mantenimiento 001")
        for archivo in registro.archivos.all():
            self.assertEqual(archivo.estado, "DISPONIBLE")
            self.assertEqual(self.nodes[archivo.drive_file_id]["parents"], [registro.drive_folder_id])

    def test_duplicate_post_and_identical_files_do_not_duplicate_records_or_files(self):
        clave = str(uuid.uuid4())
        for _ in range(2):
            self.assertEqual(self.crear([self.archivo(), self.archivo()], clave=clave).status_code, 302)
        self.assertEqual(RegistroEquipo.objects.filter(clave_creacion=clave).count(), 1)
        self.assertEqual(self.ultimo().archivos.count(), 1)
        self.assertEqual(len(self.contenidos), 1)
        self.assertEqual(self.reservados, 1)

    def test_optional_files_and_sequence_across_dates_types_and_legacy_folders(self):
        self.nodes["legacy"] = dict(id="legacy", name="Mantenimiento 009", mimeType="application/vnd.google-apps.folder", parents=["equipment-root"])
        for tipo, fecha, expected in (("MANTENIMIENTO", "2026-09-21", 10), ("MANTENIMIENTO", "2026-09-23", 11), ("NUEVO", "2026-09-23", 1)):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.crear(tipo=tipo, fecha=fecha).status_code, 302)
            registro = self.ultimo()
            self.assertEqual(registro.secuencia, expected)
            self.assertEqual(self.nodes[registro.drive_folder_id]["name"], f"{fecha} - {registro.get_tipo_display()} {expected:03d}")
            self.assertEqual(str(registro.fecha), fecha)
        self.assertEqual(self.nodes["legacy"]["name"], "Mantenimiento 009")

    def test_partial_error_reselection_uses_same_reserved_id(self):
        self.fallar = "dos.pdf"
        self.crear([self.archivo(), self.archivo("dos.pdf", b"%PDF-1.7 dos")])
        registro = self.ultimo()
        pendiente = registro.archivos.get(nombre="dos.pdf")
        reserved = pendiente.drive_file_id
        self.assertEqual(pendiente.estado, "ERROR")
        self.queue.assert_called_once_with(args=[registro.pk], retry=False)
        self.assertContains(self.client.get(self.retry_url(registro)), "dos.pdf")
        self.fallar = None
        response = self.client.post(self.retry_url(registro), {"archivos": [self.archivo(), self.archivo("dos.pdf", b"%PDF-1.7 dos")]})
        self.assertEqual(response.status_code, 302)
        pendiente.refresh_from_db()
        self.assertEqual(pendiente.drive_file_id, reserved)
        self.assertEqual(pendiente.estado, "DISPONIBLE")
        self.assertEqual(registro.archivos.count(), 2)
        self.assertEqual(len(self.contenidos), 2)
        self.assertEqual(self.reservados, 2)

    def test_lost_response_celery_reconciles_without_bytes_or_duplicates(self):
        self.fallar, self.perder_respuesta = "uno.pdf", True
        self.crear([self.archivo()])
        registro = self.ultimo()
        self.assertEqual(registro.archivos.get().estado, "ERROR")
        count = self.api.files().create.call_count
        revisar_archivos_registro.run(registro.pk)
        self.assertEqual(registro.archivos.get().estado, "DISPONIBLE")
        self.assertEqual(self.api.files().create.call_count, count)
        self.assertEqual(len(self.contenidos), 1)

    def test_failed_before_transmission_requires_reselection_without_discarding_metadata(self):
        self.fallar = "uno.pdf"
        self.crear([self.archivo()])
        registro = self.ultimo()
        reserved = registro.archivos.get().drive_file_id
        revisar_archivos_registro.run(registro.pk)
        self.assertEqual(registro.archivos.get().drive_file_id, reserved)
        self.assertEqual(registro.archivos.get().estado, "ERROR")
        self.assertEqual(self.contenidos, {})
        self.assertContains(self.client.get(self.retry_url(registro)), "Vuelva a seleccionar")

    def test_validation_of_every_file_before_creating_record(self):
        total = RegistroEquipo.objects.count()
        for files in ([self.archivo(), self.archivo("falso.pdf", b"not a PDF")],
                      [self.archivo("bad.exe", b"program", "application/octet-stream")],
                      [self.archivo() for _ in range(11)]):
            response = self.crear(files)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(RegistroEquipo.objects.count(), total)
        self.api.files().create.assert_not_called()

    def test_client_hides_empty_pending_error_and_entire_empty_section_in_backend(self):
        self.crear([self.archivo()])
        disponible = self.ultimo()
        vacio = RegistroEquipo.objects.create(equipo=self.equipo, tipo="NUEVO", fecha="2026-09-25")
        fallido = RegistroEquipo.objects.create(equipo=self.equipo, tipo="GARANTIA", fecha="2026-09-26", drive_error="Error Drive")
        self.login_as(self.cliente_user)
        with patch("masiscam.views.encolar_carpeta_registro") as repair:
            for view in ("ficha_detalle", "equipo_informe"):
                url = reverse("masiscam:" + view, args=[self.equipo.pk])
                response = self.client.get(url)
                self.assertEqual({r.pk for r in response.context["registros"]}, {self.registro.pk, disponible.pk})
                self.assertNotContains(response, "Eliminar Registro")
            repair.assert_not_called()
        RegistroEquipo.objects.filter(pk__in=[self.registro.pk, disponible.pk]).update(drive_error="Error Drive")
        for view in ("ficha_detalle", "equipo_informe"):
            response = self.client.get(reverse("masiscam:" + view, args=[self.equipo.pk]))
            self.assertEqual(response.context["registros"], [])
            self.assertNotContains(response, 'id="historial-registros"')
        self.login_as(self.admin)
        with override_settings(GOOGLE_DRIVE_ENABLED=False):
            response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.equipo.pk]))
        self.assertEqual(len(response.context["registros"]), 5)
        self.assertContains(response, "Error de Drive")
        self.assertContains(response, "Pendiente")
        self.assertContains(response, "Eliminar Registro")

    def test_client_cannot_upload_and_does_not_see_partially_failed_record(self):
        self.fallar = "dos.pdf"
        self.crear([self.archivo(), self.archivo("dos.pdf", b"%PDF-1.7 dos")])
        registro = self.ultimo()
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.equipo.pk]))
        self.assertNotIn(registro.pk, [r.pk for r in response.context["registros"]])
        self.assertEqual(self.crear([self.archivo()]).status_code, 403)
        self.assertEqual(self.client.post(self.retry_url(registro), {"archivos": [self.archivo()]}).status_code, 403)

    def test_database_sequence_constraint_and_deleted_record_retry(self):
        self.crear([self.archivo()])
        registro = self.ultimo()
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroEquipo.objects.create(equipo=self.equipo, tipo=registro.tipo, fecha=registro.fecha, secuencia=registro.secuencia)
        id = registro.pk
        registro.delete()
        self.api.reset_mock()
        revisar_archivos_registro.run(id)
        self.api.files().create.assert_not_called()

    def test_new_upload_download_is_protected_for_admin_and_owner(self):
        self.crear([self.archivo()])
        archivo = self.ultimo().archivos.get()
        url = reverse("masiscam:equipo_documento_privado", args=[self.equipo.pk, archivo.drive_file_id])
        self.api.files().get_media.side_effect = lambda fileId, **kwargs: fileId
        def download(stream, request, **kwargs):
            def chunk(**options):
                stream.write(self.contenidos[request])
                return None, True
            return MagicMock(next_chunk=chunk)
        with patch("masiscam.drive_documents.MediaIoBaseDownload", side_effect=download):
            for user in (self.admin, self.cliente_user):
                self.login_as(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
                self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.7 uno")
                response.close()
        self.equipo.cliente = self.otro_cliente
        self.equipo.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_pending_metadata_and_drive_listing_failure_hide_record_for_client(self):
        self.crear([self.archivo()])
        registro = self.ultimo()
        registro.archivos.update(estado="PENDIENTE")
        self.login_as(self.cliente_user)
        url = reverse("masiscam:ficha_detalle", args=[self.equipo.pk])
        response = self.client.get(url)
        self.assertNotIn(registro.pk, [r.pk for r in response.context["registros"]])
        with patch("masiscam.views.EquipoDriveDocuments.list", side_effect=TimeoutError):
            response = self.client.get(url)
            self.assertEqual(response.context["registros"], [])
            self.assertNotContains(response, 'id="historial-registros"')

    def test_celery_unavailable_still_allows_manual_retry(self):
        self.fallar, self.perder_respuesta = "uno.pdf", True
        self.queue.side_effect = ConnectionError("broker down")
        response = self.crear([self.archivo()])
        self.assertEqual(response.status_code, 302)
        registro = self.ultimo()
        count = self.api.files().create.call_count
        self.client.post(self.retry_url(registro))
        self.assertEqual(registro.archivos.get().estado, "DISPONIBLE")
        self.assertEqual(self.api.files().create.call_count, count)

    def test_existing_shared_record_folder_never_receives_uploaded_bytes(self):
        self.otro.drive_folder_id = self.registro.drive_folder_id
        self.otro.save()
        response = self.client.post(self.retry_url(self.registro), {"archivos": [self.archivo()]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.registro.archivos.get().estado, "ERROR")
        self.assertEqual(self.contenidos, {})

    def test_reservation_survives_folder_create_failure(self):
        original = self.api.files().create.side_effect
        def fail_folder(body, **kwargs):
            if "media_body" not in kwargs:
                raise TimeoutError("folder unavailable")
            return original(body, **kwargs)
        self.api.files().create.side_effect = fail_folder
        with self.assertLogs("masiscam.services", level="ERROR"):
            self.crear([self.archivo()])
        registro = self.ultimo()
        self.assertEqual(registro.secuencia, 1)
        self.api.files().create.side_effect = original
        self.client.post(self.retry_url(registro), {"archivos": [self.archivo()]})
        registro.refresh_from_db()
        self.assertEqual(registro.secuencia, 1)
        self.assertEqual(self.nodes[registro.drive_folder_id]["name"], "2026-09-21 - Mantenimiento 001")

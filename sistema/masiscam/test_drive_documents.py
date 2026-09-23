import re
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from googleapiclient.errors import HttpError
from httplib2 import Response

from .models import Equipo, Proyecto, RegistroEquipo
from .drive_documents import FOLDER
from .services import GoogleDriveService


@override_settings(GOOGLE_DRIVE_ENABLED=True, GOOGLE_DRIVE_ROOT_FOLDER_ID="configured-root")
class PublicDriveDocumentsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_clientes import ClientesReductoresTests
        ClientesReductoresTests.setUpTestData.__func__(cls)
        cls.antiguo.consulta_publica_activa = True
        cls.antiguo.drive_folder_id = "series-root"
        cls.antiguo.save()
        cls.other = Equipo.objects.create(proyecto=cls.proyecto, nombre="OTHER", drive_folder_id="other-root")

    def setUp(self):
        self.nodes = {
            "series-root": self.node("series-root", "SERIE", FOLDER, "type-root"),
            "internal": self.node("internal", "05_Informes", FOLDER, "series-root"),
            "other-root": self.node("other-root", "OTRA", FOLDER, "type-root"),
            "pdf": self.node("pdf", "informe.pdf", "application/pdf", "internal"),
            "foreign": self.node("foreign", "ajeno.pdf", "application/pdf", "other-root"),
            "exe": self.node("exe", "programa.exe", "application/octet-stream", "series-root"),
            "shortcut": self.node("shortcut", "enlace.pdf", "application/vnd.google-apps.shortcut", "series-root"),
        }
        self.api = MagicMock()
        self.api.files().get.side_effect = self.get
        self.api.files().list.side_effect = self.list
        service = object.__new__(GoogleDriveService)
        service.drive, service.shared_drive_id = self.api, "shared"
        patcher = patch("masiscam.drive_documents.GoogleDriveService", return_value=service)
        self.service_factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.content = b"%PDF-1.7 test content"
        self.download = MagicMock()
        def downloader(stream, request, **kwargs):
            def chunk(**kwargs):
                stream.write(self.content)
                return None, True
            self.download.next_chunk.side_effect = chunk
            return self.download
        patcher = patch("masiscam.drive_documents.MediaIoBaseDownload", side_effect=downloader)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def node(id, name, mime, parent):
        return {"id": id, "name": name, "mimeType": mime, "parents": [parent],
                "trashed": False, "size": "24", "capabilities": {"canDownload": True}}

    def get(self, fileId, **kwargs):
        if fileId not in self.nodes:
            raise HttpError(Response({"status": "404"}), b"private provider details")
        return MagicMock(execute=lambda: self.nodes[fileId].copy())

    def list(self, **kwargs):
        parent = re.search(r"^'([^']+)'", kwargs["q"]).group(1)
        self.assertEqual(kwargs["driveId"], "shared")
        return MagicMock(execute=lambda: {"files": [n.copy() for n in self.nodes.values() if n["parents"] == [parent]]})

    def url(self, file="pdf", token=None):
        return reverse("masiscam:equipo_documento_drive", args=[token or self.antiguo.token_publico, file])

    def test_invalid_token_no_drive_access(self):
        response = self.client.get(self.url(token="invalid"))
        self.assertEqual(response.status_code, 404)
        self.service_factory.assert_not_called()

    def test_foreign_missing_and_disallowed_files(self):
        for file in ("foreign", "missing", "exe", "shortcut", "internal"):
            with self.subTest(file=file):
                response = self.client.get(self.url(file))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b"private provider", response.content)
        self.api.files().get_media.assert_not_called()

    def test_pdf_inline_anonymous_and_safe_headers(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(b"".join(response.streaming_content), self.content)
        response.close()
        self.api.files().get_media.assert_called_once_with(fileId="pdf", supportsAllDrives=True)
        self.api.permissions.assert_not_called()

    def test_all_image_mimes(self):
        for ext, mime, content in [("jpg", "image/jpeg", b"\xff\xd8\xffimage"),
                                   ("jpeg", "image/jpeg", b"\xff\xd8\xffimage"),
                                   ("png", "image/png", b"\x89PNG\r\n\x1a\nimage"),
                                   ("webp", "image/webp", b"RIFF1234WEBPimage")]:
            self.nodes[ext] = self.node(ext, "image." + ext, mime, "series-root")
            self.content = content
            response = self.client.get(self.url(ext))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], mime)
            response.close()

    def test_mime_extension_and_content_mismatch_rejected(self):
        self.nodes["pdf"]["name"] = "fake.html"
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.api.files().get_media.assert_not_called()
        self.nodes["pdf"]["name"] = "fake.pdf"
        self.content = b"<html>not a PDF</html>"
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_public_list_only_own_files_without_drive_links(self):
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22",
                                      drive_folder_url="https://drive.google.com/secret-folder", drive_folder_id="internal")
        response = self.client.get(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico]))
        self.assertContains(response, "Documentos")
        self.assertContains(response, "informe.pdf")
        self.assertContains(response, self.url())
        for text in ("ajeno.pdf", "programa.exe", "enlace.pdf", "drive.google.com", "series-root", "other-root"):
            self.assertNotContains(response, text)

    def test_inactive_equipment_project_company_and_revoked_token(self):
        for field, value in [("consulta_publica_activa", False), ("estado", "INACTIVO")]:
            original = getattr(self.antiguo, field)
            Equipo.objects.filter(pk=self.antiguo.pk).update(**{field: value})
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            Equipo.objects.filter(pk=self.antiguo.pk).update(**{field: original})
        Proyecto.objects.filter(pk=self.proyecto.pk).update(estado="ARCHIVADO")
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        Proyecto.objects.filter(pk=self.proyecto.pk).update(estado="ACTIVO")
        self.empresa.activa = False
        self.empresa.save()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.empresa.activa = True
        self.empresa.save()
        Equipo.objects.filter(pk=self.antiguo.pk).update(token_publico="renewed")
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.api.files().get_media.assert_not_called()

    def test_trashed_moved_cycles_and_nested_equipment_roots(self):
        self.nodes["internal"]["trashed"] = True
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.nodes["internal"]["trashed"] = False
        self.nodes["internal"]["parents"] = ["other-root"]
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.nodes["internal"]["parents"] = ["internal"]
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.nodes["other-root"]["parents"] = ["series-root"]
        self.assertEqual(self.client.get(self.url("foreign")).status_code, 404)
        self.api.files().get_media.assert_not_called()

    def test_shared_root_and_configured_root_not_exposed(self):
        for folder in ("other-root", "configured-root", ""):
            Equipo.objects.filter(pk=self.antiguo.pk).update(drive_folder_id=folder)
            self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.service_factory.assert_not_called()

    def test_provider_failure_does_not_expose_details(self):
        self.api.files().get.side_effect = RuntimeError("access_token=SECRET /internal/credentials.json")
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "SECRET", status_code=503)
        response = self.client.get(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico]))
        self.assertContains(response, "temporalmente")
        self.assertNotContains(response, "credentials")

    def test_listing_pagination(self):
        pdf = self.nodes["pdf"].copy()
        pdf["parents"] = ["series-root"]
        def pages(**kwargs):
            response = {"files": [pdf]} if kwargs.get("pageToken") else {"files": [], "nextPageToken": "next"}
            return MagicMock(execute=lambda: response)
        self.api.files().list.side_effect = pages
        response = self.client.get(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico]))
        self.assertContains(response, "informe.pdf")
        self.assertEqual(self.api.files().list.call_count, 2)

    def test_deleted_during_download_returns_safe_404(self):
        self.api.files().get_media.side_effect = HttpError(Response({"status": "404"}), b"secret backend path")
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "secret", status_code=404)

    def test_file_moved_while_downloading_is_not_served(self):
        def chunk(**kwargs):
            self.nodes["pdf"]["parents"] = ["other-root"]
            return None, True
        def downloader(stream, request, **kwargs):
            return MagicMock(next_chunk=chunk)
        with patch("masiscam.drive_documents.MediaIoBaseDownload", side_effect=downloader):
            self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_download_permission_and_size_limits(self):
        self.nodes["pdf"]["capabilities"]["canDownload"] = False
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.nodes["pdf"]["capabilities"]["canDownload"] = True
        self.nodes["pdf"]["size"] = str(65 * 1024 * 1024)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.api.files().get_media.assert_not_called()

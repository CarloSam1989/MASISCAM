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
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22",
                                      drive_folder_id="internal")
        self.api.files().get.side_effect = RuntimeError("access_token=SECRET /internal/credentials.json")
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "SECRET", status_code=503)
        response = self.client.get(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico]))
        self.assertContains(response, "temporalmente")
        self.assertNotContains(response, "credentials")

    def test_listing_pagination(self):
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22",
                                      drive_folder_id="series-root")
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

    def login(self):
        self.client.force_login(self.usuario)

    def private_url(self, file="pdf", equipo=None):
        return reverse("masiscam:equipo_documento_privado", args=[equipo or self.antiguo.pk, file])

    def test_internal_documents_require_login_and_ignore_public_flag(self):
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22",
                                      drive_folder_id="internal")
        response = self.client.get(self.private_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.service_factory.assert_not_called()
        self.login()
        Equipo.objects.filter(pk=self.antiguo.pk).update(consulta_publica_activa=False)
        response = self.client.get(self.private_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        response.close()
        self.assertEqual(self.client.get(self.private_url("foreign")).status_code, 404)
        for vista in ("ficha_detalle", "equipo_informe"):
            response = self.client.get(reverse("masiscam:" + vista, args=[self.antiguo.pk]))
            self.assertContains(response, self.private_url())
            self.assertNotContains(response, "drive.google.com")
            self.assertNotContains(response, self.url())

    def test_internal_customer_cannot_access_another_customer(self):
        from .models import RolMasiscam
        Equipo.objects.filter(pk=self.antiguo.pk).update(cliente=self.cliente)
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="CLIENTE", cliente=self.cliente)
        self.login()
        response = self.client.get(self.private_url())
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(self.private_url("foreign", self.other.pk)).status_code, 403)

    def test_token_rotation_changes_url_qr_and_keeps_data(self):
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22",
                                      drive_folder_id="internal")
        from . import views
        old = self.antiguo.token_publico
        old_url = self.url()
        self.login()
        qr_url = reverse("masiscam:equipo_qr", args=[self.antiguo.pk])
        before_qr = self.client.get(qr_url).content
        response = self.client.post(reverse("masiscam:equipo_token_regenerar", args=[self.antiguo.pk]))
        self.assertEqual(response.status_code, 302)
        self.antiguo.refresh_from_db()
        self.assertNotEqual(self.antiguo.token_publico, old)
        self.assertEqual(self.antiguo.drive_folder_id, "series-root")
        self.assertNotEqual(self.client.get(qr_url).content, before_qr)
        self.assertIn(self.antiguo.token_publico, views._url_publica_equipo(self.antiguo))
        self.client.logout()
        self.assertEqual(self.client.get(old_url).status_code, 404)
        self.assertEqual(self.client.get(reverse("masiscam:equipo_publico", args=[old])).status_code, 404)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertContains(self.client.get(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico])), "informe.pdf")
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()

    def test_disable_url_and_soft_delete_preserve_history_and_files(self):
        from .models import Documento, RolMasiscam
        registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22")
        documento = Documento.objects.create(proyecto=self.proyecto, equipo=self.antiguo,
            titulo="Conservar", archivo="conservar.pdf", hash_sha256="f" * 64, subido_por=self.usuario)
        self.login()
        old_url = self.url()
        response = self.client.post(reverse("masiscam:equipo_publico_desactivar", args=[self.antiguo.pk]))
        self.assertEqual(response.status_code, 302)
        self.antiguo.refresh_from_db()
        self.assertFalse(self.antiguo.consulta_publica_activa)
        self.assertEqual(self.client.get(old_url).status_code, 404)
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="ADMIN")
        for tipo in ("REDUCTOR", "BOMBA"):
            Equipo.objects.filter(pk=self.antiguo.pk).update(tipo_producto=tipo, estado="ACTIVO", consulta_publica_activa=True)
            response = self.client.post(reverse("masiscam:ficha_archivar", args=[self.antiguo.pk]))
            self.assertEqual(response.status_code, 302)
            self.antiguo.refresh_from_db()
            self.assertEqual(self.antiguo.estado, "INACTIVO")
            self.assertEqual(self.antiguo.drive_folder_id, "series-root")
            self.assertTrue(RegistroEquipo.objects.filter(pk=registro.pk).exists())
            self.assertTrue(Documento.objects.filter(pk=documento.pk).exists())
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            self.assertNotContains(self.client.get(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico])), "informe.pdf")
        self.api.files().delete.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_live_documents_empty_then_added_without_manual_state(self):
        self.login()
        registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO",
            fecha="2026-09-23", drive_folder_id="internal", drive_error="estado antiguo")
        empty = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-22")
        pdf = self.nodes.pop("pdf")
        ficha = reverse("masiscam:ficha_detalle", args=[self.antiguo.pk])
        response = self.client.get(ficha)
        self.assertContains(response, "Sin documentos")
        self.assertEqual(response.context["documentos_drive"], [])
        self.assertEqual(response.context["registros"][0].documentos_drive, [])
        self.service_factory.reset_mock()
        self.nodes["pdf"] = pdf
        response = self.client.get(ficha)
        self.service_factory.assert_called_once()
        self.assertContains(response, self.private_url())
        registros = {r.pk: r for r in response.context["registros"]}
        self.assertEqual([d["id"] for d in registros[registro.pk].documentos_drive], ["pdf"])
        self.assertEqual(registros[empty.pk].documentos_drive, [])
        registro.refresh_from_db()
        self.assertEqual(registro.drive_error, "estado antiguo")
        self.assertNotContains(response, "drive.google.com")
        self.assertNotContains(response, "series-root")
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_listing_uses_live_files(self):
        self.login()
        url = reverse("masiscam:producto_listado", args=["reductor"])
        pdf = self.nodes.pop("pdf")
        response = self.client.get(url)
        self.assertContains(response, "Sin documentos")
        self.assertNotContains(response, self.private_url())
        self.nodes["pdf"] = pdf
        self.assertContains(self.client.get(url), self.private_url())

    def test_no_read_permission_hides_section_and_blocks_direct_url(self):
        from .models import RolMasiscam
        self.login()
        with patch.dict("masiscam.access.PERMISOS_ROL", {RolMasiscam.Rol.TECNICO: set()}):
            response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.antiguo.pk]))
            self.assertNotContains(response, 'id="documentos-titulo"')
            self.assertNotContains(response, '<th scope="col">Documentos</th>')
            self.assertNotContains(response, "informe.pdf")
            self.assertEqual(self.client.get(self.private_url()).status_code, 403)
        self.service_factory.assert_not_called()

    def test_unauthorized_customer_never_lists_or_downloads_other_equipment(self):
        from .models import RolMasiscam
        Equipo.objects.filter(pk=self.antiguo.pk).update(cliente=self.cliente)
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="CLIENTE", cliente=self.cliente)
        self.login()
        for vista in ("ficha_detalle", "equipo_informe"):
            response = self.client.get(reverse("masiscam:" + vista, args=[self.other.pk]))
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, "ajeno.pdf", status_code=403)
        self.assertEqual(self.client.get(self.private_url("foreign", self.other.pk)).status_code, 403)
        self.service_factory.assert_not_called()

    def test_record_only_lists_its_own_descendants(self):
        self.login()
        registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO",
            fecha="2026-09-23", drive_folder_id="internal")
        self.nodes["nested"] = self.node("nested", "Fotos", FOLDER, "internal")
        self.nodes["photo"] = self.node("photo", "foto.png", "image/png", "nested")
        self.nodes["rootfile"] = self.node("rootfile", "general.pdf", "application/pdf", "series-root")
        response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.antiguo.pk]))
        self.assertEqual({d["id"] for d in response.context["registros"][0].documentos_drive}, {"pdf", "photo"})
        self.assertEqual(len(response.context["documentos_drive"]), 3)
        self.service_factory.assert_called_once()

    def test_documents_only_in_matching_record_in_internal_and_public_views(self):
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO",
            fecha="2026-09-23", drive_folder_id="internal")
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO",
            fecha="2026-09-22", drive_folder_id="new-folder")
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="ASISTENCIA", fecha="2026-09-21")
        self.nodes["new-folder"] = self.node("new-folder", "Nuevo", FOLDER, "series-root")
        self.nodes["new-file"] = self.node("new-file", "nuevo.png", "image/png", "new-folder")
        self.nodes["root-file"] = self.node("root-file", "sin-categoria.pdf", "application/pdf", "series-root")
        self.login()
        urls = [reverse("masiscam:" + name, args=[self.antiguo.pk])
                for name in ("ficha_detalle", "equipo_informe")]
        urls.append(reverse("masiscam:equipo_publico", args=[self.antiguo.token_publico]))
        for index, url in enumerate(urls):
            with self.subTest(url=url):
                if index == 2:
                    self.client.logout()
                self.service_factory.reset_mock()
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.service_factory.assert_called_once()
                self.assertNotContains(response, 'id="documentos-titulo"')
                self.assertNotContains(response, "sin-categoria.pdf")
                self.assertNotContains(response, "drive.google.com")
                html = response.content.decode()
                rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
                maintenance = next(row for row in rows if "<td>Mantenimiento</td>" in row)
                new = next(row for row in rows if "<td>Nuevo</td>" in row)
                empty = next(row for row in rows if "<td>Asistencia</td>" in row)
                self.assertIn("informe.pdf", maintenance)
                self.assertNotIn("nuevo.png", maintenance)
                self.assertIn("nuevo.png", new)
                self.assertNotIn("informe.pdf", new)
                self.assertIn("Sin documentos", empty)
                expected = self.url() if index == 2 else self.private_url()
                self.assertIn(expected, maintenance)
                self.assertEqual(html.count('aria-label="Ver informe.pdf"'), 1)

    def test_inactive_equipment_hides_records_and_files_without_deleting(self):
        registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO",
            fecha="2026-09-23", drive_folder_id="internal")
        Equipo.objects.filter(pk=self.antiguo.pk).update(estado="INACTIVO")
        self.login()
        response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.antiguo.pk]))
        self.assertContains(response, self.antiguo.numero_serie)
        self.assertContains(response, "Inactivo")
        for text in ("historial-registros", "registro-modal", "Pendiente", "informe.pdf", "Agregar registro"):
            self.assertNotContains(response, text)
        self.assertEqual(self.client.get(self.private_url()).status_code, 403)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.service_factory.assert_not_called()
        registro.refresh_from_db()
        self.assertEqual(registro.drive_folder_id, "internal")
        self.assertTrue(RegistroEquipo.objects.filter(pk=registro.pk).exists())

    def test_nested_or_shared_record_folders_do_not_duplicate_files(self):
        self.login()
        parent = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO",
            fecha="2026-09-22", drive_folder_id="series-root")
        child = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO",
            fecha="2026-09-23", drive_folder_id="internal")
        url = reverse("masiscam:ficha_detalle", args=[self.antiguo.pk])
        response = self.client.get(url)
        registros = {r.pk: r for r in response.context["registros"]}
        self.assertEqual(registros[parent.pk].documentos_drive, [])
        self.assertEqual([d["id"] for d in registros[child.pk].documentos_drive], ["pdf"])
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="ASISTENCIA",
            fecha="2026-09-24", drive_folder_id="internal")
        response = self.client.get(url)
        self.assertNotContains(response, "informe.pdf")
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()

    def test_inactive_customer_only_message_and_direct_urls_blocked(self):
        from .models import RolMasiscam
        Equipo.objects.filter(pk=self.antiguo.pk).update(cliente=self.cliente, estado="INACTIVO")
        self.login()
        ficha = reverse("masiscam:ficha_detalle", args=[self.antiguo.pk])
        self.assertContains(self.client.get(ficha), self.antiguo.numero_serie)
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="CLIENTE", cliente=self.cliente)
        for url in (ficha, ficha + "?foto=placa"):
            response = self.client.get(url)
            self.assertTemplateUsed(response, "masiscam/equipo_inactivo.html")
            self.assertTemplateUsed(response, "masiscam/base.html")
            self.assertTrue(response.context["equipo_inactivo"])
            self.assertContains(response, "Este equipo se encuentra actualmente inactivo.")
            self.assertContains(response, 'id="appMenu"')
            self.assertContains(response, "Mis productos")
            self.assertContains(response, "Salir")
            self.assertContains(response, reverse("accounts:logout"))
            self.assertContains(response, "csrfmiddlewaretoken")
            self.assertEqual(response["Cache-Control"], "private, no-store")
            for hidden in (self.antiguo.numero_serie, "Ver informe", "historial-registros",
                           "informe.pdf", "INFORMACIÓN GENERAL", "Descargar QR"):
                self.assertNotContains(response, hidden)
        self.assertEqual(self.client.get(reverse("masiscam:equipo_informe", args=[self.antiguo.pk])).status_code, 403)
        self.assertEqual(self.client.get(self.private_url()).status_code, 403)
        self.service_factory.assert_not_called()

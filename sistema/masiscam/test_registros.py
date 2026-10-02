from io import BytesIO
from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .models import Auditoria, Equipo, RegistroEquipo, RolMasiscam
from .services import GoogleDriveService, sincronizar_carpeta_registro
from . import test_cliente_access, test_drive_equipo


@override_settings(GOOGLE_DRIVE_ENABLED=False, GOOGLE_DRIVE_ROOT_FOLDER_ID="configured-root")
class RegistroActionsTests(TestCase):
    setUpTestData = classmethod(test_cliente_access.ClienteAccessTests.setUpTestData.__func__)
    login_as = test_cliente_access.ClienteAccessTests.login_as

    def setUp(self):
        self.equipo.drive_folder_id = "equipment-root"
        self.equipo.save()
        self.registro = RegistroEquipo.objects.create(
            equipo=self.equipo, tipo="MANTENIMIENTO", fecha="2026-09-20", drive_folder_id="record")
        self.otro = RegistroEquipo.objects.create(
            equipo=self.equipo, tipo="MANTENIMIENTO", fecha="2026-09-10", drive_folder_id="sibling")
        folder = "application/vnd.google-apps.folder"
        self.nodes = {
            "equipment-root": {"id": "equipment-root", "parents": ["configured-root"], "mimeType": folder},
            "record": {"id": "record", "parents": ["equipment-root"], "mimeType": folder},
            "nested": {"id": "nested", "parents": ["record"], "mimeType": folder},
            "sibling": {"id": "sibling", "parents": ["equipment-root"], "mimeType": folder},
            "pdf": {"id": "pdf", "name": "archivo.pdf", "parents": ["nested"], "mimeType": "application/pdf",
                    "size": "24", "capabilities": {"canDownload": True}},
        }
        self.api = MagicMock()
        self.api.files().get.side_effect = lambda fileId, **kw: MagicMock(execute=lambda: self.nodes[fileId].copy())
        def listar(**kw):
            parent = kw["q"].split("'")[1]
            folders_only = "mimeType=" in kw["q"]
            return MagicMock(execute=lambda: {"files": [n.copy() for n in self.nodes.values()
                if n["parents"] == [parent] and (not folders_only or n["mimeType"] == folder)]})
        def actualizar(fileId, body, **kw):
            def execute():
                self.nodes[fileId].update(body)
                if kw.get("addParents"):
                    self.nodes[fileId]["parents"] = [kw["addParents"]]
                pending = [fileId] if "trashed" in body else []
                while pending:
                    parent = pending.pop()
                    for n in self.nodes.values():
                        if n["parents"] == [parent]:
                            n.update(body)
                            pending.append(n["id"])
                return self.nodes[fileId].copy()
            return MagicMock(execute=execute)
        def eliminar(fileId, **kw):
            def execute():
                pendientes = [fileId]
                while pendientes:
                    actual = pendientes.pop()
                    pendientes.extend(n["id"] for n in self.nodes.values() if n["parents"] == [actual])
                    self.nodes.pop(actual, None)
                return {}
            return MagicMock(execute=execute)
        self.api.files().list.side_effect = listar
        self.api.files().update.side_effect = actualizar
        self.api.files().delete.side_effect = eliminar
        self.service = object.__new__(GoogleDriveService)
        self.service.drive, self.service.shared_drive_id = self.api, ""
        for path in ("masiscam.views.GoogleDriveService", "masiscam.drive_documents.GoogleDriveService"):
            patcher = patch(path, return_value=self.service)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.login_as(self.admin)

    def url(self, registro=None, equipo=None):
        return reverse("masiscam:registro_eliminar", args=[equipo or self.equipo.pk, (registro or self.registro).pk])

    def test_confirmacion_csrf_y_cancelacion_no_eliminan(self):
        response = self.client.get(self.url())
        self.assertContains(response, "Confirmar eliminación")
        self.assertContains(response, "Cancelar")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertEqual(self.client.post(self.url()).status_code, 400)
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.url(), {"confirmar": "eliminar"}).status_code, 403)
        self.api.files().delete.assert_not_called()
        self.assertTrue(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())

    def test_admin_elimina_carpeta_contenido_y_registro_y_audita(self):
        response = self.client.post(self.url(), {"confirmar": "eliminar"})
        self.assertEqual(response.status_code, 302)
        self.api.files().delete.assert_called_once_with(fileId="record", supportsAllDrives=True)
        for node in ("record", "nested", "pdf"):
            self.assertNotIn(node, self.nodes)
        for node in ("equipment-root", "sibling"):
            self.assertIn(node, self.nodes)
        self.assertFalse(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())
        self.otro.refresh_from_db()
        self.equipo.refresh_from_db()
        self.assertEqual(self.otro.drive_folder_id, "sibling")
        self.assertEqual(self.equipo.drive_folder_id, "equipment-root")
        audit = Auditoria.objects.get(accion="REGISTRO_EQUIPO_ELIMINADO")
        self.assertEqual(audit.usuario, self.admin)
        self.assertEqual(audit.objeto_id, str(self.registro.pk))
        self.assertEqual(audit.detalle["fecha"], "2026-09-20")
        self.assertEqual(audit.detalle["drive_folder_id"], "record")
        self.assertEqual(audit.detalle["destino"], "eliminacion_permanente")

    def test_fallo_drive_conserva_datos_y_muestra_error(self):
        original = RegistroEquipo.objects.values().get(pk=self.registro.pk)
        self.api.files().delete.side_effect = TimeoutError("private provider details")
        response = self.client.post(self.url(), {"confirmar": "eliminar"}, follow=True)
        self.assertContains(response, "Se conservaron los datos del registro")
        self.assertNotContains(response, "private provider details")
        self.assertEqual(RegistroEquipo.objects.values().get(pk=self.registro.pk), original)
        self.assertFalse(Auditoria.objects.filter(accion="REGISTRO_EQUIPO_ELIMINADO").exists())

    def test_fallo_auditoria_no_toca_drive(self):
        with patch("masiscam.views.auditar", side_effect=RuntimeError("database unavailable")):
            self.assertEqual(self.client.post(self.url(), {"confirmar": "eliminar"}).status_code, 302)
        self.api.files().delete.assert_not_called()
        self.assertTrue(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())

    def test_cliente_y_tecnico_no_pueden_eliminar_ni_ver_boton(self):
        for user in (self.cliente_user, self.admin):
            if user == self.admin:
                RolMasiscam.objects.filter(perfil__user=user).update(rol="TECNICO")
            self.login_as(user)
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(self.url(), {"confirmar": "eliminar"}).status_code, 403)
            response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.equipo.pk]))
            self.assertNotContains(response, "Eliminar Registro")
        self.api.files().delete.assert_not_called()

    def test_registro_de_otro_equipo_y_otra_empresa_no_se_elimina(self):
        self.assertEqual(self.client.post(self.url(equipo=self.equipo_ajeno.pk), {"confirmar": "eliminar"}).status_code, 404)
        from accounts.models import Empresa
        empresa = Empresa.objects.create(nombre="Otra empresa")
        self.proyecto.empresa = empresa
        self.proyecto.save()
        self.assertEqual(self.client.post(self.url(), {"confirmar": "eliminar"}).status_code, 404)
        self.api.files().delete.assert_not_called()

    def test_protege_raices_carpetas_compartidas_y_registros_anidados(self):
        for folder in ("configured-root", "equipment-root", "sibling"):
            RegistroEquipo.objects.filter(pk=self.registro.pk).update(drive_folder_id=folder)
            self.assertEqual(self.client.post(self.url(), {"confirmar": "eliminar"}).status_code, 302)
            self.assertTrue(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())
        RegistroEquipo.objects.filter(pk=self.registro.pk).update(drive_folder_id="record")
        RegistroEquipo.objects.filter(pk=self.otro.pk).update(drive_folder_id="nested")
        self.assertEqual(self.client.post(self.url(), {"confirmar": "eliminar"}).status_code, 302)
        self.assertTrue(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())
        self.api.files().delete.assert_not_called()

    def test_carpeta_movida_fuera_del_equipo_no_se_elimina(self):
        self.nodes["record"]["parents"] = ["foreign-root"]
        self.client.post(self.url(), {"confirmar": "eliminar"})
        self.assertTrue(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())
        self.api.files().delete.assert_not_called()

    def test_reintento_tras_respuesta_perdida_borra_solo_el_registro_validado(self):
        self.nodes["record"]["trashed"] = True
        self.client.post(self.url(), {"confirmar": "eliminar"})
        self.assertFalse(RegistroEquipo.objects.filter(pk=self.registro.pk).exists())
        self.assertTrue(RegistroEquipo.objects.filter(pk=self.otro.pk).exists())
        self.api.files().delete.assert_called_once_with(fileId="record", supportsAllDrives=True)

    def test_ficha_informe_ordenan_por_fecha_del_formulario(self):
        nuevo = RegistroEquipo.objects.create(equipo=self.equipo, tipo="NUEVO", fecha="2026-09-30")
        antiguo = RegistroEquipo.objects.create(equipo=self.equipo, tipo="GARANTIA", fecha="2026-01-01")
        secuencia_alta = RegistroEquipo.objects.create(
            equipo=self.equipo, tipo="NUEVO", fecha="2026-09-15", secuencia=20)
        secuencia_baja = RegistroEquipo.objects.create(
            equipo=self.equipo, tipo="GARANTIA", fecha="2026-09-15", secuencia=10)
        for user in (self.admin, self.cliente_user):
            self.login_as(user)
            for view in ("ficha_detalle", "equipo_informe"):
                response = self.client.get(reverse("masiscam:" + view, args=[self.equipo.pk]))
                self.assertEqual([r.pk for r in response.context["registros"]],
                                 [antiguo.pk, self.otro.pk, secuencia_alta.pk, secuencia_baja.pk,
                                  self.registro.pk, nuevo.pk] if user == self.admin else [self.registro.pk])

    def test_nombre_descarga_protegida_admin_cliente_y_equipo_ajeno(self):
        private = reverse("masiscam:equipo_documento_privado", args=[self.equipo.pk, "pdf"])
        Equipo.objects.filter(pk=self.equipo.pk).update(consulta_publica_activa=True)
        for user in (self.admin, self.cliente_user):
            self.login_as(user)
            for view in ("ficha_detalle", "equipo_informe"):
                response = self.client.get(reverse("masiscam:" + view, args=[self.equipo.pk]))
                self.assertContains(response, f'<a class="text-break" href="{private}" download>archivo.pdf</a>', html=True)
                self.assertNotContains(response, ">Ver</a>")
                self.assertNotContains(response, "drive.google.com")
            with patch("masiscam.views.EquipoDriveDocuments.download",
                       return_value=(BytesIO(b"%PDF-test"), "archivo.pdf", "application/pdf")):
                response = self.client.get(private)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
                response.close()
        self.login_as(self.cliente_user)
        Equipo.objects.filter(pk=self.equipo_ajeno.pk).update(consulta_publica_activa=True, drive_folder_id="foreign-root")
        for url in (reverse("masiscam:equipo_documento_privado", args=[self.equipo_ajeno.pk, "pdf"]),
                    reverse("masiscam:equipo_documento_drive", args=[self.equipo_ajeno.token_publico, "pdf"])):
            with patch("masiscam.views.EquipoDriveDocuments") as documents:
                self.assertEqual(self.client.get(url).status_code, 403)
                documents.assert_not_called()
        self.nodes["pdf"]["parents"] = ["foreign-root"]
        self.assertEqual(self.client.get(private).status_code, 404)
        self.api.files().get_media.assert_not_called()
        self.client.logout()
        for url in (private, reverse("masiscam:equipo_documento_drive", args=[self.equipo.token_publico, "pdf"])):
            self.assertEqual(self.client.get(url).status_code, 302)


@override_settings(GOOGLE_DRIVE_ENABLED=False, GOOGLE_DRIVE_ROOT_FOLDER_ID="root-masiscam")
class ConcurrentRecordFoldersTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_same_type_creations_and_retries(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        from .test_clientes import ClientesReductoresTests
        ClientesReductoresTests.setUpTestData.__func__(type(self))
        test_drive_equipo.DriveEquipoTests.setUp(self)
        barrier = Barrier(2)
        def create():
            try:
                registro = RegistroEquipo.objects.create(equipo_id=self.antiguo.pk, tipo="MANTENIMIENTO", fecha="2026-09-20")
                barrier.wait(timeout=10)
                return registro.pk, sincronizar_carpeta_registro(registro.pk)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(len({folder for _, folder in results}), 2)
        self.assertEqual({self.archivos[folder]["name"] for _, folder in results},
                         {"MANTENIMIENTO 001", "MANTENIMIENTO 002"})
        count = len(self.archivos)
        for pk, folder in results:
            self.assertEqual(sincronizar_carpeta_registro(pk), folder)
        self.assertEqual(len(self.archivos), count)

    listar = test_drive_equipo.DriveEquipoTests.listar
    crear = test_drive_equipo.DriveEquipoTests.crear

    obtener = test_drive_equipo.DriveEquipoTests.obtener
    actualizar = test_drive_equipo.DriveEquipoTests.actualizar

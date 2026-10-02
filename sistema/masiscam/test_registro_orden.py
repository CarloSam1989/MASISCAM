from copy import deepcopy
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from . import test_registro_archivos
from .models import ArchivoRegistro, Auditoria, RegistroEquipo, RolMasiscam
from .registro_orden import PENDIENTE, sincronizar_grupo
from .services import sincronizar_carpeta_registro


@override_settings(GOOGLE_DRIVE_ENABLED=True, GOOGLE_DRIVE_ROOT_FOLDER_ID="configured-root")
class RegistroOrdenTests(TestCase):
    setUpTestData = classmethod(test_registro_archivos.RegistroUploadTests.setUpTestData.__func__)
    login_as = test_registro_archivos.RegistroUploadTests.login_as

    def setUp(self):
        test_registro_archivos.RegistroUploadTests.setUp(self)
        RegistroEquipo.objects.filter(pk=self.registro.pk).update(fecha="2026-01-01", secuencia=1)
        RegistroEquipo.objects.filter(pk=self.otro.pk).update(fecha="2026-03-01", secuencia=2)
        self.tercero = RegistroEquipo.objects.create(equipo=self.equipo, tipo="MANTENIMIENTO", fecha="2026-05-01",
                                                   secuencia=3, drive_folder_id="third")
        self.nodes["third"] = dict(id="third", name="Mantenimiento 003", parents=["equipment-root"], mimeType="application/vnd.google-apps.folder")
        for folder, file in (("sibling", "doc2"), ("third", "doc3")):
            self.nodes[file] = dict(id=file, name=file+".pdf", parents=[folder], mimeType="application/pdf",
                                   size="20", capabilities={"canDownload": True})
        self.documento = ArchivoRegistro.objects.create(registro=self.tercero, nombre="doc3.pdf", tamano=20,
            tipo_mime="application/pdf", hash_sha256="a"*64, drive_file_id="doc3", estado="DISPONIBLE")
        self.registro.refresh_from_db()
        self.otro.refresh_from_db()

    def url(self, registro=None, equipo=None):
        return reverse("masiscam:registro_editar", args=[equipo or self.equipo.pk, (registro or self.tercero).pk])

    def editar(self, fecha="2026-02-01"):
        return self.client.post(self.url(), {"fecha": fecha, "tipo": "GARANTIA"})

    def assert_layout(self):
        for registro in self.equipo.registros.all():
            folder = self.nodes[registro.drive_folder_id]
            fecha = self.nodes[folder["parents"][0]]
            tipo = self.nodes[fecha["parents"][0]]
            self.assertEqual(folder["name"], f"{registro.tipo} {registro.secuencia:03d}")
            self.assertEqual(fecha["name"], str(registro.fecha))
            self.assertEqual(tipo["name"], registro.tipo)
            self.assertEqual(tipo["parents"], ["equipment-root"])

    def test_editar_fecha_conserva_registros_documentos_ids_y_renumera(self):
        ids = dict(self.equipo.registros.values_list("pk", "drive_folder_id"))
        documentos = deepcopy({k:v for k,v in self.nodes.items() if v["mimeType"] == "application/pdf"})
        response = self.editar()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(dict(self.equipo.registros.values_list("pk", "drive_folder_id")), ids)
        self.assertEqual(list(self.equipo.registros.values_list("pk", "secuencia")),
                         [(self.registro.pk, 1), (self.tercero.pk, 2), (self.otro.pk, 3)])
        self.documento.refresh_from_db()
        self.assertEqual(self.documento.registro_id, self.tercero.pk)
        self.assertEqual(self.documento.drive_file_id, "doc3")
        self.assertEqual({k:self.nodes[k] for k in documentos}, documentos)
        self.assert_layout()
        self.api.files().delete.assert_not_called()
        audit = Auditoria.objects.get(accion="REGISTRO_FECHA_EDITADA")
        self.assertEqual(audit.detalle["fecha_anterior"], "2026-05-01")
        self.assertEqual(audit.detalle["tipo"], "MANTENIMIENTO")

    def test_admin_cliente_mismo_orden_numeros_y_cliente_sigue_filtrado(self):
        self.editar()
        for user in (self.admin, self.cliente_user):
            self.login_as(user)
            for view in ("ficha_detalle", "equipo_informe"):
                response = self.client.get(reverse("masiscam:"+view, args=[self.equipo.pk]))
                self.assertEqual([r.pk for r in response.context["registros"]], [self.registro.pk, self.tercero.pk, self.otro.pk])
                for numero in (1,2,3):
                    self.assertContains(response, f"Mantenimiento #{numero:03d}")
                if user == self.cliente_user:
                    self.assertNotContains(response, "Editar registro")
        self.nodes.pop("doc3")
        response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.equipo.pk]))
        self.assertEqual([r.pk for r in response.context["registros"]], [self.registro.pk, self.otro.pk])
        self.assertContains(response, "Mantenimiento #003")
        self.assertNotContains(response, "Mantenimiento #002")

    def test_solo_admin_fecha_valida_y_registro_del_equipo(self):
        response = self.client.get(self.url())
        self.assertContains(response, 'name="fecha"')
        self.assertNotContains(response, 'name="tipo"')
        self.assertEqual(self.editar("fecha incorrecta").status_code, 400)
        self.assertEqual(self.client.post(self.url(equipo=self.equipo_ajeno.pk), {"fecha":"2026-01-01"}).status_code, 404)
        for user in (self.cliente_user, self.admin):
            if user == self.admin:
                RolMasiscam.objects.filter(perfil__user=user).update(rol="TECNICO")
            self.login_as(user)
            self.assertEqual(self.client.get(self.url()).status_code, 403)
            self.assertEqual(self.editar().status_code, 403)
        self.tercero.refresh_from_db()
        self.assertEqual(self.tercero.fecha, date(2026,5,1))
        self.api.files().update.assert_not_called()

    def test_fallo_parcial_conserva_ids_y_reintento_completa_sin_duplicar(self):
        original = self.api.files().update.side_effect
        failed = []
        def update(fileId, body, **kwargs):
            call = original(fileId, body, **kwargs)
            def execute():
                result = call.execute()
                if kwargs.get("addParents") and not failed:
                    failed.append(True)
                    raise TimeoutError("lost response after move")
                return result
            return MagicMock(execute=execute)
        self.api.files().update.side_effect = update
        response = self.editar()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.equipo.registros.filter(drive_error=PENDIENTE).count(), 3)
        self.tercero.refresh_from_db()
        self.assertEqual(self.tercero.fecha, date(2026,2,1))
        ids = dict(self.equipo.registros.values_list("pk", "drive_folder_id"))
        count = len(self.nodes)
        self.api.files().update.side_effect = original
        self.editar()
        self.assertEqual(len(self.nodes), count)
        self.assertEqual(dict(self.equipo.registros.values_list("pk", "drive_folder_id")), ids)
        self.assertFalse(self.equipo.registros.exclude(drive_error="").exists())
        self.assert_layout()
        self.assertEqual(self.nodes["doc3"]["parents"], ["third"])

    def test_repetir_edicion_no_duplica_ni_mueve_otra_vez(self):
        self.editar()
        count = len(self.nodes)
        self.api.files().update.reset_mock()
        self.editar()
        self.assertEqual(len(self.nodes), count)
        self.api.files().update.assert_not_called()

    def test_fecha_igual_desempate_estable_sin_usar_creado_en(self):
        self.editar("2026-03-01")
        RegistroEquipo.objects.filter(pk=self.otro.pk).update(creado_en=datetime(2030,1,1,tzinfo=timezone.utc))
        RegistroEquipo.objects.filter(pk=self.tercero.pk).update(creado_en=datetime(2020,1,1,tzinfo=timezone.utc))
        sincronizar_grupo(self.equipo.pk, "MANTENIMIENTO")
        self.assertEqual(list(self.equipo.registros.values_list("pk", "secuencia")),
                         [(self.registro.pk,1),(self.otro.pk,2),(self.tercero.pk,3)])
        self.assertEqual(self.nodes["sibling"]["parents"], self.nodes["third"]["parents"])

    def test_alta_antedatada_renumera_grupo_y_otros_tipos_independientes(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("masiscam:registro_crear", args=[self.equipo.pk]), {
                "tipo":"MANTENIMIENTO", "fecha":"2025-12-01", "clave_creacion":"e0b3c8cf-1769-42a4-b8b7-1f5cbb05b408"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.equipo.registros.values_list("secuencia", flat=True)), [1,2,3,4])
        self.assert_layout()
        for tipo in ("GARANTIA", "ASISTENCIA", "REPARACION", "NUEVO"):
            registro = RegistroEquipo.objects.create(equipo=self.equipo,tipo=tipo,fecha="2026-01-01")
            sincronizar_carpeta_registro(registro.pk)
            registro.refresh_from_db()
            self.assertEqual(registro.secuencia, 1)
        self.assert_layout()

    def test_carpeta_compartida_o_fuera_del_equipo_no_se_mueve(self):
        RegistroEquipo.objects.create(equipo=self.equipo_ajeno,tipo="NUEVO",fecha="2026-01-01",drive_folder_id="third")
        self.editar()
        self.api.files().update.assert_not_called()
        self.assertTrue(self.equipo.registros.filter(drive_error=PENDIENTE).exists())

    def test_descarga_y_eliminacion_siguen_aceptando_jerarquia_nueva(self):
        self.editar()
        from .drive_documents import EquipoDriveDocuments
        self.assertEqual(EquipoDriveDocuments(self.equipo).authorize("doc3")["id"], "doc3")
        self.tercero.refresh_from_db()
        self.assertEqual(self.service.validar_carpeta_registro_eliminable(self.tercero)["id"], "third")
        response = self.client.post(reverse("masiscam:registro_eliminar", args=[self.equipo.pk, self.tercero.pk]),
                                    {"confirmar": "eliminar"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RegistroEquipo.objects.filter(pk=self.tercero.pk).exists())
        self.assertNotIn("third", self.nodes)
        self.assertNotIn("doc3", self.nodes)
        self.assertIn("equipment-root", self.nodes)
        self.assertIn("doc2", self.nodes)
        self.assertEqual(list(self.equipo.registros.values_list("secuencia", flat=True)), [1, 2])
        self.assert_layout()

    def test_carpeta_fuera_del_equipo_no_se_mueve(self):
        self.nodes["third"]["parents"] = ["foreign-root"]
        self.editar()
        self.api.files().update.assert_not_called()
        self.assertEqual(self.nodes["third"]["parents"], ["foreign-root"])
        self.assertEqual(self.equipo.registros.filter(drive_error=PENDIENTE).count(), 3)

    def test_colision_con_carpeta_ajena_no_sobrescribe_ni_mezcla(self):
        self.editar()
        fecha = self.nodes["third"]["parents"][0]
        self.nodes["ajena"] = dict(id="ajena", name="MANTENIMIENTO 001", parents=[fecha],
                                  mimeType="application/vnd.google-apps.folder")
        anteriores = deepcopy(self.nodes)
        self.api.files().update.reset_mock()
        self.client.post(self.url(self.registro), {"fecha": "2026-02-01"})
        self.assertEqual(self.nodes, anteriores)
        self.api.files().update.assert_not_called()
        self.assertEqual(self.equipo.registros.filter(drive_error=PENDIENTE).count(), 3)

    def test_celery_reorganiza_el_grupo_pendiente_sin_duplicados(self):
        original = self.api.files().update.side_effect
        self.api.files().update.side_effect = TimeoutError("Drive unavailable")
        self.editar()
        self.queue.assert_called_once_with(args=[self.tercero.pk], retry=False)
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.equipo.pk]))
        self.assertEqual(response.context["registros"], [])
        self.assertNotContains(response, 'id="historial-registros"')
        count = len(self.nodes)
        self.api.files().update.side_effect = original
        from .tasks import revisar_archivos_registro
        revisar_archivos_registro.run(self.tercero.pk)
        self.assertEqual(len(self.nodes), count)
        self.assertFalse(self.equipo.registros.exclude(drive_error="").exists())
        self.assert_layout()

    def test_editar_requiere_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url(), {"fecha": "2026-02-01"}).status_code, 403)
        self.tercero.refresh_from_db()
        self.assertEqual(self.tercero.fecha, date(2026, 5, 1))

    def test_migracion_renumera_historicos_sin_modificar_drive_ni_documentos(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        ids = dict(self.equipo.registros.values_list("pk", "drive_folder_id"))
        RegistroEquipo.objects.filter(pk=self.tercero.pk).update(fecha="2026-02-01")
        migration = import_module("masiscam.migrations.0014_orden_cronologico_registros")
        migration.numerar_por_fecha(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(self.equipo.registros.values_list("pk", "secuencia")),
                         [(self.registro.pk, 1), (self.tercero.pk, 2), (self.otro.pk, 3)])
        self.assertEqual(dict(self.equipo.registros.values_list("pk", "drive_folder_id")), ids)
        self.assertTrue(ArchivoRegistro.objects.filter(pk=self.documento.pk, registro=self.tercero).exists())
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_respuesta_perdida_al_crear_carpeta_se_recupera_por_clave(self):
        nuevo = RegistroEquipo.objects.create(equipo=self.equipo,tipo="NUEVO",fecha="2026-01-01")
        original = self.api.files().create.side_effect
        failed = []
        def create(body, **kwargs):
            call = original(body, **kwargs)
            def execute():
                result = call.execute()
                if body.get("appProperties") and not failed:
                    failed.append(True)
                    raise TimeoutError("lost response")
                return result
            return MagicMock(execute=execute)
        self.api.files().create.side_effect = create
        with self.assertRaises(TimeoutError):
            sincronizar_carpeta_registro(nuevo.pk)
        count = len(self.nodes)
        sincronizar_carpeta_registro(nuevo.pk)
        self.assertEqual(len(self.nodes), count)
        nuevo.refresh_from_db()
        self.assertEqual(self.nodes[nuevo.drive_folder_id]["name"], "NUEVO 001")

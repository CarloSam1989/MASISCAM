from copy import deepcopy
from io import StringIO
import re
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import Equipo, RegistroEquipo, Documento
from .services import GoogleDriveService
from .drive_rebuild import FOLDER


@override_settings(GOOGLE_DRIVE_ROOT_FOLDER_ID="root", GOOGLE_DRIVE_ENABLED=False)
class DriveRebuildTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_clientes import ClientesReductoresTests
        ClientesReductoresTests.setUpTestData.__func__(cls)
        # The legacy fixture has no customer and must be skipped.
        cls.equipo = Equipo.objects.create(proyecto=cls.proyecto, cliente=cls.cliente,
            nombre="EQ1", numero_serie="SERIE1", drive_folder_id="old", tipo_producto="REDUCTOR")

    def setUp(self):
        self.nodes = {}
        self.node("root", "MASISCAM")
        self.node("legacy", "Anterior", "root")
        self.node("old", "Equipo anterior", "legacy")
        self.node("photos", "01_Fotografias", "old")
        self.node("file", "foto.png", "photos", "image/png")
        self.api = MagicMock()
        self.api.files().get.side_effect = lambda fileId, **kw: self.result(lambda: deepcopy(self.nodes[fileId]))
        self.api.files().list.side_effect = self.list
        self.api.files().create.side_effect = self.create
        self.api.files().update.side_effect = self.update
        self.counter, self.fail = 0, None
        self.service = object.__new__(GoogleDriveService)
        self.service.drive, self.service.shared_drive_id = self.api, ""
        patcher = patch("masiscam.management.commands.regenerar_estructura_drive.GoogleDriveService", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def result(self, action):
        return MagicMock(execute=action)

    def node(self, id, name, parent=None, mime=FOLDER):
        self.nodes[id] = dict(id=id, name=name, mimeType=mime, parents=[parent] if parent else [],
                              webViewLink=f"https://drive.google.com/drive/folders/{id}", trashed=False)
        return id

    def list(self, q, **kwargs):
        parent = re.search(r"^'([^']+)' in parents", q).group(1)
        name = re.search(r"and name='([^']+)'", q)
        items = [deepcopy(n) for n in self.nodes.values() if n["parents"] == [parent]
                 and (not name or (n["name"] == name.group(1) and n["mimeType"] == FOLDER))]
        return self.result(lambda: {"files": items})

    def create(self, body, **kwargs):
        def action():
            self.counter += 1
            id = f"new-{self.counter}"
            self.node(id, body["name"], body["parents"][0], body["mimeType"])
            if self.fail == "create":
                self.fail = None
                raise TimeoutError("private credentials should never appear")
            return deepcopy(self.nodes[id])
        return self.result(action)

    def update(self, fileId, body, addParents=None, removeParents=None, **kwargs):
        def action():
            self.nodes[fileId].update(body)
            if addParents:
                self.nodes[fileId]["parents"] = [addParents]
            if self.fail == "update":
                self.fail = None
                raise TimeoutError("private credentials should never appear")
            return deepcopy(self.nodes[fileId])
        return self.result(action)

    def run_command(self, mode="--apply", error=False):
        out, err = StringIO(), StringIO()
        if error:
            with self.assertRaises(CommandError):
                call_command("regenerar_estructura_drive", mode, stdout=out, stderr=err)
        else:
            call_command("regenerar_estructura_drive", mode, stdout=out, stderr=err)
        self.api.files().delete.assert_not_called()
        self.api.files().copy.assert_not_called()
        return out.getvalue() + err.getvalue()

    def path(self, id):
        names = []
        while id:
            node = self.nodes[id]
            names.append(node["name"])
            id = node["parents"][0] if node["parents"] else None
        return list(reversed(names))

    def test_dry_run_does_not_write_drive_or_database(self):
        nodes = deepcopy(self.nodes)
        db = list(Equipo.objects.values())
        output = self.run_command("--dry-run")
        self.assertIn("DRY-RUN", output)
        self.assertIn("omitidos=1", output)
        self.assertIn("movidos=1", output)
        self.assertEqual(self.nodes, nodes)
        self.assertEqual(list(Equipo.objects.values()), db)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_move_preserves_ids_data_and_second_run_is_noop(self):
        before = Equipo.objects.filter(pk=self.equipo.pk).values().get()
        self.run_command()
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.drive_folder_id, "old")
        self.assertEqual(self.path("file"), ["MASISCAM", self.cliente.razon_social, "REDUCTOR", "SERIE1", "01_Fotografias", "foto.png"])
        after = Equipo.objects.filter(pk=self.equipo.pk).values().get()
        for field in before:
            if field not in {"drive_folder_id", "drive_folder_url", "drive_error"}:
                self.assertEqual(before[field], after[field], field)
        counts = (self.api.files().create.call_count, self.api.files().update.call_count)
        nodes = deepcopy(self.nodes)
        output = self.run_command()
        self.assertIn("movidos=0", output)
        self.assertEqual(self.nodes, nodes)
        self.assertEqual(counts, (self.api.files().create.call_count, self.api.files().update.call_count))

    def test_merge_existing_target_and_update_record_references(self):
        self.node("customer", self.cliente.razon_social, "root")
        self.node("type", "REDUCTOR", "customer")
        self.node("target", "SERIE1", "type")
        self.node("newphotos", "01_Fotografias", "target")
        self.node("another", "foto.png", "newphotos", "image/png")
        record = RegistroEquipo.objects.create(equipo=self.equipo, fecha="2026-09-22", tipo="NUEVO", drive_folder_id="photos")
        self.run_command()
        record.refresh_from_db()
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.drive_folder_id, "target")
        self.assertEqual(record.drive_folder_id, "newphotos")
        self.assertEqual(self.nodes["file"]["parents"], ["newphotos"])
        self.assertIn("another", self.nodes)  # Same name does not mean same document.
        self.api.files().create.assert_not_called()
        counts = self.api.files().update.call_count
        self.run_command()
        self.assertEqual(counts, self.api.files().update.call_count)

    def test_shared_legacy_folder_moves_only_owned_records_and_documents(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        second = Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente,
            nombre="EQ2", numero_serie="SERIE2", drive_folder_id="old", tipo_producto="BOMBA")
        registro = RegistroEquipo.objects.create(equipo=self.equipo, fecha="2026-09-22", tipo="NUEVO", drive_folder_id="photos")
        self.node("pdf", "manual.pdf", "old", "application/pdf")
        doc = Documento.objects.create(proyecto=self.proyecto, equipo=second, titulo="Manual",
            categoria="MANUALES", drive_file_id="pdf", archivo="manual.pdf", subido_por=self.usuario)
        self.run_command()
        self.equipo.refresh_from_db()
        second.refresh_from_db()
        doc.refresh_from_db()
        self.assertNotEqual(self.equipo.drive_folder_id, second.drive_folder_id)
        self.assertEqual(doc.drive_file_id, "pdf")
        self.assertEqual(self.path("pdf"), ["MASISCAM", self.cliente.razon_social, "BOMBA", "SERIE2", "04_Manuales", "manual.pdf"])
        self.assertEqual(self.nodes["photos"]["parents"], [self.equipo.drive_folder_id])
        counts = (self.api.files().create.call_count, self.api.files().update.call_count)
        self.run_command()
        self.assertEqual(counts, (self.api.files().create.call_count, self.api.files().update.call_count))

    def test_ambiguous_shared_content_is_reported_without_mutation(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        before = deepcopy(self.nodes)
        output = self.run_command(error=True)
        self.assertIn("Contenido sin propietario", output)
        self.assertEqual(before, self.nodes)
        self.api.files().create.assert_not_called()

    def test_recover_after_lost_move_response(self):
        self.fail = "update"
        output = self.run_command(error=True)
        self.assertNotIn("private credentials", output)
        self.run_command()
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.drive_folder_id, "old")
        self.assertEqual(len([n for n in self.nodes.values() if n["name"] == "SERIE1"]), 1)
        self.assertEqual(len([n for n in self.nodes.values() if n["name"] == "foto.png"]), 1)

    def test_recover_after_lost_create_response(self):
        self.fail = "create"
        self.run_command(error=True)
        self.run_command()
        self.assertEqual(len([n for n in self.nodes.values() if n["name"] == self.cliente.razon_social]), 1)
        self.assertEqual(self.path("old")[-2:], ["REDUCTOR", "SERIE1"])

    def test_skip_missing_series_and_customer(self):
        Equipo.objects.filter(pk=self.equipo.pk).update(numero_serie=" ")
        output = self.run_command()
        self.assertIn("omitidos=2", output)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_duplicate_destination_and_outside_root_fail_safely(self):
        self.equipo.drive_folder_id = "not-in-root"
        self.equipo.save()
        self.run_command(error=True)
        self.api.files().update.assert_not_called()
        self.api.files().create.assert_not_called()

    def test_modes_required_and_mutually_exclusive(self):
        with self.assertRaises(CommandError):
            call_command("regenerar_estructura_drive")
        with self.assertRaises(CommandError):
            call_command("regenerar_estructura_drive", "--apply", "--dry-run")

    def test_destination_conflicts_do_not_create_or_move_anything(self):
        self.node("customer", self.cliente.razon_social, "root")
        self.node("customer-duplicate", self.cliente.razon_social, "root")
        output = self.run_command(error=True)
        self.assertIn("varias carpetas destino", output)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_duplicate_equipment_paths_are_not_merged(self):
        Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente,
            nombre="EQ2", numero_serie="SERIE1", tipo_producto="REDUCTOR")
        output = self.run_command(error=True)
        self.assertIn("errores=2", output)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_merge_recovery_after_lost_response_updates_database(self):
        self.node("customer", self.cliente.razon_social, "root")
        self.node("type", "REDUCTOR", "customer")
        self.node("target", "SERIE1", "type")
        self.node("newphotos", "01_Fotografias", "target")
        registro = RegistroEquipo.objects.create(equipo=self.equipo, tipo="NUEVO", fecha="2026-09-22", drive_folder_id="photos")
        self.fail = "update"
        self.run_command(error=True)
        self.run_command()
        registro.refresh_from_db()
        self.equipo.refresh_from_db()
        self.assertEqual(registro.drive_folder_id, "newphotos")
        self.assertEqual(self.equipo.drive_folder_id, "target")
        self.assertEqual(self.nodes["file"]["parents"], ["newphotos"])
        self.assertEqual(len([n for n in self.nodes.values() if n["name"] == "foto.png"]), 1)

    def test_inactive_equipment_is_processed_and_data_preserved(self):
        Equipo.objects.filter(pk=self.equipo.pk).update(estado="INACTIVO", consulta_publica_activa=False)
        token = self.equipo.token_publico
        self.run_command()
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.estado, "INACTIVO")
        self.assertFalse(self.equipo.consulta_publica_activa)
        self.assertEqual(self.equipo.token_publico, token)
        self.assertEqual(self.path("old")[-2:], ["REDUCTOR", "SERIE1"])

    def test_missing_client_and_series_do_not_initialize_drive(self):
        Equipo.objects.filter(pk=self.equipo.pk).update(numero_serie="")
        with patch("masiscam.management.commands.regenerar_estructura_drive.GoogleDriveService") as factory:
            self.run_command("--dry-run")
            factory.assert_not_called()

    def test_snapshot_pagination_and_shared_drive(self):
        self.service.shared_drive_id = "shared-id"
        original = self.list
        def paginated(q, **kwargs):
            self.assertEqual(kwargs.get("driveId"), "shared-id")
            if "pageToken" not in kwargs:
                return self.result(lambda: {"files": [], "nextPageToken": "page-two"})
            return original(q, **kwargs)
        self.api.files().list.side_effect = paginated
        self.run_command("--dry-run")
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_shared_tree_preserves_internal_document_path(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        Documento.objects.create(proyecto=self.proyecto, equipo=self.equipo, titulo="Foto",
            categoria="FOTOGRAFIAS", drive_file_id="file", archivo="foto.png", subido_por=self.usuario)
        self.node("custom", "Inspeccion", "photos")
        self.nodes["file"]["parents"] = ["custom"]
        self.run_command()
        self.assertEqual(self.path("file"), ["MASISCAM", self.cliente.razon_social,
                         "REDUCTOR", "SERIE1", "01_Fotografias", "Inspeccion", "foto.png"])
        counts = (self.api.files().create.call_count, self.api.files().update.call_count)
        self.run_command()
        self.assertEqual(counts, (self.api.files().create.call_count, self.api.files().update.call_count))

    def test_equipment_without_saved_folder_reuses_correct_series(self):
        Equipo.objects.filter(pk=self.equipo.pk).update(drive_folder_id="")
        self.node("customer", self.cliente.razon_social, "root")
        self.node("type", "REDUCTOR", "customer")
        self.node("target", "SERIE1", "type")
        self.run_command()
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.drive_folder_id, "target")
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_ownership_diagnostic_lists_all_conflicts_without_writes(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        self.node("unowned", "sin_asignar.pdf", "old", "application/pdf")
        self.node("owned", "asignado.pdf", "old", "application/pdf")
        Documento.objects.create(proyecto=self.proyecto, equipo=self.equipo, titulo="Asignado",
            drive_file_id="owned", archivo="asignado.pdf", subido_por=self.usuario)
        before = deepcopy(self.nodes)
        equipos_before = list(Equipo.objects.values())
        docs_before = list(Documento.objects.values())
        output = self.run_command("--dry-run", error=True)
        for text in (f"DIAGNOSTICO SOLO LECTURA - Equipo #{self.equipo.pk}", self.cliente.razon_social,
                     "Tipo: REDUCTOR", 'Serie: "SERIE1"', 'ID carpeta: "old"',
                     'Carpeta Drive actual: "MASISCAM / Anterior / REDUCTORES"',
                     'Subcarpeta: "MASISCAM / Anterior / REDUCTORES / 01_Fotografias"',
                     'ID: "photos"', 'ID: "file"', 'ID: "unowned"', "Sin referencias directas en BD."):
            self.assertIn(text, output)
        self.assertNotIn('ID: "owned"', output)
        self.assertEqual(self.nodes, before)
        self.assertEqual(list(Equipo.objects.values()), equipos_before)
        self.assertEqual(list(Documento.objects.values()), docs_before)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_diagnostic_reports_other_equipment_and_shared_record_folders(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        second = Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="EQ2",
            numero_serie="SERIE2", tipo_producto="BOMBA", drive_folder_id="old")
        first_record = RegistroEquipo.objects.create(equipo=self.equipo, tipo="NUEVO", fecha="2026-09-22", drive_folder_id="photos")
        second_record = RegistroEquipo.objects.create(equipo=second, tipo="NUEVO", fecha="2026-09-22", drive_folder_id="photos")
        before = list(RegistroEquipo.objects.values())
        output = self.run_command("--dry-run", error=True)
        self.assertIn(f"DIAGNOSTICO SOLO LECTURA - Equipo #{self.equipo.pk}", output)
        self.assertIn(f"DIAGNOSTICO SOLO LECTURA - Equipo #{second.pk}", output)
        self.assertIn(f"Equipo #{second.pk} (OTRO EQUIPO)", output)
        self.assertIn('tipo=BOMBA; serie="SERIE2"', output)
        for record in (first_record, second_record):
            self.assertIn(f"RegistroEquipo.drive_folder_id (registro BD #{record.pk})", output)
        self.assertIn("Referencias por carpetas contenedoras", output)
        self.assertIn("corresponde a varios equipos", output)
        self.assertEqual(list(RegistroEquipo.objects.values()), before)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_diagnostic_reports_document_and_upload_references(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        second = Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="EQ2",
            numero_serie="SERIE2", tipo_producto="BOMBA")
        doc = Documento.objects.create(proyecto=self.proyecto, equipo=self.equipo, titulo="Uno",
            drive_file_id="file", archivo="uno.png", hash_sha256="a" * 64, subido_por=self.usuario)
        upload = Documento.objects.create(proyecto=self.proyecto, equipo=second, titulo="Dos",
            drive_upload_id="file", archivo="dos.png", hash_sha256="b" * 64, subido_por=self.usuario)
        output = self.run_command("--dry-run", error=True)
        self.assertIn(f"Documento.drive_file_id (registro BD #{doc.pk})", output)
        self.assertIn(f"Documento.drive_upload_id (registro BD #{upload.pk})", output)
        self.assertIn(f"Equipo #{second.pk} (OTRO EQUIPO)", output)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_diagnostic_identifies_documents_without_equipment(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        doc = Documento.objects.create(proyecto=self.proyecto, titulo="Sin equipo",
            drive_file_id="file", archivo="foto.png", subido_por=self.usuario)
        output = self.run_command("--dry-run", error=True)
        self.assertIn(f"Documento.drive_file_id (registro BD #{doc.pk}): sin equipo asociado.", output)
        self.assertIn("referencia de documento sin equipo asociado", output)

    def test_apply_does_not_run_diagnostics_or_change_error_decision(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        with patch("masiscam.management.commands.regenerar_estructura_drive.OwnershipDiagnostics") as diagnostic:
            output = self.run_command(error=True)
            diagnostic.assert_not_called()
        self.assertIn("Contenido sin propietario", output)
        self.assertNotIn("DIAGNOSTICO SOLO LECTURA", output)
        self.api.files().create.assert_not_called()
        self.api.files().update.assert_not_called()

    def test_diagnostic_names_escape_newlines(self):
        self.nodes["old"]["name"] = "REDUCTORES"
        self.nodes["file"]["name"] = "foto\nNO_ES_OTRA_LINEA.png"
        output = self.run_command("--dry-run", error=True)
        self.assertIn(r"foto\nNO_ES_OTRA_LINEA.png", output)
        self.assertNotIn("foto\nNO_ES_OTRA_LINEA.png", output)

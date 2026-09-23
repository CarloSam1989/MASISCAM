import re
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings

from .forms import FichaEquipoForm
from .models import Equipo, Proyecto
from .services import GoogleDriveService, sincronizar_carpeta_equipo


@override_settings(GOOGLE_DRIVE_ROOT_FOLDER_ID="root-masiscam")
class DriveEquipoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_clientes import ClientesReductoresTests
        ClientesReductoresTests.setUpTestData.__func__(cls)

    def setUp(self):
        # Simulate only the remote API; exercise real hierarchy and persistence code.
        self.archivos = {}
        self.fallar_despues = None
        self.api = MagicMock()
        self.api.files().list.side_effect = self.listar
        self.api.files().create.side_effect = self.crear
        self.servicio = object.__new__(GoogleDriveService)
        self.servicio.drive = self.api
        self.servicio.shared_drive_id = "shared-test"
        parche = patch("masiscam.services.GoogleDriveService", return_value=self.servicio)
        parche.start()
        self.addCleanup(parche.stop)

    def listar(self, **parametros):
        self.assertTrue(parametros["supportsAllDrives"])
        self.assertEqual(parametros["driveId"], "shared-test")
        q = parametros["q"]
        padre = re.search(r"^'([^']+)' in parents", q).group(1)
        nombre = re.search(r"name='([^']+)'", q).group(1)
        encontrados = [f for f in self.archivos.values() if f["parents"] == [padre] and f["name"] == nombre]
        return MagicMock(execute=lambda: {"files": encontrados})

    def crear(self, body, **parametros):
        def ejecutar():
            clave = f"folder-{len(self.archivos) + 1}"
            carpeta = dict(body, id=clave, webViewLink=f"https://drive.google.com/drive/folders/{clave}")
            self.archivos[clave] = carpeta
            if len(self.archivos) == self.fallar_despues:
                raise TimeoutError("Drive no respondio")
            return carpeta
        return MagicMock(execute=ejecutar)

    def nuevo(self, **datos):
        return Equipo.objects.create(**dict({"proyecto": self.proyecto, "cliente": self.cliente, "nombre": "REDUCTOR-001"}, **datos))

    def test_alta_formulario_encola_al_confirmar_y_edicion_no_encola(self):
        from .test_clientes import ClientesReductoresTests
        datos = ClientesReductoresTests.datos_reductor(self)
        with patch("masiscam.signals.encolar_carpeta_equipo", side_effect=sincronizar_carpeta_equipo) as encolar:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                form = FichaEquipoForm(datos, empresa=self.empresa)
                self.assertTrue(form.is_valid(), form.errors)
                equipo = form.save(usuario=self.usuario)
                encolar.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            encolar.assert_called_once_with(equipo.pk)
            equipo.refresh_from_db()
            self.assertTrue(equipo.drive_folder_id)
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                form = FichaEquipoForm(dict(datos, modelo="EDITADO"), empresa=self.empresa, equipo=equipo)
                self.assertTrue(form.is_valid(), form.errors)
                form.save(usuario=self.usuario)
                equipo.estado = Equipo.Estado.INACTIVO
                equipo.save()
            self.assertEqual(callbacks, [])
            self.assertEqual(encolar.call_count, 1)
            self.assertEqual(len(self.archivos), 3)

    def test_series_separadas_y_fallback_id(self):
        primero = self.nuevo(numero_serie="SERIE123")
        segundo = self.nuevo(nombre="REDUCTOR-002", numero_serie="SERIE456")
        uno = sincronizar_carpeta_equipo(primero.pk)
        dos = sincronizar_carpeta_equipo(segundo.pk)
        self.assertNotEqual(uno, dos)
        self.assertEqual([f["name"] for f in self.archivos.values()],
                         [self.cliente.razon_social,
                          "REDUCTOR", "SERIE123", "SERIE456"])
        self.assertEqual(self.archivos[uno]["parents"], self.archivos[dos]["parents"])
        self.assertEqual(sincronizar_carpeta_equipo(primero.pk), uno)
        vacio = self.nuevo()
        carpeta = sincronizar_carpeta_equipo(vacio.pk)
        self.assertEqual(self.archivos[carpeta]["name"], f"EQUIPO-{vacio.pk}")

    def test_fallo_drive_conserva_equipo_y_reintento_recupera_sin_duplicar(self):
        equipo = self.nuevo()
        token = equipo.token_publico
        self.fallar_despues = 2  # Remote folder exists, but its response was lost.
        with self.assertLogs("masiscam.services", level="ERROR"), self.assertRaises(TimeoutError):
            sincronizar_carpeta_equipo(equipo.pk)
        equipo.refresh_from_db()
        self.assertFalse(equipo.drive_folder_id)
        self.assertIn("No se pudo sincronizar", equipo.drive_error)
        self.assertEqual(equipo.token_publico, token)
        salida = StringIO()
        call_command("reintentar_drive_equipo", equipo.pk, stdout=salida)
        equipo.refresh_from_db()
        self.assertEqual(equipo.drive_folder_id, "folder-3")
        self.assertEqual(equipo.drive_error, "")
        self.assertEqual(len(self.archivos), 3)

    def test_fallo_cola_no_pierde_alta_y_registra_error(self):
        with patch("masiscam.tasks.crear_carpeta_equipo.apply_async", side_effect=ConnectionError("Cola no disponible")), self.assertLogs("masiscam.services", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                equipo = self.nuevo()
        equipo.refresh_from_db()
        self.assertIn("No se pudo sincronizar", equipo.drive_error)
        self.assertFalse(equipo.drive_folder_id)
        sincronizar_carpeta_equipo(equipo.pk)
        equipo.refresh_from_db()
        self.assertTrue(equipo.drive_folder_id)
        self.assertFalse(equipo.drive_error)

    def test_transaccion_cancelada_no_encola(self):
        with patch("masiscam.signals.encolar_carpeta_equipo") as encolar:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(ValueError), transaction.atomic():
                    self.nuevo()
                    raise ValueError("Cancelar")
            self.assertEqual(callbacks, [])
            encolar.assert_not_called()

    def test_razon_social_legacy_y_producto_fijo_y_empresa_validada(self):
        sincronizar_carpeta_equipo(self.antiguo.pk)
        self.assertIn(self.proyecto.razon_social, [f["name"] for f in self.archivos.values()])
        futuro = self.nuevo(tipo_producto="BOMBA", nombre="BOMBA-001")
        sincronizar_carpeta_equipo(futuro.pk)
        self.assertIn("BOMBA", [f["name"] for f in self.archivos.values()])
        self.assertEqual(sum(f["name"] == "REDUCTOR" for f in self.archivos.values()), 1)
        invalido = self.nuevo(cliente=self.ajeno)
        with self.assertLogs("masiscam.services", level="ERROR"), self.assertRaises(ValueError):
            sincronizar_carpeta_equipo(invalido.pk)

    def request_registro(self, nombre, datos=None, registro=None, equipo=None, post=True):
        from django.test import RequestFactory
        from django.urls import reverse, resolve
        from django.contrib.messages.storage.fallback import FallbackStorage
        kwargs = {"pk": equipo or self.antiguo.pk}
        if registro is not None:
            kwargs["registro_pk"] = registro.pk
        url = reverse("masiscam:" + nombre, kwargs=kwargs)
        request = (RequestFactory().post if post else RequestFactory().get)(url, datos or {})
        request.user, request.empresa_activa, request.session = self.usuario, self.empresa, {}
        request._messages = FallbackStorage(request)
        request.resolver_match = resolve(url)
        return request

    @patch("masiscam.views.EquipoDriveDocuments")
    def test_registros_tipos_historial_y_jerarquia(self, documentos):
        documentos.return_value.list.return_value = []
        import uuid
        from . import views
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        for tipo in RegistroEquipo.Tipo.values:
            with self.captureOnCommitCallbacks(execute=True), patch("masiscam.signals.encolar_carpeta_registro", side_effect=sincronizar_carpeta_registro):
                response = views.registro_crear(self.request_registro("registro_crear", {
                    "tipo": tipo, "fecha": "2026-09-15", "observacion": "Observación " + tipo,
                    "clave_creacion": str(uuid.uuid4()),
                }), self.antiguo.pk)
            self.assertEqual(response.status_code, 302)
        self.assertEqual(RegistroEquipo.objects.count(), 5)
        self.assertEqual(len(self.archivos), 8)
        self.antiguo.refresh_from_db()
        padre = next(f for f in self.archivos.values() if f["name"] == self.antiguo.numero_serie)
        self.assertEqual(padre["id"], self.antiguo.drive_folder_id)
        for registro in RegistroEquipo.objects.all():
            self.assertEqual(self.archivos[registro.drive_folder_id]["parents"], [padre["id"]])
            self.assertEqual(registro.tipo, self.archivos[registro.drive_folder_id]["name"])
        response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        for texto in ["Nuevo", "Asistencia", "Garantía", "Sin documentos", "Disponible", "registro-modal", "Editar ficha", "Ver informe", "Descargar QR", "Imprimir etiqueta"]:
            self.assertContains(response, texto)
        self.assertNotContains(response, "https://drive.google.com/")
        html = response.content.decode()
        tabla = html.split('id="historial-registros"', 1)[1].split('<dialog', 1)[0]
        self.assertNotIn("Historial / Registros", html)
        self.assertNotIn("Observación", tabla)
        for tipo in RegistroEquipo.Tipo.labels:
            self.assertIn('class="h5 mb-3">' + tipo + '</h2>', tabla)
        self.assertEqual(tabla.count("Sin documentos"), 5)
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="ASISTENCIA", fecha="2026-09-16")
        response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        tabla = response.content.decode().split('id="historial-registros"', 1)[1].split('<dialog', 1)[0]
        self.assertEqual(tabla.count('class="h5 mb-3">Asistencia</h2>'), 1)
        self.assertLess(tabla.index("16/09/2026"), tabla.index("15/09/2026"))
        self.assertIn("Sin documentos", tabla)

    def test_registro_doble_envio_y_edicion_no_duplican(self):
        import uuid
        from . import views
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        datos = {"tipo": "ASISTENCIA", "fecha": "2026-09-15", "clave_creacion": str(uuid.uuid4())}
        with patch("masiscam.signals.encolar_carpeta_registro", side_effect=sincronizar_carpeta_registro) as cola:
            with self.captureOnCommitCallbacks(execute=True):
                for _ in range(2):
                    self.assertEqual(views.registro_crear(self.request_registro("registro_crear", datos), self.antiguo.pk).status_code, 302)
            self.assertEqual(RegistroEquipo.objects.count(), 1)
            self.assertEqual(cola.call_count, 1)
            registro = RegistroEquipo.objects.get()
            carpeta = registro.drive_folder_id
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                registro.observacion = "Editado"
                registro.save()
            self.assertEqual(callbacks, [])
            self.assertEqual(sincronizar_carpeta_registro(registro.pk), carpeta)
            self.assertEqual(len(self.archivos), 4)

    def test_registro_fallo_drive_guarda_y_reintento_desde_ficha(self):
        import uuid
        from . import views
        from .models import RegistroEquipo
        self.fallar_despues = 3
        with self.assertLogs("masiscam.services", level="ERROR"), self.captureOnCommitCallbacks(execute=True):
            response = views.registro_crear(self.request_registro("registro_crear", {
                "tipo": "GARANTIA", "fecha": "2026-09-15", "clave_creacion": str(uuid.uuid4()),
            }), self.antiguo.pk)
        self.assertEqual(response.status_code, 302)
        registro = RegistroEquipo.objects.get()
        self.assertTrue(registro.drive_error)
        self.assertFalse(registro.drive_folder_id)
        views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        registro.refresh_from_db()
        self.assertEqual(registro.drive_folder_id, "folder-4")
        self.assertFalse(registro.drive_error)
        self.assertEqual(len(self.archivos), 4)

    def test_registro_drive_caido_y_equipo_sin_drive(self):
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        with patch("masiscam.services.sincronizar_carpeta_registro", side_effect=ConnectionError("Drive caido")):
            with self.assertLogs("masiscam.services", level="ERROR"), self.captureOnCommitCallbacks(execute=True):
                registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="NUEVO", fecha="2026-09-15")
        registro.refresh_from_db()
        self.assertIn("No se pudo sincronizar", registro.drive_error)
        self.fallar_despues = 1
        with self.assertRaises(TimeoutError), self.assertLogs("masiscam.services", level="ERROR"):
            sincronizar_carpeta_registro(registro.pk)
        registro.refresh_from_db()
        self.antiguo.refresh_from_db()
        self.assertTrue(registro.drive_error)
        self.assertTrue(self.antiguo.drive_error)
        sincronizar_carpeta_registro(registro.pk)
        registro.refresh_from_db()
        self.assertTrue(registro.drive_folder_id)
        self.assertEqual(len(self.archivos), 4)

    def test_registro_valida_datos_y_empresa_y_rol(self):
        import uuid
        from django.http import Http404
        from django.core.exceptions import PermissionDenied
        from . import views
        from .models import RegistroEquipo, RolMasiscam
        datos = {"tipo": "INVALIDO", "fecha": "incorrecta", "clave_creacion": str(uuid.uuid4())}
        response = views.registro_crear(self.request_registro("registro_crear", datos), self.antiguo.pk)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'data-open="true"', status_code=400)
        self.assertFalse(RegistroEquipo.objects.exists())
        request = self.request_registro("registro_crear", datos)
        request.empresa_activa = self.otra
        request.user.is_superuser = True
        with self.assertRaises(Http404):
            views.registro_crear(request, self.antiguo.pk)
        self.usuario.is_superuser = False
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol=RolMasiscam.Rol.CONSULTA)
        with self.assertRaises(PermissionDenied):
            views.registro_crear(self.request_registro("registro_crear", datos), self.antiguo.pk)
        response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        self.assertNotContains(response, 'id="registro-abrir"')
        self.assertContains(response, "Este equipo aún no tiene registros.")

    def test_informe_comparte_registros_con_ficha_y_oculta_vacios(self):
        from html.parser import HTMLParser
        from . import views
        from .models import RegistroEquipo
        self.antiguo.consulta_publica_activa = True
        self.antiguo.save()
        request = self.request_registro("ficha_detalle", post=False)
        vacio = views.equipo_publico(request, self.antiguo.token_publico)
        self.assertNotContains(vacio, 'id="historial-registros"')
        for indice, tipo in enumerate(["NUEVO", "ASISTENCIA", "GARANTIA", "REVISION"]):
            RegistroEquipo.objects.create(
                equipo=self.antiguo, tipo=tipo, fecha=f"2026-09-{15 + indice}", observacion="NO MOSTRAR OBSERVACION",
                drive_folder_id="folder-test" if indice == 0 else "",
                drive_folder_url="https://drive.google.com/drive/folders/folder-test" if indice == 0 else "",
                drive_error="Error simulado" if indice == 1 else "",
            )
        ficha = views.ficha_detalle(request, self.antiguo.pk)
        informe = views.equipo_publico(request, self.antiguo.token_publico)
        class Tablas(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tabla = False
                self.celda = None
                self.filas = []
            def handle_starttag(self, tag, attrs):
                if tag == "table":
                    self.tabla = "ficha-registros" in dict(attrs).get("class", "")
                if self.tabla and tag == "tr":
                    self.fila = []
                if self.tabla and tag == "td":
                    self.celda = []
            def handle_data(self, data):
                if self.celda is not None:
                    self.celda.append(data)
            def handle_endtag(self, tag):
                if self.tabla and tag == "td":
                    self.fila.append(" ".join("".join(self.celda).split()))
                    self.celda = None
                if self.tabla and tag == "tr" and self.fila:
                    self.filas.append(self.fila[:4])
                if tag == "table":
                    self.tabla = False
        interna, externa = Tablas(), Tablas()
        interna.feed(ficha.content.decode())
        externa.feed(informe.content.decode())
        self.assertEqual(interna.filas, externa.filas)
        self.assertEqual(len(externa.filas), 4)
        for tipo in ["Nuevo", "Asistencia", "Garantía", "REVISION"]:
            self.assertContains(informe, 'class="h5 mb-3">' + tipo + '</h2>')
        self.assertNotContains(informe, "https://drive.google.com/")
        self.assertNotContains(informe, "NO MOSTRAR OBSERVACION")
        self.assertNotContains(informe, "Reintentar Drive")
        self.assertNotContains(informe, "registro-modal")
        self.assertNotContains(informe, '<th scope="col">Acciones</th>')
        html = informe.content.decode()
        self.assertLess(html.index("Tipo de aceite"), html.index('id="historial-registros"'))
        self.assertLess(html.index('id="historial-registros"'), html.index('class="placa-informe"'))
        self.antiguo.consulta_publica_activa = False
        self.antiguo.save()
        self.assertNotContains(views.equipo_publico(request, self.antiguo.token_publico), 'id="historial-registros"')

    def test_registros_separados_y_reutiliza_id_antiguo_sin_mover(self):
        from copy import deepcopy
        from datetime import date
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        equipos = [self.nuevo(numero_serie=serie) for serie in ("SERIE1", "SERIE2")]
        carpetas = []
        for equipo in equipos:
            registro = RegistroEquipo.objects.create(equipo=equipo, tipo="NUEVO", fecha=date(2026, 9, 16))
            carpetas.append(sincronizar_carpeta_registro(registro.pk))
            equipo.refresh_from_db()
            self.assertEqual(self.archivos[carpetas[-1]]["parents"], [equipo.drive_folder_id])
        self.assertNotEqual(*carpetas)
        equipo = equipos[0]
        Equipo.objects.filter(pk=equipo.pk).update(drive_folder_id="antigua")
        anteriores = deepcopy(self.archivos)
        nuevo = RegistroEquipo.objects.create(equipo=equipo, tipo="REPARACION", fecha=date(2026, 9, 18))
        nueva = sincronizar_carpeta_registro(nuevo.pk)
        self.assertEqual(self.archivos[nueva]["parents"], ["antigua"])
        for pk, archivo in anteriores.items():
            self.assertEqual(self.archivos[pk], archivo)
        equipo.refresh_from_db()
        self.assertEqual(self.servicio.crear_estructura_equipo(equipo)["id"], "antigua")
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()

    def test_documentos_se_guardan_bajo_su_serie(self):
        from types import SimpleNamespace
        from googleapiclient.errors import HttpError
        from httplib2 import Response
        padres = []
        self.api.files().get.return_value.execute.side_effect = HttpError(Response({"status": "404"}), b"missing")
        for serie in ("DOC-SER1", "DOC-SER2"):
            equipo = self.nuevo(numero_serie=serie)
            documento = SimpleNamespace(
                equipo_id=equipo.pk, drive_file_id="", drive_upload_id="upload-" + serie,
                categoria="INFORMES", archivo=SimpleNamespace(path="unused"),
                tipo_mime="application/pdf", nombre_original="informe.pdf",
            )
            with patch("googleapiclient.http.MediaFileUpload"):
                self.servicio.subir_documento(documento)
            archivo = self.api.files().create.call_args.kwargs["body"]
            categoria = self.archivos[archivo["parents"][0]]
            carpeta_serie = self.archivos[categoria["parents"][0]]
            self.assertEqual(carpeta_serie["name"], serie)
            padres.append(carpeta_serie["id"])
        self.assertNotEqual(*padres)

    def test_empresas_homonimas_no_comparten_carpeta(self):
        self.otra.nombre = self.empresa.nombre
        self.otra.save()
        proyecto = Proyecto.objects.create(
            empresa=self.otra, codigo="OTRO", nombre="Otro", responsable="R",
            fecha_inicio=self.proyecto.fecha_inicio, creado_por=self.usuario,
        )
        equipos = [self.nuevo(numero_serie="IGUAL"),
                   self.nuevo(proyecto=proyecto, cliente=self.ajeno, numero_serie="IGUAL")]
        ids = [sincronizar_carpeta_equipo(equipo.pk) for equipo in equipos]
        self.assertNotEqual(*ids)

    def test_tipos_comparten_cliente_y_reutilizan_estructura_existente(self):
        nombre = self.cliente.razon_social
        padre = self.servicio.obtener_carpeta_equipo(nombre, "root-masiscam")["id"]
        tipo = self.servicio.obtener_carpeta_equipo("REDUCTOR", padre)["id"]
        serie = self.servicio.obtener_carpeta_equipo("SERIE123", tipo)["id"]
        reductor = self.nuevo(numero_serie="SERIE123")
        self.assertEqual(sincronizar_carpeta_equipo(reductor.pk), serie)
        self.assertEqual(len(self.archivos), 3)
        bomba = self.nuevo(tipo_producto="BOMBA", numero_serie="SERIE456")
        carpeta = sincronizar_carpeta_equipo(bomba.pk)
        tipo_bomba = self.archivos[self.archivos[carpeta]["parents"][0]]
        self.assertEqual(tipo_bomba["name"], "BOMBA")
        self.assertEqual(tipo_bomba["parents"], [padre])
        self.assertEqual(len(self.archivos), 5)

    def test_sanitizacion_reintentos_y_colisiones_de_series(self):
        from .services import nombre_carpeta_drive
        self.cliente.razon_social = "Cliente / especial?"
        self.cliente.save()
        ids = []
        for serie in ("SER/123", "SER?123"):
            equipo = self.nuevo(numero_serie=serie)
            carpeta = sincronizar_carpeta_equipo(equipo.pk)
            self.assertEqual(self.archivos[carpeta]["name"], nombre_carpeta_drive(serie))
            self.assertEqual(sincronizar_carpeta_equipo(equipo.pk), carpeta)
            ids.append(carpeta)
        self.assertNotEqual(*ids)
        self.assertEqual(len(self.archivos), 4)
        for carpeta in self.archivos.values():
            self.assertNotRegex(carpeta["name"], r"[/\\?]")

    def test_regenerar_y_desactivar_token_no_crean_carpetas(self):
        from . import views
        equipo = self.nuevo(numero_serie="SERIE-TOKEN", consulta_publica_activa=True)
        carpeta = sincronizar_carpeta_equipo(equipo.pk)
        antes = self.archivos.copy()
        for action in ("equipo_token_regenerar", "equipo_publico_desactivar"):
            with patch("masiscam.signals.encolar_carpeta_equipo") as cola:
                with self.captureOnCommitCallbacks(execute=True):
                    request = self.request_registro(action, equipo=equipo.pk)
                    self.assertEqual(getattr(views, action)(request, equipo.pk).status_code, 302)
                cola.assert_not_called()
            equipo.refresh_from_db()
            self.assertEqual(equipo.drive_folder_id, carpeta)
            self.assertEqual(sincronizar_carpeta_equipo(equipo.pk), carpeta)
            self.assertEqual(self.archivos, antes)
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()

    def test_five_equipment_and_repeated_records_have_independent_folders(self):
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        registros = []
        for index in range(5):
            equipo = self.nuevo(nombre=f"EQ-{index}", numero_serie=f"SER-{index}")
            with self.captureOnCommitCallbacks(execute=True), patch(
                "masiscam.signals.encolar_carpeta_registro", side_effect=sincronizar_carpeta_registro
            ):
                registro = RegistroEquipo.objects.create(equipo=equipo, tipo="MANTENIMIENTO", fecha="2026-09-23")
            registro.refresh_from_db()
            equipo.refresh_from_db()
            self.assertEqual(self.archivos[registro.drive_folder_id]["parents"], [equipo.drive_folder_id])
            self.assertEqual(self.archivos[registro.drive_folder_id]["name"], "MANTENIMIENTO")
            registros.append(registro)
        self.assertEqual(len({r.drive_folder_id for r in registros}), 5)
        segundo = RegistroEquipo.objects.create(equipo=registros[0].equipo, tipo="MANTENIMIENTO", fecha="2026-09-23")
        folder = sincronizar_carpeta_registro(segundo.pk)
        self.assertNotEqual(folder, registros[0].drive_folder_id)
        count = len(self.archivos)
        self.assertEqual(sincronizar_carpeta_registro(segundo.pk), folder)
        self.assertEqual(len(self.archivos), count)
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()

    @override_settings(GOOGLE_DRIVE_ENABLED=True)
    def test_record_folder_created_immediately_after_commit(self):
        from .models import RegistroEquipo
        with self.captureOnCommitCallbacks(execute=True):
            registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO", fecha="2026-09-23")
        registro.refresh_from_db()
        self.antiguo.refresh_from_db()
        self.assertTrue(registro.drive_folder_id)
        self.assertEqual(self.archivos[registro.drive_folder_id]["name"], "MANTENIMIENTO")
        self.assertEqual(self.archivos[registro.drive_folder_id]["parents"], [self.antiguo.drive_folder_id])

    @override_settings(GOOGLE_DRIVE_ENABLED=True)
    @patch("masiscam.views.EquipoDriveDocuments")
    def test_loading_repairs_missing_record_folder(self, documents):
        from . import views
        from .models import RegistroEquipo
        documents.return_value.list.return_value = []
        registro = RegistroEquipo.objects.create(equipo=self.antiguo, tipo="MANTENIMIENTO", fecha="2026-09-23")
        response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        registro.refresh_from_db()
        self.assertTrue(registro.drive_folder_id)
        self.assertEqual(self.archivos[registro.drive_folder_id]["name"], "MANTENIMIENTO")
        self.assertContains(response, "Sin documentos")
        total = len(self.archivos)
        views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        self.assertEqual(len(self.archivos), total)

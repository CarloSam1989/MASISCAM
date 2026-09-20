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
            self.assertEqual(len(self.archivos), 2)

    def test_jerarquia_razon_social_y_reductores_compartidos(self):
        primero = self.nuevo()
        segundo = self.nuevo(nombre="REDUCTOR-002")
        uno = sincronizar_carpeta_equipo(primero.pk)
        dos = sincronizar_carpeta_equipo(segundo.pk)
        self.assertEqual(uno, dos)
        self.assertEqual(len(self.archivos), 2)
        self.assertEqual([f["name"] for f in self.archivos.values()], [self.cliente.razon_social, "REDUCTORES"])
        self.assertEqual(self.archivos["folder-1"]["parents"], ["root-masiscam"])
        self.assertEqual(self.archivos[uno]["parents"], ["folder-1"])
        self.assertEqual(sincronizar_carpeta_equipo(primero.pk), uno)
        primero.refresh_from_db()
        self.assertEqual(primero.drive_folder_url, self.archivos[uno]["webViewLink"])

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
        self.assertEqual(equipo.drive_folder_id, "folder-2")
        self.assertEqual(equipo.drive_error, "")
        self.assertEqual(len(self.archivos), 2)

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
        self.assertNotIn("BOMBA", [f["name"] for f in self.archivos.values()])
        self.assertEqual(sum(f["name"] == "REDUCTORES" for f in self.archivos.values()), 2)
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

    def test_registros_tipos_historial_y_jerarquia(self):
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
        self.assertEqual(len(self.archivos), 7)
        self.antiguo.refresh_from_db()
        padre = next(f for f in self.archivos.values() if f["name"] == "REDUCTORES")
        self.assertEqual(padre["id"], self.antiguo.drive_folder_id)
        for registro in RegistroEquipo.objects.all():
            self.assertEqual(self.archivos[registro.drive_folder_id]["parents"], [padre["id"]])
            self.assertIn("2026-09-15 - " + registro.get_tipo_display(), self.archivos[registro.drive_folder_id]["name"])
        response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        for texto in ["Nuevo", "Asistencia", "Garantía", "Abrir carpeta", "Disponible", "registro-modal", "Editar ficha", "Ver informe", "Descargar QR", "Imprimir etiqueta"]:
            self.assertContains(response, texto)
        self.assertContains(response, RegistroEquipo.objects.first().drive_folder_url)
        html = response.content.decode()
        tabla = html.split('id="historial-registros"', 1)[1].split('<dialog', 1)[0]
        self.assertNotIn("Historial / Registros", html)
        self.assertNotIn("Observación", tabla)
        for tipo in RegistroEquipo.Tipo.labels:
            self.assertIn('class="h5 mb-3">' + tipo + '</h2>', tabla)
        self.assertEqual(tabla.count("Abrir carpeta</a>"), 5)
        RegistroEquipo.objects.create(equipo=self.antiguo, tipo="ASISTENCIA", fecha="2026-09-16")
        response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
        tabla = response.content.decode().split('id="historial-registros"', 1)[1].split('<dialog', 1)[0]
        self.assertEqual(tabla.count('class="h5 mb-3">Asistencia</h2>'), 1)
        self.assertLess(tabla.index("16/09/2026"), tabla.index("15/09/2026"))
        self.assertIn("Pendiente", tabla)

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
            self.assertEqual(len(self.archivos), 3)

    def test_registro_fallo_drive_guarda_y_reintento_desde_ficha(self):
        import uuid
        from . import views
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        self.fallar_despues = 3
        def enviar(args, **kwargs):
            return sincronizar_carpeta_registro(*args)
        with patch("masiscam.tasks.crear_carpeta_registro.apply_async", side_effect=enviar):
            with self.assertLogs("masiscam.services", level="ERROR"), self.captureOnCommitCallbacks(execute=True):
                response = views.registro_crear(self.request_registro("registro_crear", {
                    "tipo": "GARANTIA", "fecha": "2026-09-15", "clave_creacion": str(uuid.uuid4()),
                }), self.antiguo.pk)
            self.assertEqual(response.status_code, 302)
            registro = RegistroEquipo.objects.get()
            self.assertTrue(registro.drive_error)
            self.assertFalse(registro.drive_folder_id)
            response = views.ficha_detalle(self.request_registro("ficha_detalle", post=False), self.antiguo.pk)
            self.assertContains(response, "Reintentar Drive")
            self.assertContains(response, "Error de Drive")
            response = views.registro_reintentar(self.request_registro("registro_reintentar", registro=registro), self.antiguo.pk, registro.pk)
            self.assertEqual(response.status_code, 302)
        registro.refresh_from_db()
        self.assertEqual(registro.drive_folder_id, "folder-3")
        self.assertFalse(registro.drive_error)
        self.assertEqual(len(self.archivos), 3)

    def test_registro_cola_caida_y_equipo_sin_drive(self):
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        with patch("masiscam.tasks.crear_carpeta_registro.apply_async", side_effect=ConnectionError("Cola caída")):
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
        self.assertEqual(len(self.archivos), 3)

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
        self.assertContains(informe, 'href="https://drive.google.com/drive/folders/folder-test"')
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

    def test_registros_estructura_exacta_reutiliza_y_no_toca_carpetas_antiguas(self):
        from copy import deepcopy
        from datetime import date
        from .models import RegistroEquipo
        from .services import sincronizar_carpeta_registro
        equipo = self.nuevo()
        primero = RegistroEquipo.objects.create(equipo=equipo, tipo="NUEVO", fecha=date(2026, 9, 16))
        carpeta = sincronizar_carpeta_registro(primero.pk)
        self.assertEqual([f["name"] for f in self.archivos.values()], [self.cliente.razon_social, "REDUCTORES", "2026-09-16 - Nuevo"])
        self.assertEqual([f["parents"] for f in self.archivos.values()], [["root-masiscam"], ["folder-1"], ["folder-2"]])
        otro_equipo = self.nuevo(nombre="OTRO")
        segundo = RegistroEquipo.objects.create(equipo=otro_equipo, tipo="ASISTENCIA", fecha=date(2026, 9, 17))
        sincronizar_carpeta_registro(segundo.pk)
        self.assertEqual(len(self.archivos), 4)
        self.assertEqual(self.archivos["folder-4"]["parents"], ["folder-2"])
        repetido = RegistroEquipo.objects.create(equipo=otro_equipo, tipo="NUEVO", fecha=date(2026, 9, 16))
        self.assertEqual(sincronizar_carpeta_registro(repetido.pk), carpeta)
        self.assertEqual(len(self.archivos), 4)
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()
        # Reutilizar carpetas creadas manualmente, sin etiquetas de la aplicacion.
        for archivo in self.archivos.values():
            archivo.pop("appProperties", None)
        self.assertEqual(self.servicio.crear_estructura_equipo(equipo)["id"], "folder-2")
        self.assertEqual(len(self.archivos), 4)
        # El ID previo del equipo no debe introducir niveles viejos en registros nuevos.
        Equipo.objects.filter(pk=equipo.pk).update(drive_folder_id="antigua", drive_folder_url="https://drive.google.com/drive/folders/antigua")
        self.archivos["antigua"] = {"id": "antigua", "name": "EQUIPO ANTIGUO", "parents": ["padre-antiguo"]}
        anteriores = deepcopy(self.archivos)
        nuevo = RegistroEquipo.objects.create(equipo=equipo, tipo="REPARACION", fecha=date(2026, 9, 18))
        nueva = sincronizar_carpeta_registro(nuevo.pk)
        self.assertEqual(self.archivos[nueva]["parents"], ["folder-2"])
        for pk, archivo in anteriores.items():
            self.assertEqual(self.archivos[pk], archivo)
        equipo.refresh_from_db()
        self.assertEqual(equipo.drive_folder_id, "antigua")
        self.api.files().update.assert_not_called()
        self.api.files().delete.assert_not_called()

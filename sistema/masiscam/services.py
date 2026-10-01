import hashlib
import logging
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

logger = logging.getLogger(__name__)

SUBCARPETAS = ["01_Fotografias", "02_Equipos", "03_Planos", "04_Manuales", "05_Informes", "06_Certificados", "07_Mantenimientos", "08_Otros"]
CATEGORIA_CARPETA = {"FOTOGRAFIAS": "01_Fotografias", "PLACAS": "02_Equipos", "PLANOS": "03_Planos", "MANUALES": "04_Manuales", "INFORMES": "05_Informes", "CERTIFICADOS": "06_Certificados", "MANTENIMIENTOS": "07_Mantenimientos"}


def nombre_seguro(texto):
    return re.sub(r"[^\w. -]+", "_", texto, flags=re.UNICODE).strip()[:180]


def nombre_carpeta_drive(texto):
    original = texto.strip()
    seguro = nombre_seguro(original).strip() or "SIN-NOMBRE"
    if seguro in {".", ".."}:
        seguro = "SIN-NOMBRE"
    # Distinct series must not collapse into the same name after sanitization.
    if seguro != original:
        seguro = seguro[:169] + "-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
    return seguro


class GoogleDriveService:
    def __init__(self):
        if not settings.GOOGLE_DRIVE_ENABLED or not settings.GOOGLE_DRIVE_CREDENTIALS_FILE or not settings.GOOGLE_DRIVE_ROOT_FOLDER_ID:
            raise ImproperlyConfigured("Google Drive no está configurado; defina GOOGLE_DRIVE_CREDENTIALS_FILE y GOOGLE_DRIVE_ROOT_FOLDER_ID.")
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        credenciales = service_account.Credentials.from_service_account_file(settings.GOOGLE_DRIVE_CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/drive"])
        self.drive = build("drive", "v3", credentials=credenciales, cache_discovery=False)
        self.shared_drive_id = settings.GOOGLE_DRIVE_SHARED_DRIVE_ID

    def _crear_carpeta(self, nombre, padre):
        datos = {"name": nombre_seguro(nombre), "mimeType": "application/vnd.google-apps.folder", "parents": [padre]}
        return self.drive.files().create(body=datos, fields="id,webViewLink", supportsAllDrives=True).execute()

    def crear_estructura_proyecto(self, proyecto):
        carpeta = self.obtener_carpeta_equipo(f"{proyecto.codigo} - {proyecto.nombre}", settings.GOOGLE_DRIVE_ROOT_FOLDER_ID)
        subcarpetas = {nombre: self.obtener_carpeta_equipo(nombre, carpeta["id"])["id"] for nombre in SUBCARPETAS}
        return carpeta, subcarpetas

    def obtener_carpeta_equipo(self, nombre, padre):
        # Reutilizar por nombre dentro del padre, incluso carpetas sin appProperties.
        def escapar(valor):
            return valor.replace("\\", "\\\\").replace("'", "\\'")

        parametros = {
            "q": (f"'{escapar(padre)}' in parents and trashed=false "
                  "and mimeType='application/vnd.google-apps.folder' "
                  f"and name='{escapar(nombre)}'"),
            "fields": "nextPageToken,files(id,webViewLink)",
            "supportsAllDrives": True, "includeItemsFromAllDrives": True,
        }
        if self.shared_drive_id:
            parametros.update(corpora="drive", driveId=self.shared_drive_id)
        while True:
            respuesta = self.drive.files().list(**parametros).execute()
            if respuesta.get("files"):
                return respuesta["files"][0]
            if not respuesta.get("nextPageToken"):
                break
            parametros["pageToken"] = respuesta["nextPageToken"]
        return self.drive.files().create(
            body={"name": nombre, "mimeType": "application/vnd.google-apps.folder",
                  "parents": [padre]},
            fields="id,webViewLink", supportsAllDrives=True,
        ).execute()

    def crear_estructura_equipo(self, equipo):
        empresa = equipo.proyecto.empresa
        if equipo.cliente_id and equipo.cliente.empresa_id != empresa.pk:
            raise ValueError("El cliente del equipo no pertenece a su empresa.")
        if equipo.drive_folder_id:
            return {"id": equipo.drive_folder_id, "webViewLink": equipo.drive_folder_url}
        cliente = equipo.razon_social_cliente.strip()
        if not cliente or equipo.tipo_producto not in {"REDUCTOR", "BOMBA"}:
            raise ValueError("El equipo requiere razon social y un tipo de producto valido.")
        niveles = [cliente, equipo.tipo_producto,
                   equipo.numero_serie.strip() or f"EQUIPO-{equipo.pk}"]
        padre = settings.GOOGLE_DRIVE_ROOT_FOLDER_ID
        for nombre in niveles:
            carpeta = self.obtener_carpeta_equipo(nombre_carpeta_drive(nombre), padre)
            padre = carpeta["id"]
        return carpeta

    def buscar_subcarpeta(self, proyecto, nombre):
        return self.obtener_carpeta_equipo(nombre, proyecto.drive_folder_id)["id"]

    def crear_estructura_registro(self, registro, equipo_folder_id):
        from .models import Equipo, RegistroEquipo
        if Equipo.objects.exclude(pk=registro.equipo_id).filter(drive_folder_id=equipo_folder_id).exists():
            raise ValueError("La carpeta del equipo esta compartida; requiere revision manual.")
        # The caller holds the existing database lock throughout allocation and save.
        # Include trash so deleting a record does not immediately reuse its number.
        carpetas = self.listar_carpetas(equipo_folder_id)
        clave = str(registro.clave_creacion)
        propias = [f for f in carpetas if not f.get("trashed")
                   and f.get("appProperties", {}).get("masiscam_registro") == clave]
        if len(propias) > 1:
            raise ValueError("El registro tiene varias carpetas; requiere revision manual.")
        if propias:
            carpeta = propias[0]  # Recover a successful create whose response was lost.
        else:
            nombre = registro.get_tipo_display()
            if registro.secuencia is None:
                raise ValueError("Reserve la secuencia antes de crear la carpeta.")
            carpeta = self.drive.files().create(
                body={"name": f"{registro.fecha} - {nombre} {registro.secuencia:03d}",
                      "mimeType": "application/vnd.google-apps.folder", "parents": [equipo_folder_id],
                      "appProperties": {"masiscam_registro": clave}},
                fields="id,webViewLink", supportsAllDrives=True,
            ).execute()
        if RegistroEquipo.objects.exclude(pk=registro.pk).filter(drive_folder_id=carpeta["id"]).exists():
            raise ValueError("La carpeta del registro esta compartida; requiere revision manual.")
        return carpeta

    def listar_carpetas(self, padre):
        padre = padre.replace("\\", "\\\\").replace("'", "\\'")
        params = {"q": f"'{padre}' in parents and mimeType='application/vnd.google-apps.folder'",
                  "fields": "nextPageToken,incompleteSearch,files(id,name,parents,trashed,appProperties,webViewLink)",
                  "pageSize": 1000, "supportsAllDrives": True, "includeItemsFromAllDrives": True}
        if self.shared_drive_id:
            params.update(corpora="drive", driveId=self.shared_drive_id)
        carpetas, paginas = [], set()
        while True:
            respuesta = self.drive.files().list(**params).execute()
            if respuesta.get("incompleteSearch"):
                raise ValueError("Drive no pudo listar todas las carpetas.")
            carpetas.extend(respuesta.get("files", []))
            token = respuesta.get("nextPageToken")
            if not token:
                return carpetas
            if token in paginas:
                raise ValueError("Drive devolvio una paginacion repetida.")
            paginas.add(token)
            params["pageToken"] = token

    def validar_carpeta_registro_eliminable(self, registro):
        from .models import Equipo, Proyecto, RegistroEquipo
        carpeta_id = registro.drive_folder_id
        equipo = registro.equipo
        if not carpeta_id or not equipo.drive_folder_id:
            raise ValueError("El registro no tiene una carpeta de Drive valida.")
        protegidas = {settings.GOOGLE_DRIVE_ROOT_FOLDER_ID}
        protegidas.update(Equipo.objects.exclude(drive_folder_id="").values_list("drive_folder_id", flat=True))
        protegidas.update(Proyecto.objects.exclude(drive_folder_id="").values_list("drive_folder_id", flat=True))
        protegidas.update(RegistroEquipo.objects.exclude(pk=registro.pk).exclude(
            drive_folder_id="").values_list("drive_folder_id", flat=True))
        if (carpeta_id in protegidas or Equipo.objects.exclude(pk=equipo.pk).filter(
                drive_folder_id=equipo.drive_folder_id).exists()):
            raise ValueError("La carpeta esta compartida o protegida; requiere revision manual.")
        carpeta = self.drive.files().get(fileId=carpeta_id, fields="id,mimeType,parents,trashed",
                                        supportsAllDrives=True).execute()
        if (carpeta.get("mimeType") != "application/vnd.google-apps.folder"
                or carpeta.get("parents") != [equipo.drive_folder_id]):
            raise ValueError("La carpeta no pertenece directamente a este equipo.")
        pendientes, vistas = [carpeta_id], set()
        while pendientes:
            actual = pendientes.pop()
            if actual in protegidas or actual in vistas or len(vistas) >= 2000:
                raise ValueError("La carpeta contiene referencias protegidas; requiere revision manual.")
            vistas.add(actual)
            pendientes.extend(f["id"] for f in self.listar_carpetas(actual))
        return carpeta

    def reservar_documento(self, documento_id):
        """Commit the remote ID before any upload, independently of final DB save."""
        from .models import Documento
        with transaction.atomic():
            documento = Documento.objects.select_for_update().get(pk=documento_id)
            if documento.drive_file_id:
                return documento.drive_file_id
            if not documento.drive_upload_id:
                documento.drive_upload_id = self.drive.files().generateIds(count=1, space="drive", type="files").execute()["ids"][0]
                documento.save(update_fields=["drive_upload_id"])
            return documento.drive_upload_id

    def subir_documento(self, documento):
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        remote_id = documento.drive_file_id or documento.drive_upload_id
        if not remote_id:
            raise ValueError("Reservar ID de carga antes de subir el documento.")
        fields = "id,webViewLink,webContentLink"
        try:
            return self.drive.files().get(fileId=remote_id, fields=fields, supportsAllDrives=True).execute()
        except HttpError as exc:
            if exc.resp.status != 404:
                raise
        carpeta = CATEGORIA_CARPETA.get(documento.categoria, "08_Otros")
        if documento.equipo_id:
            equipo_folder_id = sincronizar_carpeta_equipo(documento.equipo_id)
            padre = self.obtener_carpeta_equipo(carpeta, equipo_folder_id)["id"]
        else:
            padre = self.buscar_subcarpeta(documento.proyecto, carpeta)
        media = MediaFileUpload(documento.archivo.path, mimetype=documento.tipo_mime, resumable=True)
        try:
            return self.drive.files().create(body={"id": remote_id, "name": nombre_seguro(documento.nombre_original), "parents": [padre]}, media_body=media, fields=fields, supportsAllDrives=True).execute()
        except HttpError as exc:
            if exc.resp.status != 409:
                raise
            return self.drive.files().get(fileId=remote_id, fields=fields, supportsAllDrives=True).execute()
        finally:
            media.stream().close()

    def eliminar_permanentemente(self, file_id):
        self.drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def auditar(*, empresa, usuario, accion, objeto, proyecto=None, detalle=None):
    from .models import Auditoria
    return Auditoria.objects.create(empresa=empresa, usuario=usuario, accion=accion, objeto_tipo=objeto.__class__.__name__, objeto_id=str(objeto.pk), proyecto=proyecto or (objeto if objeto.__class__.__name__ == "Proyecto" else getattr(objeto, "proyecto", None)), detalle=detalle or {})


def encolar_carpeta_equipo(equipo_id):
    from .models import Equipo
    if not settings.GOOGLE_DRIVE_ENABLED:
        return
    try:
        from .tasks import crear_carpeta_equipo
        crear_carpeta_equipo.apply_async(args=[equipo_id], retry=False)
    except Exception as exc:
        logger.error("No se pudo encolar la carpeta Drive del equipo %s", equipo_id)
        Equipo.objects.filter(pk=equipo_id, drive_folder_id="").update(drive_error="No se pudo sincronizar con Drive. Reintente o contacte al administrador.")


def sincronizar_carpeta_equipo(equipo_id):
    from .models import Empresa, Equipo
    empresa_id = Equipo.objects.filter(pk=equipo_id).values_list("proyecto__empresa_id", flat=True).first()
    if empresa_id is None:
        return ""
    try:
        with transaction.atomic():
            # Serializar la raiz compartida, incluso entre empresas con la misma razon social.
            Empresa.objects.select_for_update().order_by("pk").first()
            equipo = Equipo.objects.select_for_update().get(pk=equipo_id)
            if equipo.drive_folder_id:
                return equipo.drive_folder_id
            carpeta = GoogleDriveService().crear_estructura_equipo(equipo)
            Equipo.objects.filter(pk=equipo_id).update(
                drive_folder_id=carpeta["id"], drive_folder_url=carpeta.get("webViewLink", ""), drive_error="",
            )
            return carpeta["id"]
    except Exception as exc:
        logger.error("Error creando carpeta Drive del equipo %s", equipo_id)
        Equipo.objects.filter(pk=equipo_id, drive_folder_id="").update(drive_error="No se pudo sincronizar con Drive. Reintente o contacte al administrador.")
        raise


def encolar_carpeta_registro(registro_id):
    from .models import RegistroEquipo
    if not settings.GOOGLE_DRIVE_ENABLED:
        return
    RegistroEquipo.objects.filter(pk=registro_id, drive_folder_id="").update(drive_error="")
    try:
        # Called after commit: persist the folder before returning to the user.
        sincronizar_carpeta_registro(registro_id)
    except Exception as exc:
        logger.error("No se pudo sincronizar Drive del registro %s", registro_id)
        RegistroEquipo.objects.filter(pk=registro_id, drive_folder_id="").update(drive_error="No se pudo sincronizar con Drive. Reintente o contacte al administrador.")


def sincronizar_carpeta_registro(registro_id):
    from .models import Empresa, RegistroEquipo
    registro = RegistroEquipo.objects.select_related("equipo__proyecto").filter(pk=registro_id).first()
    if registro is None:
        return ""
    if registro.drive_folder_id:
        return registro.drive_folder_id
    try:
        equipo_folder_id = sincronizar_carpeta_equipo(registro.equipo_id)
        # Commit the sequence before the remote write, including on lost responses.
        with transaction.atomic():
            Empresa.objects.select_for_update().order_by("pk").first()
            registro = RegistroEquipo.objects.select_for_update().get(pk=registro_id)
            if registro.drive_folder_id:
                return registro.drive_folder_id
            if registro.secuencia is None:
                from django.db.models import Max
                carpetas = GoogleDriveService().listar_carpetas(equipo_folder_id)
                patron = re.compile(r"(?:\d{4}-\d{2}-\d{2} - )?" + re.escape(registro.get_tipo_display()) + r" (\d+)$", re.IGNORECASE)
                numeros = [int(m.group(1)) for f in carpetas if (m := patron.fullmatch(f["name"]))]
                maximo = RegistroEquipo.objects.filter(equipo_id=registro.equipo_id, tipo=registro.tipo).aggregate(
                    valor=Max("secuencia"))["valor"] or 0
                registro.secuencia = max([maximo, *numeros]) + 1
                registro.save(update_fields=["secuencia"])
        with transaction.atomic():
            Empresa.objects.select_for_update().order_by("pk").first()
            registro = RegistroEquipo.objects.select_for_update().get(pk=registro_id)
            if registro.drive_folder_id:
                return registro.drive_folder_id
            servicio = GoogleDriveService()
            carpeta = servicio.crear_estructura_registro(registro, equipo_folder_id)
            RegistroEquipo.objects.filter(pk=registro_id).update(
                drive_folder_id=carpeta["id"], drive_folder_url=carpeta.get("webViewLink", ""), drive_error="",
            )
            return carpeta["id"]
    except Exception as exc:
        logger.error("Error creando carpeta Drive del registro %s", registro_id)
        RegistroEquipo.objects.filter(pk=registro_id, drive_folder_id="").update(drive_error="No se pudo sincronizar con Drive. Reintente o contacte al administrador.")
        raise


def encolar_drive(task, objeto_id):
    if not settings.GOOGLE_DRIVE_ENABLED:
        return False
    try:
        task.apply_async(args=[objeto_id], retry=False)
        return True
    except Exception:
        logger.error("No se pudo encolar la sincronización Drive")
        from .models import Documento
        if task.name.endswith("sincronizar_documento"):
            Documento.objects.filter(pk=objeto_id).update(estado_sincronizacion=Documento.Sincronizacion.ERROR,
                error_sincronizacion="Sincronización no disponible. Reintente más tarde.")
        return False

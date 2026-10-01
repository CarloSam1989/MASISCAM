"""Drive uploads from request streams. No FileField, storage.save or retained bytes."""
from django.conf import settings
from django.db import transaction
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from accounts.models import Empresa
from .models import ArchivoRegistro, RegistroEquipo
from .services import GoogleDriveService, sincronizar_carpeta_registro, nombre_seguro

RESELECCIONAR = "No se confirmó la carga. Vuelva a seleccionar este archivo para reintentarlo."


def registrar_archivos(registro, archivos):
    """Called inside the record transaction; de-duplicate within this record only."""
    for archivo in archivos:
        ArchivoRegistro.objects.get_or_create(
            registro=registro, hash_sha256=archivo.hash_sha256,
            defaults={"nombre": nombre_seguro(archivo.name), "tipo_mime": archivo.content_type.lower(), "tamano": archivo.size},
        )


def _remoto(servicio, archivo, carpeta):
    if not archivo.drive_file_id:
        return None
    try:
        item = servicio.drive.files().get(
            fileId=archivo.drive_file_id, fields="id,parents,trashed,mimeType,size,appProperties", supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            return None
        raise
    if (item.get("trashed") or item.get("parents") != [carpeta]
            or item.get("mimeType") != archivo.tipo_mime or int(item.get("size", 0)) != archivo.tamano
            or item.get("appProperties", {}).get("masiscam_hash") != archivo.hash_sha256):
        raise ValueError("El archivo remoto no corresponde a este registro.")
    return item


def sincronizar_archivo(archivo_id, contenido=None):
    """A reserved Drive ID survives failures and prevents duplicate remote files."""
    archivo = ArchivoRegistro.objects.filter(pk=archivo_id).first()
    if archivo is None:
        return False  # A queued retry after deleting a record must not recreate it.
    if archivo.estado == ArchivoRegistro.Estado.DISPONIBLE:
        return True
    try:
        if not settings.GOOGLE_DRIVE_ENABLED:
            raise ValueError("Drive no está disponible.")
        carpeta = sincronizar_carpeta_registro(archivo.registro_id)
        servicio = GoogleDriveService()
        # Reserve independently of the transaction which transmits bytes.
        with transaction.atomic():
            archivo = ArchivoRegistro.objects.select_for_update().get(pk=archivo_id)
            if not archivo.drive_file_id and contenido is not None:
                archivo.drive_file_id = servicio.drive.files().generateIds(count=1, space="drive", type="files").execute()["ids"][0]
                archivo.save(update_fields=["drive_file_id"])
        with transaction.atomic():
            # Same lock order as folder creation/deletion, across workers and web.
            Empresa.objects.select_for_update().order_by("pk").first()
            registro = RegistroEquipo.objects.select_for_update().get(pk=archivo.registro_id)
            archivo = ArchivoRegistro.objects.select_for_update().get(pk=archivo_id)
            if archivo.estado == ArchivoRegistro.Estado.DISPONIBLE:
                return True
            if registro.drive_folder_id != carpeta:
                raise ValueError("La carpeta del registro cambió.")
            if _remoto(servicio, archivo, carpeta) is None:
                if contenido is None:
                    archivo.estado, archivo.error = ArchivoRegistro.Estado.ERROR, RESELECCIONAR
                    archivo.save(update_fields=["estado", "error"])
                    return False
                # Validate ownership before sending bytes, including shared legacy folders.
                servicio.validar_carpeta_registro_eliminable(registro)
                contenido.seek(0)
                media = MediaIoBaseUpload(contenido, mimetype=archivo.tipo_mime, chunksize=1024 * 1024, resumable=True)
                try:
                    servicio.drive.files().create(
                        body={"id": archivo.drive_file_id, "name": archivo.nombre, "mimeType": archivo.tipo_mime, "parents": [carpeta],
                              "appProperties": {"masiscam_hash": archivo.hash_sha256}},
                        media_body=media, fields="id", supportsAllDrives=True,
                    ).execute(num_retries=2)
                except HttpError as exc:
                    if exc.resp.status != 409:
                        raise
                if _remoto(servicio, archivo, carpeta) is None:
                    raise ValueError("Drive no confirmó el archivo.")
            archivo.estado, archivo.error = ArchivoRegistro.Estado.DISPONIBLE, ""
            archivo.save(update_fields=["estado", "error"])
            return True
    except Exception:
        ArchivoRegistro.objects.filter(pk=archivo_id).exclude(estado=ArchivoRegistro.Estado.DISPONIBLE).update(
            estado=ArchivoRegistro.Estado.ERROR, error=RESELECCIONAR)
        raise


def subir_archivos(registro, archivos):
    pendientes = {a.hash_sha256: a for a in registro.archivos.all()}
    for contenido in archivos:
        try:
            sincronizar_archivo(pendientes[contenido.hash_sha256].pk, contenido)
        except Exception:
            # Keep successful files; the failed file retains its reserved ID.
            pass
    return not registro.archivos.exclude(estado=ArchivoRegistro.Estado.DISPONIBLE).exists()


def encolar_revision(registro_id):
    from .tasks import revisar_archivos_registro
    try:
        revisar_archivos_registro.apply_async(args=[registro_id], retry=False)
    except Exception:
        # The explicit retry also reconciles remote results when Celery is down.
        ArchivoRegistro.objects.filter(registro_id=registro_id, estado=ArchivoRegistro.Estado.PENDIENTE).update(
            estado=ArchivoRegistro.Estado.ERROR, error=RESELECCIONAR)

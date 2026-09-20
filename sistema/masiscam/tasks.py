from celery import shared_task
from django.conf import settings
from django.db import transaction


class DriveSyncError(Exception):
    """Safe public error without provider credentials, URLs or filesystem paths."""


TASK_OPTIONS = dict(autoretry_for=(DriveSyncError,), retry_backoff=True,
                    retry_jitter=True, retry_kwargs={"max_retries": 4})


@shared_task(**TASK_OPTIONS)
def crear_carpeta_registro(registro_id):
    if not settings.GOOGLE_DRIVE_ENABLED:
        return ""
    from .services import sincronizar_carpeta_registro
    try:
        return sincronizar_carpeta_registro(registro_id)
    except Exception:
        raise DriveSyncError("No se pudo sincronizar el registro con Drive.") from None


@shared_task(**TASK_OPTIONS)
def crear_carpeta_equipo(equipo_id):
    if not settings.GOOGLE_DRIVE_ENABLED:
        return ""
    from .services import sincronizar_carpeta_equipo
    try:
        return sincronizar_carpeta_equipo(equipo_id)
    except Exception:
        raise DriveSyncError("No se pudo sincronizar el equipo con Drive.") from None


@shared_task(**TASK_OPTIONS)
def crear_carpeta_proyecto(proyecto_id):
    if not settings.GOOGLE_DRIVE_ENABLED:
        return ""
    from .models import Empresa, Proyecto
    from .services import GoogleDriveService
    try:
        with transaction.atomic():
            Empresa.objects.select_for_update().order_by("pk").first()
            proyecto = Proyecto.objects.select_for_update().get(pk=proyecto_id)
            if proyecto.drive_folder_id:
                return proyecto.drive_folder_id
            carpeta, _ = GoogleDriveService().crear_estructura_proyecto(proyecto)
            proyecto.drive_folder_id = carpeta["id"]
            proyecto.drive_folder_url = carpeta.get("webViewLink", "")
            proyecto.save(update_fields=["drive_folder_id", "drive_folder_url", "actualizado_en"])
            return proyecto.drive_folder_id
    except Exception:
        raise DriveSyncError("No se pudo sincronizar el proyecto con Drive.") from None


@shared_task(**TASK_OPTIONS)
def sincronizar_documento(documento_id):
    if not settings.GOOGLE_DRIVE_ENABLED:
        return ""
    from .models import Documento
    from .services import GoogleDriveService
    try:
        # Reservation survives a rollback of the subsequent upload/result transaction.
        current = Documento.objects.get(pk=documento_id)
        if current.estado_sincronizacion == Documento.Sincronizacion.ARCHIVADO:
            return ""
        servicio = GoogleDriveService()
        servicio.reservar_documento(documento_id)
        with transaction.atomic():
            documento = Documento.objects.select_for_update().select_related("proyecto").get(pk=documento_id)
            if documento.estado_sincronizacion == Documento.Sincronizacion.ARCHIVADO:
                return ""
            if documento.drive_file_id and documento.estado_sincronizacion == Documento.Sincronizacion.SINCRONIZADO:
                return documento.drive_file_id
            if not documento.proyecto.drive_folder_id:
                crear_carpeta_proyecto(documento.proyecto_id)
                documento.proyecto.refresh_from_db()
            resultado = servicio.subir_documento(documento)
            documento.drive_file_id = resultado["id"]
            documento.drive_view_url = resultado.get("webViewLink", "")
            documento.drive_download_url = resultado.get("webContentLink", "")
            documento.estado_sincronizacion = Documento.Sincronizacion.SINCRONIZADO
            documento.error_sincronizacion = ""
            documento.save(update_fields=["drive_file_id", "drive_view_url", "drive_download_url", "estado_sincronizacion", "error_sincronizacion"])
            return documento.drive_file_id
    except Exception:
        Documento.objects.filter(pk=documento_id).exclude(estado_sincronizacion=Documento.Sincronizacion.ARCHIVADO).update(
            estado_sincronizacion=Documento.Sincronizacion.ERROR,
            error_sincronizacion="No se pudo sincronizar con Drive. Reintente más tarde.")
        raise DriveSyncError("No se pudo sincronizar el documento con Drive.") from None

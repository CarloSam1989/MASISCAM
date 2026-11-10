from celery import shared_task
from django.conf import settings
from django.db import transaction
import logging


class DriveSyncError(Exception):
    """Safe public error without provider credentials, URLs or filesystem paths."""


TASK_OPTIONS = dict(autoretry_for=(DriveSyncError,), retry_backoff=True,
                    retry_jitter=True, retry_kwargs={"max_retries": 4})


@shared_task(**TASK_OPTIONS)
def revisar_archivos_registro(registro_id):
    from .models import ArchivoRegistro, RegistroEquipo
    from .registro_archivos import sincronizar_archivo
    from .services import sincronizar_carpeta_registro
    if not settings.GOOGLE_DRIVE_ENABLED or not RegistroEquipo.objects.filter(pk=registro_id).exists():
        return
    try:
        sincronizar_carpeta_registro(registro_id)
        fallos = False
        for archivo_id in ArchivoRegistro.objects.filter(registro_id=registro_id).exclude(
                estado=ArchivoRegistro.Estado.DISPONIBLE).values_list("pk", flat=True):
            try:
                sincronizar_archivo(archivo_id)
            except Exception:
                fallos = True
        if fallos:
            raise DriveSyncError("No se pudo verificar la carga en Drive.")
    except Exception:
        raise DriveSyncError("No se pudo verificar la carga en Drive.") from None


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
            if not documento.equipo_id and not documento.proyecto.drive_folder_id:
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
    except Exception as exc:
        # Never log provider bodies, credentials, URLs or local paths.
        from pathlib import Path
        if not settings.GOOGLE_DRIVE_CREDENTIALS_FILE or not settings.GOOGLE_DRIVE_ROOT_FOLDER_ID:
            causa = 'CONFIGURACION_INCOMPLETA'
        elif not Path(settings.GOOGLE_DRIVE_CREDENTIALS_FILE).is_file():
            causa = 'CREDENCIAL_NO_ACCESIBLE'
        else:
            status = getattr(getattr(exc, 'resp', None), 'status', None)
            causa = f'HTTP_{status}' if isinstance(status,int) else 'SERVICIO_NO_DISPONIBLE'
        logging.getLogger(__name__).warning('Drive pendiente: documento=%s causa=%s', documento_id, causa)
        Documento.objects.filter(pk=documento_id).exclude(estado_sincronizacion=Documento.Sincronizacion.ARCHIVADO).update(
            estado_sincronizacion=Documento.Sincronizacion.ERROR,
            error_sincronizacion="No se pudo sincronizar con Drive. Reintente más tarde.")
        raise DriveSyncError("No se pudo sincronizar el documento con Drive.") from None


# Celery autodiscovers this module; workers must register phase tasks even
# before a web view imports them. These imports perform no remote operations.
from .recepcion_tasks import generar_guia_ingreso  # noqa: E402,F401
from .tecnica_tasks import generar_informe_inicial  # noqa: E402,F401
from .operacion_tasks import generar_documento_operativo  # noqa: E402,F401
from .calibracion_tasks import generar_informe_calibracion  # noqa: E402,F401
from .cierre_tasks import generar_informe_final  # noqa: E402,F401

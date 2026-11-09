from celery import shared_task
from .models import EmisionCalibracion


@shared_task
def generar_informe_calibracion(emision_id):
    from .calibracion_services import generar_informe
    try:
        return generar_informe(emision_id)
    except Exception:
        EmisionCalibracion.objects.filter(pk=emision_id,documento__isnull=True).update(estado='ERROR',
            error='No se pudo emitir; verifique fuente, aprobaciones y almacenamiento. Reserva conservada.')
        raise

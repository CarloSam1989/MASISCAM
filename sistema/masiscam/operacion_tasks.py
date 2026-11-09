from celery import shared_task
from .models import EmisionOperativa


@shared_task
def generar_documento_operativo(emision_id):
    from .operacion_services import generar_documento
    try:
        return generar_documento(emision_id)
    except Exception:
        # Do not expose storage paths, exception details or credentials in UI.
        EmisionOperativa.objects.filter(pk=emision_id,documento__isnull=True).update(
            estado='ERROR',error='No se pudo emitir. Verifique fuentes, política y almacenamiento; reintente la misma reserva.')
        raise

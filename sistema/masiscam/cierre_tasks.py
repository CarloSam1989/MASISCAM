from celery import shared_task


@shared_task
def generar_informe_final(informe_id):
    from .cierre_services import generar
    from .models import InformeFinal
    try: return generar(informe_id)
    except Exception:
        InformeFinal.objects.filter(pk=informe_id,documento__isnull=True).update(error='Generación fallida; reserva conservada. Reintente la misma emisión.')
        raise

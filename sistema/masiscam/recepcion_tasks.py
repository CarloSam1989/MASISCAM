from celery import shared_task
from django.conf import settings

from .models import EmisionGuiaIngreso


@shared_task
def generar_guia_ingreso(guia_id):
    from .recepcion_services import generar_guia
    from .services import encolar_drive
    from .tasks import sincronizar_documento
    try:
        doc_id = generar_guia(guia_id)
    except Exception:
        EmisionGuiaIngreso.objects.filter(pk=guia_id, documento__isnull=True).update(
            estado="ERROR", error="No se pudo generar la guía. Reintente; la recepción se conserva.")
        raise
    if settings.GOOGLE_DRIVE_ENABLED:
        guia = EmisionGuiaIngreso.objects.get(pk=guia_id)
        for doc in [guia.documento] + [e.documento for e in guia.expediente.recepcion.evidencias.select_related("documento")]:
            encolar_drive(sincronizar_documento, doc.pk)
    return doc_id

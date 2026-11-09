from celery import shared_task
from django.conf import settings
from .models import EmisionInformeTecnico


@shared_task
def generar_informe_inicial(emision_id):
    from .tecnica_services import generar_informe
    from .services import encolar_drive
    from .tasks import sincronizar_documento
    try:
        doc_id=generar_informe(emision_id)
    except Exception:
        EmisionInformeTecnico.objects.filter(pk=emision_id,documento__isnull=True).update(
            estado='ERROR',error='No se pudo generar el informe íntegro. Reintente; la reserva se conserva.')
        raise
    if settings.GOOGLE_DRIVE_ENABLED:
        em=EmisionInformeTecnico.objects.get(pk=emision_id)
        ids={doc_id}|{e['documento_id'] for e in em.snapshot['evidencias']}
        ids.update(p['referencia']['documento'] for p in em.snapshot['piezas'] if p.get('referencia',{}).get('documento'))
        for pk in ids:
            encolar_drive(sincronizar_documento,pk)
    return doc_id

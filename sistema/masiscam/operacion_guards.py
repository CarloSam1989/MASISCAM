from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_save, pre_delete
from django.dispatch import receiver
from .operacion_models import (PropuestaComercial, DecisionComercial, RecepcionRepuesto, ActividadReparacion,
    UsoRepuesto, ResultadoReparacion, RevisionReparacion, EvidenciaOperativa, EmisionOperativa)

MODELS={PropuestaComercial,DecisionComercial,RecepcionRepuesto,ActividadReparacion,
    UsoRepuesto,ResultadoReparacion,RevisionReparacion,EvidenciaOperativa,EmisionOperativa}


@receiver(pre_save, dispatch_uid='operacion_inmutable')
def proteger(sender, instance, raw=False, using='default', **kwargs):
    if sender is ResultadoReparacion and not raw and not instance.pk:
        last=ResultadoReparacion.objects.using(using).filter(actividad_id=instance.actividad_id).order_by('-version').first()
        if last and not RevisionReparacion.objects.using(using).filter(resultado=last,decision='OBSERVADO').exists():
            raise PermissionDenied('Resultado enviado o aprobado: solo una observación permite corrección.')
    if sender not in MODELS or raw or not instance.pk:
        return
    old=sender.objects.using(using).filter(pk=instance.pk).first()
    if not old:
        return
    fields=[f.attname for f in sender._meta.concrete_fields]
    if sender is EmisionOperativa and not old.documento_id:
        fields=[f for f in fields if f not in {'documento_id','hash_sha256','estado','error'}]
    if any(getattr(old,f)!=getattr(instance,f) for f in fields):
        raise PermissionDenied('Historial operativo inmutable: registre una nueva versión o corrección.')


@receiver(pre_delete, dispatch_uid='operacion_no_borrado')
def no_borrar(sender, **kwargs):
    if sender in MODELS:
        raise PermissionDenied('No se elimina historial comercial, repuestos, reparación ni documentos.')

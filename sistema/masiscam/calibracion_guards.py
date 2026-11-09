from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_save,pre_delete
from django.dispatch import receiver
from .models import (PlantillaCalibracion,ValidacionPlantillaCalibracion,AplicabilidadCalibracion,
    Calibracion,MedicionCalibracion,EvidenciaCalibracion,DecisionCalibracion,EmisionCalibracion)

MODELS={PlantillaCalibracion,ValidacionPlantillaCalibracion,AplicabilidadCalibracion,Calibracion,
    MedicionCalibracion,EvidenciaCalibracion,DecisionCalibracion,EmisionCalibracion}


@receiver(pre_save,dispatch_uid='calibracion_proteger')
def proteger(sender,instance,raw=False,using='default',**kwargs):
    if sender not in MODELS or raw:
        return
    if sender in {MedicionCalibracion,EvidenciaCalibracion} and instance.calibracion.enviado_en:
        raise PermissionDenied('Revisión enviada: datos y evidencias congelados.')
    if not instance.pk:
        return
    old=sender.objects.using(using).filter(pk=instance.pk).first()
    if not old:
        return
    fields=[f.attname for f in sender._meta.concrete_fields]
    if sender is Calibracion:
        fields=['expediente_id','diagnostico_id','autorizacion_id','plantilla_id','reparacion_snapshot',
            'revision','sustituye_id','motivo_revision','tecnico_id','creado_en','clave']
        if old.enviado_en:
            fields=[f.attname for f in sender._meta.concrete_fields if f.name!='estado']
    elif sender is MedicionCalibracion:
        fields=['calibracion_id','punto']
    elif sender is EmisionCalibracion and not old.documento_id:
        fields=[f for f in fields if f not in {'documento_id','hash_sha256','estado','error'}]
    if any(getattr(old,f)!=getattr(instance,f) for f in fields):
        raise PermissionDenied('Datos de calibración inmutables; cree nueva revisión o versión.')


@receiver(pre_delete,dispatch_uid='calibracion_no_borrado')
def no_borrar(sender,**kwargs):
    if sender in MODELS:
        raise PermissionDenied('Se conserva el historial de calibración y sus documentos.')

"""ORM guard plus database guards in additive phase-3 migration."""
from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_save, pre_delete
from django.dispatch import receiver
from .models import (AsignacionTecnica, Inspeccion, Diagnostico, EvidenciaTecnica,
    DecisionTecnica, ReferenciaTecnica, ComponenteTecnico, ParteReferenciaTecnica,
    AplicabilidadReferenciaEquipo, PiezaRequerida, EmisionInformeTecnico, EtapaExpediente)

MODELS={AsignacionTecnica,Inspeccion,Diagnostico,EvidenciaTecnica,DecisionTecnica,
    ReferenciaTecnica,ComponenteTecnico,ParteReferenciaTecnica,AplicabilidadReferenciaEquipo,
    PiezaRequerida,EmisionInformeTecnico,EtapaExpediente}


def parent(obj):
    if isinstance(obj,ParteReferenciaTecnica):
        return obj.referencia
    return getattr(obj,'inspeccion',None) or getattr(obj,'diagnostico',None)


@receiver(pre_save,dispatch_uid='tecnica_proteger_cambios')
def proteger(sender,instance,raw=False,using='default',**kwargs):
    if sender not in MODELS or raw:
        return
    if isinstance(instance,(EvidenciaTecnica,PiezaRequerida)) and parent(instance).enviado_en:
        raise PermissionDenied('No se modifican detalles de una revisión enviada.')
    if isinstance(instance,ParteReferenciaTecnica) and instance.referencia.estado!='BORRADOR':
        raise PermissionDenied('El plano publicado y sus partes son inmutables.')
    if not instance.pk:
        return
    old=sender.objects.using(using).filter(pk=instance.pk).first()
    if not old:
        return
    fields=[]
    if sender in {DecisionTecnica}:
        fields=[f.attname for f in sender._meta.concrete_fields]
    elif sender in {Inspeccion,Diagnostico}:
        fields=['expediente_id','revision','tecnico_id','creado_en','sustituye_id','motivo_revision']
        if old.enviado_en:
            fields=[f.attname for f in sender._meta.concrete_fields if f.name!='estado']
    elif sender is ReferenciaTecnica and old.estado!='BORRADOR':
        fields=[f.attname for f in sender._meta.concrete_fields if f.name!='estado']
    elif sender is EmisionInformeTecnico:
        fields=['expediente_id','diagnostico_id','tipo','version','clave','huella','snapshot','autor_id','creado_en']
        if old.documento_id:
            fields += ['documento_id','hash_sha256','estado','error']
    elif sender is AsignacionTecnica:
        fields=[f.attname for f in sender._meta.concrete_fields if f.name!='fin']
        if old.fin:
            fields+=['fin']
    if any(getattr(old,name)!=getattr(instance,name) for name in fields):
        raise PermissionDenied('Datos técnicos congelados: cree una revisión nueva.')


@receiver(pre_delete,dispatch_uid='tecnica_proteger_borrado')
def no_borrar(sender,instance,**kwargs):
    if sender not in MODELS:
        return
    if sender in {EvidenciaTecnica,PiezaRequerida} and not parent(instance).enviado_en:
        return
    if sender is ParteReferenciaTecnica and instance.referencia.estado=='BORRADOR':
        return
    raise PermissionDenied('Se conserva el historial técnico y documental.')

from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_save,pre_delete
from django.dispatch import receiver
from .models import InformeFinal,RevisionInformeFinal,CierreExpediente


@receiver(pre_save,dispatch_uid='cierre_contenido_inmutable')
def proteger(sender,instance,raw=False,using='default',**kwargs):
    if raw or sender not in {InformeFinal,RevisionInformeFinal,CierreExpediente} or not instance.pk: return
    old=sender.objects.using(using).filter(pk=instance.pk).first()
    if not old: return
    fields=[f.attname for f in sender._meta.concrete_fields]
    if sender is InformeFinal:
        fields=[f for f in fields if f!='estado']
        if not old.documento_id: fields=[f for f in fields if f not in {'documento_id','hash_sha256','enviado_en','error'}]
    if any(getattr(old,f)!=getattr(instance,f) for f in fields):
        raise PermissionDenied('Informe, revisión o cierre inmutables. Cree una nueva versión autorizada.')


@receiver(pre_delete,dispatch_uid='cierre_no_borrado')
def no_borrar(sender,**kwargs):
    if sender in {InformeFinal,RevisionInformeFinal,CierreExpediente}: raise PermissionDenied('Historial final protegido.')

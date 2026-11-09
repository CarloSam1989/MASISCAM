"""ORM guards in addition to database triggers; legacy rows remain editable."""
from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_save, pre_delete
from django.dispatch import receiver

from .models import (Auditoria, ConfirmacionRecepcion, FotoRecepcionClasificada, Documento, EmisionGuiaIngreso, Equipo,
    EvidenciaRecepcion, ExpedienteMantenimiento, Proyecto, RecepcionEquipo, RegistroEquipo)


def documento_protegido(pk):
    from .models import InformeFinal
    from .models import PlantillaCalibracion,EvidenciaCalibracion,EmisionCalibracion
    from .models import EvidenciaTecnica, ReferenciaTecnica, EmisionInformeTecnico, EvidenciaOperativa, EmisionOperativa
    return bool(pk and (InformeFinal.objects.filter(documento_id=pk).exists()
                       or EmisionGuiaIngreso.objects.filter(documento_id=pk).exists()
                       or EvidenciaRecepcion.objects.filter(documento_id=pk).exists()
                       or EvidenciaTecnica.objects.filter(documento_id=pk).exists()
                       or ReferenciaTecnica.objects.filter(documento_id=pk).exists()
                       or EmisionInformeTecnico.objects.filter(documento_id=pk).exists()
                       or EvidenciaOperativa.objects.filter(documento_id=pk).exists()
                       or EmisionOperativa.objects.filter(documento_id=pk).exists()
                       or PlantillaCalibracion.objects.filter(documento_id=pk).exists()
                       or EvidenciaCalibracion.objects.filter(documento_id=pk).exists()
                       or EmisionCalibracion.objects.filter(documento_id=pk).exists()))


@receiver(pre_save, dispatch_uid="recepcion_inmutabilidad")
def proteger_cambios(sender, instance, raw=False, using="default", **kwargs):
    if raw or not instance.pk or sender not in {ExpedienteMantenimiento, RecepcionEquipo, EvidenciaRecepcion,
            EmisionGuiaIngreso, Documento, RegistroEquipo, Equipo, Proyecto, Auditoria, ConfirmacionRecepcion, FotoRecepcionClasificada}:
        return
    old = sender.objects.using(using).filter(pk=instance.pk).first()
    if old is None:
        return
    campos = []
    if sender in {ExpedienteMantenimiento, RecepcionEquipo, EvidenciaRecepcion, ConfirmacionRecepcion, FotoRecepcionClasificada}:
        campos = [f.attname for f in sender._meta.concrete_fields]
    elif sender is EmisionGuiaIngreso:
        campos = ["expediente_id", "clave", "version", "snapshot", "creado_por_id", "creado_en"]
        if old.documento_id:
            campos += ["documento_id", "hash_sha256", "estado", "error"]
    elif sender is Documento and documento_protegido(instance.pk):
        campos = ["proyecto_id", "equipo_id", "categoria", "titulo", "descripcion", "archivo",
                  "nombre_original", "tipo_mime", "tamano", "hash_sha256", "publico", "subido_por_id", "cargado_en"]
        if instance.estado_sincronizacion == "ARCHIVADO":
            raise PermissionDenied("El documento pertenece a una recepción protegida.")
    elif sender is RegistroEquipo and ExpedienteMantenimiento.objects.filter(registro_id=instance.pk).exists():
        campos = ["equipo_id", "tipo", "fecha", "observacion", "clave_creacion", "drive_folder_id", "drive_folder_url"]
    elif sender is Equipo and instance.proyecto_id != old.proyecto_id:
        if ExpedienteMantenimiento.objects.filter(registro__equipo_id=instance.pk).exists():
            if instance.proyecto.empresa_id != old.proyecto.empresa_id:
                raise PermissionDenied("No puede transferir de empresa un equipo con expediente.")
    elif sender is Proyecto and instance.empresa_id != old.empresa_id:
        if ExpedienteMantenimiento.objects.filter(registro__equipo__proyecto_id=instance.pk).exists():
            raise PermissionDenied("Proyecto con expedientes protegidos.")
    elif sender is Auditoria and old.expediente_id:
        campos = [f.attname for f in sender._meta.concrete_fields]
    if any(getattr(old, c) != getattr(instance, c) for c in campos):
        raise PermissionDenied("Información de recepción inmutable; la corrección requiere una revisión autorizada.")


@receiver(pre_delete, dispatch_uid="recepcion_no_borrado")
def proteger_borrado(sender, instance, **kwargs):
    if sender in {ExpedienteMantenimiento, RecepcionEquipo, EvidenciaRecepcion, EmisionGuiaIngreso, ConfirmacionRecepcion, FotoRecepcionClasificada}:
        raise PermissionDenied("No se eliminan expedientes ni sus evidencias o emisiones.")
    if sender is Auditoria and instance.expediente_id:
        raise PermissionDenied("Auditoría de expediente inmutable.")
    if sender is Documento and documento_protegido(instance.pk):
        raise PermissionDenied("Documento de recepción protegido.")

from functools import partial

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Equipo, RegistroEquipo
from .services import encolar_carpeta_equipo, encolar_carpeta_registro


@receiver(post_save, sender=Equipo, dispatch_uid="masiscam_equipo_drive")
def equipo_creado(sender, instance, created, raw, using, **kwargs):
    if created and not raw and not instance.drive_folder_id:
        transaction.on_commit(partial(encolar_carpeta_equipo, instance.pk), using=using)


@receiver(post_save, sender=RegistroEquipo, dispatch_uid="masiscam_registro_drive")
def registro_creado(sender, instance, created, raw, using, **kwargs):
    if created and not raw and not instance.drive_folder_id:
        transaction.on_commit(partial(encolar_carpeta_registro, instance.pk), using=using)

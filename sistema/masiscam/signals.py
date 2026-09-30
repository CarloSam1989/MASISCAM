from functools import partial
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Equipo, RegistroEquipo
from .services import encolar_carpeta_equipo, encolar_carpeta_registro


@receiver(user_logged_in, dispatch_uid="masiscam_session_expiry")
def fijar_vencimiento_sesion(sender, request, **kwargs):
    # Una fecha fija evita prolongar el plazo al guardar la empresa activa.
    request.session.set_expiry(timedelta(seconds=settings.SESSION_COOKIE_AGE))


@receiver(post_save, sender=Equipo, dispatch_uid="masiscam_equipo_drive")
def equipo_creado(sender, instance, created, raw, using, **kwargs):
    if created and not raw and not instance.drive_folder_id:
        transaction.on_commit(partial(encolar_carpeta_equipo, instance.pk), using=using)


@receiver(post_save, sender=RegistroEquipo, dispatch_uid="masiscam_registro_drive")
def registro_creado(sender, instance, created, raw, using, **kwargs):
    if created and not raw and not instance.drive_folder_id:
        transaction.on_commit(partial(encolar_carpeta_registro, instance.pk), using=using)

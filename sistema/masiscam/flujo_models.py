"""Trazabilidad adicional del relevo taller/oficina; reutiliza piezas y recepciones."""
from django.db import models
from .operacion_models import RegistroOperativo

P = models.PROTECT


class SolicitudRepuestos(RegistroOperativo):
    diagnostico = models.OneToOneField('Diagnostico', on_delete=P, related_name='solicitud_repuestos')
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    huella = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente','version'], name='flujo_solicitud_version')]


class AdquisicionRepuesto(RegistroOperativo):
    solicitud = models.ForeignKey(SolicitudRepuestos, on_delete=P, related_name='adquisiciones')
    pieza = models.ForeignKey('PiezaRequerida', on_delete=P)
    autorizacion = models.ForeignKey('DecisionComercial', on_delete=P)
    fecha = models.DateTimeField()
    proveedor = models.CharField(max_length=200)
    referencia = models.CharField(max_length=200)
    cantidad = models.DecimalField(max_digits=20, decimal_places=6)
    unidad = models.CharField(max_length=80)
    documento = models.ForeignKey('Documento', on_delete=P, null=True, blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='flujo_adquisicion_positiva')]


class EnvioRepuestos(RegistroOperativo):
    solicitud = models.ForeignKey(SolicitudRepuestos, on_delete=P, related_name='envios')
    fecha = models.DateTimeField()
    numero_guia = models.CharField(max_length=200)
    snapshot = models.JSONField()
    documento = models.OneToOneField('Documento', on_delete=P)
    hash_sha256 = models.CharField(max_length=64)


class DetalleEnvio(models.Model):
    envio = models.ForeignKey(EnvioRepuestos, on_delete=P, related_name='lineas')
    adquisicion = models.ForeignKey(AdquisicionRepuesto, on_delete=P, related_name='envios')
    cantidad = models.DecimalField(max_digits=20, decimal_places=6)
    unidad = models.CharField(max_length=80)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['envio','adquisicion'], name='flujo_envio_adquisicion'),
            models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='flujo_envio_positivo')]


class ConfirmacionEnvio(RegistroOperativo):
    envio = models.OneToOneField(EnvioRepuestos, on_delete=P, related_name='confirmacion')
    fecha = models.DateTimeField()
    observaciones = models.TextField(blank=True)


class RecepcionLineaEnvio(models.Model):
    confirmacion = models.ForeignKey(ConfirmacionEnvio, on_delete=P, related_name='lineas')
    detalle = models.OneToOneField(DetalleEnvio, on_delete=P, related_name='confirmacion')
    recepcion = models.OneToOneField('RecepcionRepuesto', on_delete=P, null=True, blank=True)
    cantidad = models.DecimalField(max_digits=20, decimal_places=6)
    discrepancia = models.TextField(blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(cantidad__gte=0), name='flujo_recepcion_no_negativa'),
            models.CheckConstraint(condition=(models.Q(cantidad=0,recepcion__isnull=True)|
                models.Q(cantidad__gt=0,recepcion__isnull=False)), name='flujo_recepcion_enlace')]

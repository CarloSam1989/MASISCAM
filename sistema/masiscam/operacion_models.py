"""Phase 4: append-only commercial, parts and repair records."""
import uuid
from django.conf import settings
from django.db import models

P = models.PROTECT


class RegistroOperativo(models.Model):
    expediente = models.ForeignKey('ExpedienteMantenimiento', on_delete=P)
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=P)
    creado_en = models.DateTimeField(auto_now_add=True)
    clave = models.UUIDField(default=uuid.uuid4, unique=True)

    class Meta:
        abstract = True


class PropuestaComercial(RegistroOperativo):
    diagnostico = models.ForeignKey('Diagnostico', on_delete=P)
    informe = models.ForeignKey('EmisionInformeTecnico', on_delete=P)
    version = models.PositiveIntegerField()
    alcance = models.TextField()
    observaciones = models.TextField(blank=True)
    sustituye = models.ForeignKey('self', on_delete=P, null=True, blank=True)
    snapshot = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente', 'version'], name='operacion_propuesta_version')]


class DecisionComercial(RegistroOperativo):
    propuesta = models.OneToOneField(PropuestaComercial, on_delete=P, related_name='decision')
    decision = models.CharField(max_length=10, choices=[('APROBADO','Aprobado'), ('RECHAZADO','Rechazado')])
    fecha_cliente = models.DateTimeField()
    representante_cliente = models.CharField(max_length=200)
    motivo = models.TextField()
    politica = models.JSONField()


class RecepcionRepuesto(RegistroOperativo):
    pieza = models.ForeignKey('PiezaRequerida', on_delete=P, null=True, blank=True)
    procedencia = models.CharField(max_length=200)
    entregado_por = models.CharField(max_length=200)
    numero_parte = models.CharField(max_length=120, blank=True)
    descripcion = models.TextField()
    cantidad = models.DecimalField(max_digits=20, decimal_places=6)
    unidad = models.CharField(max_length=80)
    fecha = models.DateTimeField()
    guia = models.CharField(max_length=200, blank=True)
    discrepancia = models.TextField(blank=True)
    snapshot = models.JSONField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='operacion_recepcion_positiva')]


class ActividadReparacion(RegistroOperativo):
    autorizacion = models.ForeignKey(DecisionComercial, on_delete=P)
    trabajo = models.TextField()
    componentes = models.TextField()
    inicio = models.DateTimeField()
    observaciones = models.TextField(blank=True)


class UsoRepuesto(RegistroOperativo):
    actividad = models.ForeignKey(ActividadReparacion, on_delete=P, related_name='usos')
    recepcion = models.ForeignKey(RecepcionRepuesto, on_delete=P)
    cantidad = models.DecimalField(max_digits=20, decimal_places=6)
    observaciones = models.TextField(blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='operacion_uso_positivo')]


class ResultadoReparacion(RegistroOperativo):
    actividad = models.ForeignKey(ActividadReparacion, on_delete=P, related_name='resultados')
    version = models.PositiveIntegerField()
    fin = models.DateTimeField()
    trabajo_realizado = models.TextField()
    resultado = models.TextField()
    observaciones = models.TextField(blank=True)
    snapshot = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actividad', 'version'], name='operacion_resultado_version')]


class RevisionReparacion(RegistroOperativo):
    resultado = models.OneToOneField(ResultadoReparacion, on_delete=P, related_name='revision')
    decision = models.CharField(max_length=10, choices=[('APROBADO','Aprobado'), ('OBSERVADO','Observado')])
    motivo = models.TextField()


class EvidenciaOperativa(RegistroOperativo):
    documento = models.ForeignKey('Documento', on_delete=P, related_name='evidencias_operativas')
    propuesta = models.ForeignKey(PropuestaComercial, on_delete=P, null=True, blank=True, related_name='evidencias')
    decision = models.ForeignKey(DecisionComercial, on_delete=P, null=True, blank=True, related_name='evidencias')
    recepcion = models.ForeignKey(RecepcionRepuesto, on_delete=P, null=True, blank=True, related_name='evidencias')
    actividad = models.ForeignKey(ActividadReparacion, on_delete=P, null=True, blank=True, related_name='evidencias')
    clase = models.CharField(max_length=12, choices=[(x,x.capitalize()) for x in ('DOCUMENTO','FOTO','ANTES','DURANTE','DESPUES')])
    leyenda = models.TextField()

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(propuesta__isnull=False,decision__isnull=True,recepcion__isnull=True,actividad__isnull=True) |
            models.Q(propuesta__isnull=True,decision__isnull=False,recepcion__isnull=True,actividad__isnull=True) |
            models.Q(propuesta__isnull=True,decision__isnull=True,recepcion__isnull=False,actividad__isnull=True) |
            models.Q(propuesta__isnull=True,decision__isnull=True,recepcion__isnull=True,actividad__isnull=False)), name='operacion_evidencia_destino')]


class EmisionOperativa(RegistroOperativo):
    tipo = models.CharField(max_length=12, choices=[('REPUESTOS','Repuestos'),('REPARACION','Reparación')])
    version = models.PositiveIntegerField()
    huella = models.CharField(max_length=64)
    snapshot = models.JSONField()
    documento = models.OneToOneField('Documento', on_delete=P, null=True, blank=True, related_name='emision_operativa')
    hash_sha256 = models.CharField(max_length=64, blank=True)
    estado = models.CharField(max_length=12, default='RESERVADA', choices=[(s,s.capitalize()) for s in ('RESERVADA','GENERADA','ERROR')])
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente','tipo','version'], name='operacion_emision_version'),
            models.CheckConstraint(condition=(~models.Q(estado='GENERADA') | models.Q(documento__isnull=False)), name='operacion_emision_documento')]

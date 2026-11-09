"""Calibration structure; no industrial protocol or defaults are seeded."""
import uuid
from django.conf import settings
from django.db import models

P=models.PROTECT
ESTADOS=[(s,s.replace('_',' ').capitalize()) for s in ('NO_INICIADA','EN_PROCESO','PENDIENTE_REVISION','OBSERVADA','APROBADA','RECHAZADA')]


class PlantillaCalibracion(models.Model):
    empresa=models.ForeignKey('accounts.Empresa',on_delete=P)
    codigo=models.CharField(max_length=80)
    version=models.PositiveIntegerField()
    tipo_equipo=models.CharField(max_length=10,choices=[('REDUCTOR','Reductor'),('BOMBA','Bomba')])
    titulo=models.CharField(max_length=200)
    definicion=models.JSONField()
    documento=models.ForeignKey('Documento',on_delete=P,related_name='plantillas_calibracion')
    sustituye=models.ForeignKey('self',on_delete=P,null=True,blank=True)
    autor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['empresa','codigo','version'],name='cal_plantilla_version')]


class ValidacionPlantillaCalibracion(models.Model):
    plantilla=models.OneToOneField(PlantillaCalibracion,on_delete=P,related_name='validacion')
    responsable=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    fundamento=models.TextField()
    aprobacion=models.JSONField()
    clave=models.UUIDField(default=uuid.uuid4,unique=True)


class AplicabilidadCalibracion(models.Model):
    plantilla=models.ForeignKey(PlantillaCalibracion,on_delete=P,related_name='aplicabilidades')
    equipo=models.ForeignKey('Equipo',on_delete=P)
    fundamento=models.TextField()
    autor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['plantilla','equipo'],name='cal_aplicabilidad_unica')]


class Calibracion(models.Model):
    expediente=models.ForeignKey('ExpedienteMantenimiento',on_delete=P,related_name='calibraciones')
    diagnostico=models.ForeignKey('Diagnostico',on_delete=P)
    autorizacion=models.ForeignKey('DecisionComercial',on_delete=P)
    plantilla=models.ForeignKey(PlantillaCalibracion,on_delete=P)
    reparacion_snapshot=models.JSONField()
    revision=models.PositiveIntegerField()
    sustituye=models.ForeignKey('self',on_delete=P,null=True,blank=True)
    motivo_revision=models.TextField(blank=True)
    tecnico=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    fecha=models.DateTimeField(null=True,blank=True)
    parametros=models.JSONField(default=dict,blank=True)
    instrumentos=models.TextField(blank=True)
    observaciones=models.TextField(blank=True)
    resultados=models.TextField(blank=True)
    motivo_sin_evidencia=models.TextField(blank=True)
    estado=models.CharField(max_length=20,choices=ESTADOS,default='NO_INICIADA')
    version_edicion=models.PositiveIntegerField(default=1)
    enviado_en=models.DateTimeField(null=True,blank=True)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['expediente','revision'],name='cal_revision_unica')]


class MedicionCalibracion(models.Model):
    calibracion=models.ForeignKey(Calibracion,on_delete=P,related_name='mediciones')
    punto=models.CharField(max_length=80)
    valor=models.DecimalField(max_digits=30,decimal_places=12,null=True,blank=True)
    observaciones=models.TextField(blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['calibracion','punto'],name='cal_medicion_unica')]


class EvidenciaCalibracion(models.Model):
    calibracion=models.ForeignKey(Calibracion,on_delete=P,related_name='evidencias')
    documento=models.ForeignKey('Documento',on_delete=P,related_name='evidencias_calibracion')
    clase=models.CharField(max_length=12,choices=[('FOTO','Fotografía'),('CERTIFICADO','Documento del instrumento')])
    leyenda=models.TextField()
    autor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)


class DecisionCalibracion(models.Model):
    calibracion=models.OneToOneField(Calibracion,on_delete=P,related_name='decision')
    decision=models.CharField(max_length=20,choices=[(s,s.capitalize()) for s in ('APROBADA','OBSERVADA','RECHAZADA')])
    evaluaciones=models.JSONField(default=dict,blank=True)
    motivo=models.TextField()
    responsable=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)


class EmisionCalibracion(models.Model):
    calibracion=models.ForeignKey(Calibracion,on_delete=P,related_name='emisiones')
    version=models.PositiveIntegerField()
    snapshot=models.JSONField()
    huella=models.CharField(max_length=64)
    documento=models.OneToOneField('Documento',on_delete=P,null=True,blank=True,related_name='informe_calibracion')
    hash_sha256=models.CharField(max_length=64,blank=True)
    estado=models.CharField(max_length=12,default='RESERVADA',choices=[(s,s.capitalize()) for s in ('RESERVADA','GENERADA','ERROR')])
    error=models.CharField(max_length=255,blank=True)
    autor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['calibracion','version'],name='cal_emision_version'),
            models.CheckConstraint(condition=(~models.Q(estado='GENERADA')|models.Q(documento__isnull=False)),name='cal_emision_documento')]

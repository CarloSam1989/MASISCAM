"""Calibration structure; no industrial protocol or defaults are seeded."""
import uuid
from django.conf import settings
from django.db import models

P=models.PROTECT
# FINALIZADA: hoja elaborada y generada por TALLER sin revisión de ADMINISTRADOR (ficha técnica por configuración).
ESTADOS=[(s,s.replace('_',' ').capitalize()) for s in ('NO_INICIADA','EN_PROCESO','PENDIENTE_REVISION','OBSERVADA','APROBADA','RECHAZADA','FINALIZADA')]


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


class ToleranciaCalibracion(models.Model):
    """Límites técnicos de calibración por configuración del reductor (marca, modelo, ratio y rotación).

    Solo se agregan versiones: nunca se edita ni se borra una fila (guardas de aplicación y de base de datos). La vigente es la versión más alta
    de la configuración; retirarla crea una versión inactiva. No se siembran valores: cada límite lo documenta OFICINA o ADMINISTRADOR con su fuente."""
    UNIDADES_LONGITUD=[(u,u) for u in ('mm','µm','in','mil')]
    UNIDADES_PRESION=[(u,u) for u in ('psi','bar','kPa','MPa')]
    LIMITES=('backlash_min','backlash_max','tolerancia','calibracion_min','calibracion_max','presion_vertical_min','presion_vertical_max',
        'presion_horizontal_min','presion_horizontal_max')
    empresa=models.ForeignKey('accounts.Empresa',on_delete=P)
    tipo_equipo=models.CharField(max_length=10,choices=[('REDUCTOR','Reductor'),('BOMBA','Bomba')])
    marca=models.CharField(max_length=120)
    modelo=models.CharField(max_length=120)
    ratio=models.CharField(max_length=80)
    rotacion=models.CharField(max_length=80)
    configuracion=models.CharField(max_length=500)   # llave normalizada (minúsculas, espacios simples) para comparar con el equipo
    version=models.PositiveIntegerField()
    sustituye=models.ForeignKey('self',on_delete=P,null=True,blank=True)
    activa=models.BooleanField(default=True)
    backlash_min=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    backlash_max=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    tolerancia=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    calibracion_min=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    calibracion_max=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    presion_vertical_min=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    presion_vertical_max=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    presion_horizontal_min=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    presion_horizontal_max=models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    unidad_longitud=models.CharField(max_length=8,choices=UNIDADES_LONGITUD)
    unidad_presion=models.CharField(max_length=8,blank=True,choices=UNIDADES_PRESION)
    fuente=models.TextField()
    # Correspondencia documentada entre las 16 mediciones y un parámetro con límites; vacía = «Sin criterio de evaluación definido».
    mediciones_parametro=models.CharField(max_length=12,blank=True,choices=[('BACKLASH','Backlash'),('CALIBRACION','Calibración')])
    mediciones_criterio=models.TextField(blank=True)
    motivo=models.TextField(blank=True)
    huella=models.CharField(max_length=64)
    autor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['empresa','tipo_equipo','configuracion','version'],name='cal_tolerancia_version'),
            models.CheckConstraint(condition=models.Q(version__gte=1),name='cal_tolerancia_version_positiva'),
            *[models.CheckConstraint(condition=models.Q(**{f'{base}_min__isnull':True})|models.Q(**{f'{base}_max__isnull':True})|models.Q(**{f'{base}_min__lte':models.F(f'{base}_max')}),
                name=f'cal_tolerancia_rango_{base}') for base in ('backlash','calibracion','presion_vertical','presion_horizontal')]]


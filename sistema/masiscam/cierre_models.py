"""Final reports refer to existing sources; closure never rewrites the dossier."""
import uuid
from django.conf import settings
from django.db import models

P=models.PROTECT


class InformeFinal(models.Model):
    expediente=models.ForeignKey('ExpedienteMantenimiento',on_delete=P,related_name='informes_finales')
    calibracion=models.ForeignKey('Calibracion',on_delete=P)
    version=models.PositiveIntegerField()
    sustituye=models.ForeignKey('self',on_delete=P,null=True,blank=True)
    motivo=models.TextField(blank=True)
    snapshot=models.JSONField()
    huella=models.CharField(max_length=64)
    estado=models.CharField(max_length=20,default='BORRADOR',choices=[(s,s) for s in ('BORRADOR','PENDIENTE_REVISION','OBSERVADO','APROBADO','FINALIZADO')])
    documento=models.OneToOneField('Documento',on_delete=P,null=True,blank=True,related_name='informe_final')
    hash_sha256=models.CharField(max_length=64,blank=True)
    error=models.CharField(max_length=255,blank=True)
    autor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    enviado_en=models.DateTimeField(null=True,blank=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['expediente','version'],name='final_version_unica'),
            models.CheckConstraint(condition=models.Q(version__gt=0),name='final_version_positiva'),
            models.CheckConstraint(condition=models.Q(estado='BORRADOR')|models.Q(documento__isnull=False,enviado_en__isnull=False),name='final_emitido_documento')]


class RevisionInformeFinal(models.Model):
    informe=models.OneToOneField(InformeFinal,on_delete=P,related_name='revision')
    decision=models.CharField(max_length=10,choices=[('APROBADO','Aprobado'),('OBSERVADO','Observado')])
    motivo=models.TextField()
    responsable=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)


class CierreExpediente(models.Model):
    expediente=models.OneToOneField('ExpedienteMantenimiento',on_delete=P,related_name='cierre')
    informe=models.OneToOneField(InformeFinal,on_delete=P,related_name='cierre')
    responsable=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=P)
    creado_en=models.DateTimeField(auto_now_add=True)
    motivo=models.TextField()
    huella=models.CharField(max_length=64)
    clave=models.UUIDField(default=uuid.uuid4,unique=True)

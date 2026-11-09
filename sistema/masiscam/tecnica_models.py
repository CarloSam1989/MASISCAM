"""Phase 3 business relations. Binary storage remains Documento."""
import uuid
from django.conf import settings
from django.db import models

PROTECT = models.PROTECT
ESTADOS = [(s, s.replace('_', ' ').capitalize()) for s in
    ('EN_PROCESO', 'PENDIENTE_APROBACION', 'APROBADO', 'OBSERVADO', 'RECHAZADO', 'FINALIZADO')]


class AsignacionTecnica(models.Model):
    expediente = models.ForeignKey('ExpedienteMantenimiento', on_delete=PROTECT, related_name='asignaciones_tecnicas')
    tecnico = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name='asignaciones_tecnicas')
    asignado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name='+')
    inicio = models.DateTimeField(auto_now_add=True)
    fin = models.DateTimeField(null=True, blank=True)
    motivo = models.TextField()
    clave = models.UUIDField(default=uuid.uuid4, unique=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente'], condition=models.Q(fin__isnull=True), name='tecnica_asignacion_activa_unica')]


class RevisionTecnicaBase(models.Model):
    expediente = models.ForeignKey('ExpedienteMantenimiento', on_delete=PROTECT)
    revision = models.PositiveIntegerField()
    tecnico = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)
    fecha = models.DateField(null=True, blank=True)
    enviado_en = models.DateTimeField(null=True, blank=True)
    version_edicion = models.PositiveIntegerField(default=1)
    estado = models.CharField(max_length=24, choices=ESTADOS, default='EN_PROCESO')
    motivo_revision = models.TextField(blank=True)
    motivo_sin_evidencia = models.TextField(blank=True)
    observaciones = models.TextField(blank=True)
    sustituye = models.ForeignKey('self', on_delete=PROTECT, null=True, blank=True, related_name='correcciones')

    class Meta:
        abstract = True


class Inspeccion(RevisionTecnicaBase):
    estado_fisico = models.TextField(blank=True)
    condiciones_generales = models.TextField(blank=True)
    hallazgos = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente', 'revision'], name='tecnica_inspeccion_revision_unica')]


class Diagnostico(RevisionTecnicaBase):
    inspeccion = models.ForeignKey(Inspeccion, on_delete=PROTECT, related_name='diagnosticos')
    fallas = models.TextField(blank=True)
    causas = models.TextField(blank=True)
    componentes_afectados = models.TextField(blank=True)
    recomendaciones = models.TextField(blank=True)
    conclusiones = models.TextField(blank=True)
    repuestos = models.CharField(max_length=20, default='PENDIENTE', choices=[('PENDIENTE','Pendiente'),('REQUERIDOS','Requeridos'),('SIN_REPUESTOS','No se requieren repuestos')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente', 'revision'], name='tecnica_diagnostico_revision_unica')]


class EtapaExpediente(models.Model):
    expediente = models.ForeignKey('ExpedienteMantenimiento', on_delete=PROTECT, related_name='etapas_tecnicas')
    codigo = models.CharField(max_length=12, choices=[('INSPECCION','Inspección'),('DIAGNOSTICO','Diagnóstico')])
    estado = models.CharField(max_length=24, choices=ESTADOS, default='EN_PROCESO')
    inspeccion = models.ForeignKey(Inspeccion, on_delete=PROTECT, null=True, blank=True, related_name='+')
    diagnostico = models.ForeignKey(Diagnostico, on_delete=PROTECT, null=True, blank=True, related_name='+')
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente','codigo'], name='tecnica_etapa_unica'),
            models.CheckConstraint(condition=(models.Q(codigo='INSPECCION',inspeccion__isnull=False,diagnostico__isnull=True)|models.Q(codigo='DIAGNOSTICO',inspeccion__isnull=True,diagnostico__isnull=False)),name='tecnica_etapa_destino')]


class EvidenciaTecnica(models.Model):
    inspeccion = models.ForeignKey(Inspeccion, on_delete=PROTECT, null=True, blank=True, related_name='evidencias')
    diagnostico = models.ForeignKey(Diagnostico, on_delete=PROTECT, null=True, blank=True, related_name='evidencias')
    documento = models.ForeignKey('Documento', on_delete=PROTECT, related_name='evidencias_tecnicas')
    clase = models.CharField(max_length=20, choices=[(s,s.replace('_',' ').capitalize()) for s in ('ESTADO_FISICO','HALLAZGO','DANO','DETALLE','OTRA','REFERENCIA')])
    referencia = models.ForeignKey('ReferenciaTecnica', on_delete=PROTECT, null=True, blank=True, related_name='citas_tecnicas')
    leyenda = models.TextField()
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(inspeccion__isnull=False,diagnostico__isnull=True)|models.Q(inspeccion__isnull=True,diagnostico__isnull=False)), name='tecnica_evidencia_destino')]


class DecisionTecnica(models.Model):
    diagnostico = models.OneToOneField(Diagnostico, on_delete=PROTECT, related_name='decision')
    solicitud = models.UUIDField(default=uuid.uuid4, unique=True)
    decision = models.CharField(max_length=12, choices=[(s,s.capitalize()) for s in ('APROBADO','OBSERVADO','RECHAZADO')])
    motivo = models.TextField(blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)


class ReferenciaTecnica(models.Model):
    empresa = models.ForeignKey('accounts.Empresa', on_delete=PROTECT)
    documento = models.ForeignKey('Documento', on_delete=PROTECT, related_name='referencias_tecnicas')
    codigo = models.CharField(max_length=80)
    revision = models.PositiveIntegerField()
    tipo = models.CharField(max_length=6, choices=[('PLANO','Plano'),('MANUAL','Manual')])
    titulo = models.CharField(max_length=200)
    marca = models.CharField(max_length=120, blank=True)
    modelo = models.CharField(max_length=120, blank=True)
    ratio = models.CharField(max_length=80, blank=True)
    version_fabricante = models.CharField(max_length=100, blank=True)
    procedencia = models.TextField()
    estado = models.CharField(max_length=12, default='BORRADOR', choices=[(s,s.capitalize()) for s in ('BORRADOR','PUBLICADA','RETIRADA')])
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)
    sustituye = models.ForeignKey('self', on_delete=PROTECT, null=True, blank=True)
    autorizacion_uso = models.TextField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['empresa','codigo','revision'],name='tecnica_referencia_revision_unica')]

    def __str__(self):
        return f'{self.codigo} r{self.revision} · {self.titulo}'


class ComponenteTecnico(models.Model):
    empresa = models.ForeignKey('accounts.Empresa', on_delete=PROTECT)
    codigo = models.CharField(max_length=80)
    fabricante = models.CharField(max_length=120, blank=True)
    numero_parte = models.CharField(max_length=120, blank=True)
    descripcion = models.TextField()
    unidad = models.CharField(max_length=80, blank=True)
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['empresa','codigo'],name='tecnica_componente_codigo_unico')]

    def __str__(self):
        return f'{self.codigo} · {self.descripcion[:80]}'


class ParteReferenciaTecnica(models.Model):
    referencia = models.ForeignKey(ReferenciaTecnica, on_delete=PROTECT, related_name='partes')
    componente = models.ForeignKey(ComponenteTecnico, on_delete=PROTECT, null=True, blank=True)
    posicion = models.CharField(max_length=80)
    numero_parte = models.CharField(max_length=120, blank=True)
    descripcion = models.TextField()
    unidad = models.CharField(max_length=80, blank=True)
    cantidad_referencia = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    pagina = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['referencia','posicion'],name='tecnica_parte_posicion_unica')]

    def __str__(self):
        return f'{self.referencia.codigo} r{self.referencia.revision} · {self.posicion} · {self.descripcion[:60]}'


class AplicabilidadReferenciaEquipo(models.Model):
    referencia = models.ForeignKey(ReferenciaTecnica, on_delete=PROTECT, related_name='aplicabilidades')
    equipo = models.ForeignKey('Equipo', on_delete=PROTECT)
    fundamento = models.TextField()
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)
    activa = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['referencia','equipo'],name='tecnica_aplicabilidad_unica')]


class PiezaRequerida(models.Model):
    diagnostico = models.ForeignKey(Diagnostico, on_delete=PROTECT, related_name='piezas')
    parte = models.ForeignKey(ParteReferenciaTecnica, on_delete=PROTECT, null=True, blank=True)
    origen = models.CharField(max_length=10, choices=[('MANUAL','Manual'),('BIBLIOTECA','Biblioteca')])
    descripcion = models.TextField()
    numero_parte = models.CharField(max_length=120, blank=True)
    cantidad = models.DecimalField(max_digits=20, decimal_places=6)
    unidad = models.CharField(max_length=80, blank=True)
    motivo_unidad_pendiente = models.TextField(blank=True)
    condicion = models.TextField()
    accion = models.TextField()
    motivo_manual = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='tecnica_pieza_cantidad_positiva'),
            models.CheckConstraint(condition=(models.Q(origen='MANUAL',parte__isnull=True)|models.Q(origen='BIBLIOTECA',parte__isnull=False)),name='tecnica_pieza_origen')]


class EmisionInformeTecnico(models.Model):
    expediente = models.ForeignKey('ExpedienteMantenimiento', on_delete=PROTECT, related_name='informes_tecnicos')
    diagnostico = models.ForeignKey(Diagnostico, on_delete=PROTECT)
    tipo = models.CharField(max_length=8, default='INICIAL')
    version = models.PositiveIntegerField()
    clave = models.UUIDField(default=uuid.uuid4, unique=True)
    huella = models.CharField(max_length=64)
    snapshot = models.JSONField()
    documento = models.OneToOneField('Documento', on_delete=PROTECT, null=True, blank=True, related_name='informe_tecnico')
    hash_sha256 = models.CharField(max_length=64, blank=True)
    estado = models.CharField(max_length=12, default='RESERVADA', choices=[(s,s.capitalize()) for s in ('RESERVADA','GENERANDO','GENERADA','ERROR')])
    error = models.CharField(max_length=255, blank=True)
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expediente','tipo','version'],name='tecnica_informe_version_unica'),
            models.CheckConstraint(condition=(~models.Q(estado='GENERADA')|models.Q(documento__isnull=False)),name='tecnica_informe_generado_documento')]

import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

from accounts.models import Empresa, Perfil


def generar_token_publico():
    return secrets.token_urlsafe(32)


class RolMasiscam(models.Model):
    class Rol(models.TextChoices):
        ADMINISTRADOR = "ADMIN", "Administrador MASISCAM"
        TECNICO = "TECNICO", "Técnico"
        CONSULTA = "CONSULTA", "Consulta"

    perfil = models.OneToOneField(Perfil, on_delete=models.CASCADE, related_name="rol_masiscam")
    rol = models.CharField(max_length=12, choices=Rol.choices, default=Rol.CONSULTA)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "rol MASISCAM"
        permissions = [
            ("archivar_proyecto", "Puede archivar proyectos MASISCAM"),
            ("administrar_equipos", "Puede administrar equipos MASISCAM"),
            ("subir_documentos", "Puede subir documentos MASISCAM"),
            ("reemplazar_documentos", "Puede reemplazar o archivar documentos MASISCAM"),
            ("generar_qr", "Puede generar y descargar QR MASISCAM"),
            ("configurar_visibilidad", "Puede configurar visibilidad pública MASISCAM"),
            ("ver_historial", "Puede ver historial MASISCAM"),
        ]

    def __str__(self):
        return f"{self.perfil} — {self.get_rol_display()}"


class Proyecto(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Borrador"
        ACTIVO = "ACTIVO", "Activo"
        FINALIZADO = "FINALIZADO", "Finalizado"
        ARCHIVADO = "ARCHIVADO", "Archivado"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="proyectos_masiscam")
    razon_social = models.CharField(max_length=200, blank=True, default="")
    codigo = models.CharField(max_length=60)
    nombre = models.CharField(max_length=200)
    cliente = models.CharField(max_length=200)
    identificacion_cliente = models.CharField(max_length=20, blank=True)
    responsable = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    direccion = models.CharField(max_length=300, blank=True)
    ciudad = models.CharField(max_length=100, blank=True)
    provincia = models.CharField(max_length=100, blank=True)
    ubicacion_maps = models.URLField(blank=True)
    fecha_inicio = models.DateField()
    fecha_finalizacion = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.BORRADOR)
    imagen_principal = models.ImageField(upload_to="masiscam/proyectos/%Y/%m/", blank=True)
    observaciones = models.TextField(blank=True)
    drive_folder_id = models.CharField(max_length=255, blank=True, editable=False)
    drive_folder_url = models.URLField(blank=True, editable=False)
    token_publico = models.CharField(max_length=64, unique=True, default=generar_token_publico, editable=False)
    pagina_publica_activa = models.BooleanField(default=False)
    publicar_cliente = models.BooleanField(default=False)
    publicar_ubicacion = models.BooleanField(default=False)
    publicar_descripcion = models.BooleanField(default=True)
    publicar_equipos = models.BooleanField(default=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="proyectos_masiscam_creados")
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-actualizado_en"]
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="masiscam_proyecto_codigo_empresa_uniq")]
        permissions = [("regenerar_token", "Puede regenerar el token público MASISCAM")]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"

    def regenerar_token(self):
        self.token_publico = generar_token_publico()


class Cliente(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="clientes_masiscam")
    nombre_comercial = models.CharField("Cliente / nombre comercial", max_length=200)
    ruc = models.CharField("RUC", max_length=20)
    razon_social = models.CharField("Razón social", max_length=200)
    correo = models.EmailField("Correo", blank=True)
    telefono = models.CharField("Teléfono", max_length=40, blank=True)

    class Meta:
        ordering = ["nombre_comercial", "pk"]
        constraints = [models.UniqueConstraint(fields=["empresa", "ruc"], name="masiscam_cliente_empresa_ruc_uniq")]

    def __str__(self):
        return f"{self.nombre_comercial} — {self.ruc}"


class Equipo(models.Model):
    class TipoProducto(models.TextChoices):
        REDUCTOR = "REDUCTOR", "Reductores"

    class Estado(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        INACTIVO = "INACTIVO", "Inactivo"
        MANTENIMIENTO = "MANTENIMIENTO", "En mantenimiento"

    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name="equipos")
    tipo_producto = models.CharField(max_length=32, choices=TipoProducto.choices, default=TipoProducto.REDUCTOR, editable=False)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, null=True, blank=True, related_name="equipos")
    drive_folder_id = models.CharField(max_length=255, blank=True, editable=False)
    drive_folder_url = models.URLField(blank=True, editable=False)
    drive_error = models.TextField(blank=True, editable=False)
    nombre = models.CharField(max_length=200)
    fabricante = models.CharField(max_length=150, blank=True)
    marca = models.CharField(max_length=120, blank=True)
    modelo = models.CharField(max_length=120, blank=True)
    numero_serie = models.CharField(max_length=120, blank=True)
    ratio = models.CharField(max_length=80, blank=True)
    potencia_hp = models.CharField(max_length=80, blank=True)
    rpm = models.CharField(max_length=80, blank=True)
    service_factor = models.CharField(max_length=80, blank=True)
    rotacion = models.CharField(max_length=80, blank=True)
    tipo_aceite = models.CharField(max_length=180, blank=True)
    informacion_tecnica = models.TextField(blank=True)
    ubicacion = models.CharField(max_length=200, blank=True)
    sector = models.CharField(max_length=200, blank=True, default="")
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.ACTIVO)
    fotografia_general = models.ImageField(upload_to="masiscam/equipos/%Y/%m/", blank=True)
    fotografia_placa = models.ImageField(upload_to="masiscam/placas/%Y/%m/", blank=True)
    observaciones = models.TextField(blank=True)
    visible_publico = models.BooleanField(default=True)
    token_publico = models.CharField(max_length=64, unique=True, default=generar_token_publico, editable=False)
    consulta_publica_activa = models.BooleanField(default=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre", "id"]

    def __str__(self):
        return self.nombre

    @property
    def razon_social_cliente(self):
        return self.cliente.razon_social if self.cliente_id else self.proyecto.razon_social

    @property
    def ruc_cliente(self):
        return self.cliente.ruc if self.cliente_id else self.proyecto.identificacion_cliente

    def regenerar_token(self):
        self.token_publico = generar_token_publico()

    def clean(self):
        super().clean()
        errores = {}
        obligatorios = {
            "nombre": "El número de equipo es obligatorio.",
            "ubicacion": "La estación es obligatoria.",
            "sector": "El sector es obligatorio.",
            "marca": "La marca es obligatoria.",
            "modelo": "El modelo es obligatorio.",
            "numero_serie": "El número de serie es obligatorio.",
            "potencia_hp": "La potencia es obligatoria.",
        }
        for campo, mensaje in obligatorios.items():
            if not (getattr(self, campo, "") or "").strip():
                errores[campo] = mensaje
        if self.proyecto_id:
            if not (self.proyecto.nombre or "").strip():
                errores["proyecto"] = "El nombre de la camaronera es obligatorio."
            if self.cliente_id and self.cliente.empresa_id != self.proyecto.empresa_id:
                errores["cliente"] = "El cliente debe pertenecer a la misma empresa."
            if not self.cliente_id and not (self.proyecto.razon_social or "").strip():
                errores["proyecto"] = "La razón social es obligatoria."
            numero_qs = Equipo.objects.filter(
                proyecto_id=self.proyecto_id,
                nombre__iexact=(self.nombre or "").strip(),
            ).exclude(pk=self.pk)
            serie_qs = Equipo.objects.filter(
                proyecto__empresa_id=self.proyecto.empresa_id,
                numero_serie__iexact=(self.numero_serie or "").strip(),
            ).exclude(pk=self.pk)
            if self.nombre and numero_qs.exists():
                errores["nombre"] = "Ya existe este número de equipo en la camaronera."
            if self.numero_serie and serie_qs.exists():
                errores["numero_serie"] = "Ya existe esta serie en la empresa."
        if errores:
            raise ValidationError(errores)


class RegistroEquipo(models.Model):
    class Tipo(models.TextChoices):
        NUEVO = "NUEVO", "Nuevo"
        ASISTENCIA = "ASISTENCIA", "Asistencia"
        GARANTIA = "GARANTIA", "Garantía"

        MANTENIMIENTO = "MANTENIMIENTO", "Mantenimiento"
        REPARACION = "REPARACION", "Reparación"

    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name="registros")
    tipo = models.CharField(max_length=13, choices=Tipo.choices)
    fecha = models.DateField()
    observacion = models.TextField(blank=True)
    clave_creacion = models.UUIDField(default=uuid.uuid4, editable=False)
    drive_folder_id = models.CharField(max_length=255, blank=True, editable=False)
    drive_folder_url = models.URLField(blank=True, editable=False)
    drive_error = models.TextField(blank=True, editable=False)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-creado_en", "-pk"]
        constraints = [models.UniqueConstraint(fields=["equipo", "clave_creacion"], name="masiscam_registro_envio_uniq")]


class Documento(models.Model):
    class Categoria(models.TextChoices):
        FOTOGRAFIAS = "FOTOGRAFIAS", "Fotografías"
        PLACAS = "PLACAS", "Placas técnicas"
        PLANOS = "PLANOS", "Planos"
        MANUALES = "MANUALES", "Manuales"
        FICHAS = "FICHAS", "Fichas técnicas"
        CERTIFICADOS = "CERTIFICADOS", "Certificados"
        INFORMES = "INFORMES", "Informes"
        MANTENIMIENTOS = "MANTENIMIENTOS", "Mantenimientos"
        GARANTIAS = "GARANTIAS", "Garantías"
        ACTAS = "ACTAS", "Actas de entrega"
        VIDEOS = "VIDEOS", "Videos"
        OTROS = "OTROS", "Otros documentos"

    class Sincronizacion(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        SUBIENDO = "SUBIENDO", "Subiendo"
        SINCRONIZADO = "SINCRONIZADO", "Sincronizado"
        ERROR = "ERROR", "Error"
        ARCHIVADO = "ARCHIVADO", "Archivado"

    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name="documentos")
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name="documentos", null=True, blank=True)
    categoria = models.CharField(max_length=20, choices=Categoria.choices)
    titulo = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    archivo = models.FileField(upload_to="masiscam/documentos/%Y/%m/", validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp", "pdf", "doc", "docx", "xls", "xlsx", "dwg", "dxf", "mp4", "mov"] )])
    nombre_original = models.CharField(max_length=255, editable=False)
    tipo_mime = models.CharField(max_length=150, editable=False)
    tamano = models.PositiveBigIntegerField(default=0, editable=False)
    hash_sha256 = models.CharField(max_length=64, editable=False)
    drive_upload_id = models.CharField(max_length=255, blank=True, editable=False)
    drive_file_id = models.CharField(max_length=255, blank=True, editable=False)
    drive_view_url = models.URLField(blank=True, editable=False)
    drive_download_url = models.URLField(blank=True, editable=False)
    publico = models.BooleanField(default=False)
    estado_sincronizacion = models.CharField(max_length=15, choices=Sincronizacion.choices, default=Sincronizacion.PENDIENTE)
    error_sincronizacion = models.TextField(blank=True, editable=False)
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="documentos_masiscam")
    cargado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cargado_en"]
        constraints = [models.UniqueConstraint(fields=["proyecto", "hash_sha256"], condition=models.Q(estado_sincronizacion__in=["PENDIENTE", "SUBIENDO", "SINCRONIZADO"]), name="masiscam_documento_hash_activo_uniq")]

    def __str__(self):
        return self.titulo


class Auditoria(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="auditoria_masiscam")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name="historial", null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    accion = models.CharField(max_length=80)
    objeto_tipo = models.CharField(max_length=80)
    objeto_id = models.CharField(max_length=80, blank=True)
    detalle = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        indexes = [models.Index(fields=["empresa", "-creado_en"])]

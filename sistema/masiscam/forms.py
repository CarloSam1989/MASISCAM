import hashlib
import uuid
from datetime import date

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Cliente, Documento, Equipo, Proyecto, RegistroEquipo


def validar_fotografia(archivo):
    from pathlib import Path
    image = getattr(archivo, "image", None)
    formats = {"JPEG": {"jpg", "jpeg"}, "PNG": {"png"}, "WEBP": {"webp"}}
    if not image or Path(archivo.name).suffix.lower().lstrip(".") not in formats.get(image.format, set()):
        raise ValidationError("Use una fotografía JPEG, PNG o WebP con extensión correcta.")
    if archivo.size > settings.MASISCAM_MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError("La fotografía excede el límite de carga.")
    if image.width * image.height > 25_000_000:
        raise ValidationError("La fotografía excede 25 megapíxeles; reduzca su resolución.")


class MasiscamImageFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.ImageField):
                field.validators.append(validar_fotografia)


class RegistroEquipoForm(forms.ModelForm):
    clave_creacion = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    class Meta:
        model = RegistroEquipo
        fields = ("tipo", "fecha", "observacion")
        labels = {"tipo": "Tipo de registro", "fecha": "Fecha", "observacion": "Observación (opcional)"}
        widgets = {
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "observacion": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("fecha", timezone.localdate())


def datos_cliente(cliente):
    return {"id": cliente.pk, "nombre_comercial": cliente.nombre_comercial,
            "ruc": cliente.ruc, "razon_social": cliente.razon_social,
            "correo": cliente.correo, "telefono": cliente.telefono}


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ("nombre_comercial", "ruc", "razon_social", "correo", "telefono")

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.empresa = empresa
        for campo in self.fields.values():
            campo.widget.attrs["class"] = "form-control"

    def clean_ruc(self):
        ruc = self.cleaned_data["ruc"]
        if Cliente.objects.filter(empresa=self.instance.empresa, ruc=ruc).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Ya existe un cliente con este RUC en la empresa.")
        return ruc


class FichaEquipoForm(MasiscamImageFormMixin, forms.Form):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), widget=forms.HiddenInput)
    razon_social = forms.CharField(label="Razón social", max_length=200, required=False)
    camaronera = forms.CharField(label="Nombre de la camaronera", max_length=200)
    ruc = forms.CharField(label="RUC", max_length=20, required=False)
    estacion = forms.CharField(label="Estación", max_length=200)
    sector = forms.CharField(label="Sector", max_length=200)
    numero_equipo = forms.CharField(label="Número de equipo", max_length=200)
    marca = forms.CharField(label="Marca", max_length=120)
    modelo = forms.CharField(label="Modelo", max_length=120)
    numero_serie = forms.CharField(label="Número de serie", max_length=120)
    potencia = forms.CharField(label="Potencia", max_length=80)
    ratio = forms.CharField(label="Relación motriz", max_length=80, required=False)
    service_factor = forms.CharField(label="SF", max_length=80, required=False)
    rpm = forms.CharField(label="RPM", max_length=80, required=False)
    rotacion = forms.CharField(label="Rotación", max_length=80, required=False)
    tipo_aceite = forms.CharField(label="Tipo de aceite", max_length=180, required=False)
    fotografia_equipo = forms.ImageField(label="Fotografía del equipo", required=False)
    fotografia_placa = forms.ImageField(label="Fotografía de la placa", required=False)
    observaciones = forms.CharField(label="Observaciones", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    consulta_publica_activa = forms.BooleanField(label="Consulta pública activa", required=False)

    def __init__(self, *args, empresa, equipo=None, tipo_producto=Equipo.TipoProducto.REDUCTOR, **kwargs):
        self.tipo_producto = equipo.tipo_producto if equipo else tipo_producto
        self.empresa = empresa
        self.equipo = equipo
        if equipo and "initial" not in kwargs:
            kwargs["initial"] = {
                "cliente": equipo.cliente_id,
                "razon_social": equipo.razon_social_cliente,
                "camaronera": equipo.proyecto.nombre,
                "ruc": equipo.ruc_cliente,
                "estacion": equipo.ubicacion,
                "sector": equipo.sector,
                "numero_equipo": equipo.nombre,
                "marca": equipo.marca,
                "modelo": equipo.modelo,
                "numero_serie": equipo.numero_serie,
                "potencia": equipo.potencia_hp,
                "ratio": equipo.ratio,
                "service_factor": equipo.service_factor,
                "rpm": equipo.rpm,
                "rotacion": equipo.rotacion,
                "tipo_aceite": equipo.tipo_aceite,
                "fotografia_equipo": equipo.fotografia_general,
                "fotografia_placa": equipo.fotografia_placa,
                "observaciones": equipo.observaciones,
                "consulta_publica_activa": equipo.consulta_publica_activa,
            }
        super().__init__(*args, **kwargs)
        if equipo is None:
            self.fields["consulta_publica_activa"].initial = True
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa)
        self.permite_sin_cliente = bool(equipo and not equipo.cliente_id)
        self.fields["cliente"].required = not self.permite_sin_cliente
        valor_cliente = self["cliente"].value()
        try:
            seleccionado = self.fields["cliente"].queryset.filter(pk=valor_cliente).first() if valor_cliente else None
        except (ValueError, TypeError):
            seleccionado = None
        self.cliente_inicial = datos_cliente(seleccionado) if seleccionado else None
        for nombre, campo in self.fields.items():
            campo.widget.attrs["class"] = "form-check-input" if isinstance(campo, forms.BooleanField) else "form-control"
        self.fields["ruc"].widget.attrs.update({"autocomplete": "off", "inputmode": "numeric", "aria-describedby": "cliente-estado"})

    def clean(self):
        datos = super().clean()
        if not datos.get("cliente") and not datos.get("razon_social"):
            self.add_error("razon_social", "Seleccione un cliente o ingrese la raz\u00f3n social.")
        numero = (datos.get("numero_equipo") or "").strip()
        serie = (datos.get("numero_serie") or "").strip()
        duplicados_numero = Equipo.objects.filter(
            proyecto__empresa=self.empresa, ubicacion__iexact=(datos.get("estacion") or "").strip(),
            nombre__iexact=numero,
        )
        duplicados_serie = Equipo.objects.filter(proyecto__empresa=self.empresa, numero_serie__iexact=serie)
        if self.equipo:
            duplicados_numero = duplicados_numero.exclude(pk=self.equipo.pk)
            duplicados_serie = duplicados_serie.exclude(pk=self.equipo.pk)
        if numero and duplicados_numero.exists():
            self.add_error("numero_equipo", "Ya existe este número de equipo en esta estación de la empresa.")
        if serie and duplicados_serie.exists():
            self.add_error("numero_serie", "Ya existe esta serie en la empresa.")
        return datos

    @transaction.atomic
    def save(self, *, usuario):
        datos = self.cleaned_data
        cliente = datos.get("cliente")
        camaronera = datos["camaronera"].strip()
        if self.equipo:
            equipo = self.equipo
            proyecto = equipo.proyecto
            if "camaronera" in self.changed_data:
                # Cambiar solo la relacion del equipo, nunca renombrar un proyecto compartido.
                destino = Proyecto.objects.filter(empresa=self.empresa, nombre__iexact=camaronera).first() if cliente else None
                if destino is None:
                    destino = Proyecto.objects.create(
                        empresa=self.empresa,
                        codigo=f"CAM-{uuid.uuid4().hex[:12].upper()}",
                        nombre=camaronera,
                        razon_social="" if cliente else datos["razon_social"].strip(),
                        cliente="" if cliente else datos["razon_social"].strip(),
                        identificacion_cliente="" if cliente else datos["ruc"].strip(),
                        responsable=proyecto.responsable,
                        fecha_inicio=proyecto.fecha_inicio,
                        creado_por=usuario,
                    )
                proyecto = destino
        else:
            proyecto = Proyecto.objects.filter(empresa=self.empresa, nombre__iexact=camaronera).first()
            if proyecto is None:
                proyecto = Proyecto.objects.create(
                    empresa=self.empresa,
                    codigo=f"CAM-{uuid.uuid4().hex[:12].upper()}",
                    nombre=camaronera,
                    razon_social="" if cliente else datos["razon_social"].strip(),
                    cliente="" if cliente else datos["razon_social"].strip(),
                    responsable=self.empresa.nombre,
                    fecha_inicio=date.today(),
                    creado_por=usuario,
                )
            equipo = Equipo(proyecto=proyecto, tipo_producto=self.tipo_producto)
        # Los datos del cliente se consultan por relacion, sin copiarlos al proyecto.
        if not cliente:
            campos = []
            if not self.equipo or {"camaronera", "razon_social", "cliente"}.intersection(self.changed_data):
                proyecto.razon_social = datos["razon_social"].strip()
                proyecto.cliente = proyecto.razon_social
                campos.extend(["razon_social", "cliente"])
            if not self.equipo or {"ruc", "cliente"}.intersection(self.changed_data):
                proyecto.identificacion_cliente = datos["ruc"].strip()
                campos.append("identificacion_cliente")
            if campos:
                proyecto.save(update_fields=campos + ["actualizado_en"])
        equipo.proyecto = proyecto
        equipo.cliente = cliente
        equipo.nombre = datos["numero_equipo"].strip()
        equipo.ubicacion = datos["estacion"].strip()
        equipo.sector = datos["sector"].strip()
        equipo.marca = datos["marca"].strip()
        equipo.modelo = datos["modelo"].strip()
        equipo.numero_serie = datos["numero_serie"].strip()
        equipo.potencia_hp = datos["potencia"].strip()
        for campo in ("ratio", "service_factor", "rpm", "rotacion", "tipo_aceite"):
            if not self.equipo or campo in self.changed_data:
                setattr(equipo, campo, datos[campo])
        equipo.observaciones = datos.get("observaciones", "").strip()
        equipo.consulta_publica_activa = datos.get("consulta_publica_activa", False)
        if datos.get("fotografia_equipo") is False:
            equipo.fotografia_general = ""
        elif datos.get("fotografia_equipo"):
            equipo.fotografia_general = datos["fotografia_equipo"]
        if datos.get("fotografia_placa") is False:
            equipo.fotografia_placa = ""
        elif datos.get("fotografia_placa"):
            equipo.fotografia_placa = datos["fotografia_placa"]
        equipo.full_clean(exclude=["fotografia_general", "fotografia_placa"])
        equipo.save()
        return equipo


class ProyectoForm(MasiscamImageFormMixin, forms.ModelForm):
    class Meta:
        model = Proyecto
        exclude = ("empresa", "creado_por", "drive_folder_id", "drive_folder_url", "token_publico", "pagina_publica_activa", "publicar_cliente", "publicar_ubicacion", "publicar_descripcion", "publicar_equipos")
        widgets = {"fecha_inicio": forms.DateInput(attrs={"type": "date"}), "fecha_finalizacion": forms.DateInput(attrs={"type": "date"}), "descripcion": forms.Textarea(attrs={"rows": 3}), "observaciones": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("fecha_inicio"), datos.get("fecha_finalizacion")
        if inicio and fin and fin < inicio:
            self.add_error("fecha_finalizacion", "No puede ser anterior a la fecha de inicio.")
        return datos


class EquipoForm(MasiscamImageFormMixin, forms.ModelForm):
    ruc = forms.CharField(label="RUC", max_length=20, required=False)

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.proyecto_id:
            empresa = empresa or self.instance.proyecto.empresa
            self.initial.setdefault("ruc", self.instance.ruc_cliente)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa) if empresa else Cliente.objects.none()

    def guardar_ruc(self):
        if not self.instance.cliente_id and "ruc" in self.changed_data:
            proyecto = self.instance.proyecto
            proyecto.identificacion_cliente = self.cleaned_data["ruc"].strip()
            proyecto.save(update_fields=["identificacion_cliente", "actualizado_en"])

    @transaction.atomic
    def save(self, commit=True):
        equipo = super().save(commit=commit)
        if commit:
            self.guardar_ruc()
        return equipo

    class Meta:
        model = Equipo
        exclude = ("proyecto",)
        labels = {"ratio": "Relación motriz", "service_factor": "SF", "rpm": "RPM", "rotacion": "Rotación"}
        widgets = {"informacion_tecnica": forms.Textarea(attrs={"rows": 3}), "observaciones": forms.Textarea(attrs={"rows": 3})}


class VisibilidadProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ("pagina_publica_activa", "publicar_cliente", "publicar_ubicacion", "publicar_descripcion", "publicar_equipos")


class DocumentoForm(forms.ModelForm):
    MIME_PERMITIDOS = {
        "image/jpeg", "image/png", "image/webp", "application/pdf", "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/acad", "image/vnd.dwg", "application/dxf", "image/vnd.dxf", "video/mp4", "video/quicktime",
    }

    class Meta:
        model = Documento
        fields = ("equipo", "categoria", "titulo", "descripcion", "archivo", "publico")

    def __init__(self, *args, proyecto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.proyecto = proyecto
        self.fields["equipo"].queryset = proyecto.equipos.all() if proyecto else Equipo.objects.none()

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        limite = settings.MASISCAM_MAX_UPLOAD_MB * 1024 * 1024
        if archivo.size > limite:
            raise ValidationError(f"El archivo excede el límite de {settings.MASISCAM_MAX_UPLOAD_MB} MB.")
        mime = (getattr(archivo, "content_type", "") or "").lower()
        if mime not in self.MIME_PERMITIDOS:
            raise ValidationError("El tipo MIME del archivo no está permitido.")
        from pathlib import Path
        extension = Path(archivo.name).suffix.lower().lstrip(".")
        mime_por_extension = {
            "jpg": {"image/jpeg"}, "jpeg": {"image/jpeg"}, "png": {"image/png"}, "webp": {"image/webp"},
            "pdf": {"application/pdf"}, "doc": {"application/msword"}, "xls": {"application/vnd.ms-excel"},
            "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
            "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            "dwg": {"application/acad", "image/vnd.dwg"}, "dxf": {"application/dxf", "image/vnd.dxf"},
            "mp4": {"video/mp4"}, "mov": {"video/quicktime"},
        }
        if mime not in mime_por_extension.get(extension, set()):
            raise ValidationError("La extensión no coincide con el tipo declarado.")
        cabecera = archivo.read(16)
        archivo.seek(0)
        firmas = {"application/pdf": b"%PDF", "image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG", "image/webp": b"RIFF"}
        firma = firmas.get(mime)
        if firma and not cabecera.startswith(firma):
            raise ValidationError("El contenido no coincide con el tipo MIME declarado.")
        if mime == "image/webp" and cabecera[8:12] != b"WEBP":
            raise ValidationError("El contenido no corresponde a una imagen WebP.")
        if extension in {"docx", "xlsx"}:
            import zipfile
            try:
                with zipfile.ZipFile(archivo) as paquete:
                    expected = "word/document.xml" if extension == "docx" else "xl/workbook.xml"
                    if expected not in paquete.namelist() or "[Content_Types].xml" not in paquete.namelist():
                        raise ValidationError("Documento Office inválido.")
                    if any(info.file_size > 100 * 1024 * 1024 for info in paquete.infolist()):
                        raise ValidationError("Documento Office excede el tamaño admitido.")
            except (zipfile.BadZipFile, OSError):
                raise ValidationError("Documento Office inválido.") from None
            finally:
                archivo.seek(0)
        digest = hashlib.sha256()
        for bloque in archivo.chunks():
            digest.update(bloque)
        archivo.seek(0)
        archivo.hash_sha256 = digest.hexdigest()
        return archivo

    def clean_equipo(self):
        equipo = self.cleaned_data.get("equipo")
        if equipo and (not self.proyecto or equipo.proyecto_id != self.proyecto.id):
            raise ValidationError("El equipo no pertenece al proyecto seleccionado.")
        return equipo

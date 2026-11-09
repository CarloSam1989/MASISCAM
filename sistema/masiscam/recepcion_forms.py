import uuid

from django import forms
from django.utils import timezone

from .forms import ArchivosRegistroField, DocumentoForm
from .models import Cliente, Equipo, RegistroEquipo


class RecepcionForm(forms.Form):
    clave = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), label="Cliente (nombre / RUC)")
    equipo = forms.ModelChoiceField(queryset=Equipo.objects.none())
    tipo = forms.ChoiceField(choices=[(v, t) for v, t in RegistroEquipo.Tipo.choices if v in {"MANTENIMIENTO", "REPARACION"}])
    fecha = forms.DateTimeField(initial=timezone.now, widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    condicion_ingreso = forms.CharField(max_length=10000, widget=forms.Textarea, label="Condición de ingreso")
    observaciones = forms.CharField(max_length=10000, required=False, widget=forms.Textarea)
    guia_remision = forms.CharField(max_length=150, required=False, label="Número de guía de remisión")
    guia_archivo = forms.FileField(required=False, label="Archivo de la guía (PDF)")
    fotografias = ArchivosRegistroField()

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa)
        self.fields["equipo"].queryset = Equipo.objects.filter(proyecto__empresa=empresa).exclude(estado="INACTIVO").exclude(proyecto__estado="ARCHIVADO")
        self.fields["fotografias"].label = "Fotografías de recepción"
        self.fields["fotografias"].widget.attrs["accept"] = ".jpg,.jpeg,.png,.webp"
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-control")

    def clean_guia_archivo(self):
        archivo = self.cleaned_data.get("guia_archivo")
        if archivo:
            if archivo.content_type != "application/pdf":
                raise forms.ValidationError("La guía debe ser PDF.")
            validator = DocumentoForm(files={"archivo": archivo})
            validator.cleaned_data = {"archivo": archivo}
            return validator.clean_archivo()
        return archivo

    def clean_fotografias(self):
        from .recepcion_services import validar_fotografias
        return validar_fotografias(self.cleaned_data["fotografias"])

    def clean(self):
        data = super().clean()
        equipo, cliente = data.get("equipo"), data.get("cliente")
        if equipo and cliente and equipo.cliente_id != cliente.pk:
            self.add_error("equipo", "El equipo no pertenece al cliente seleccionado.")
        if data.get("guia_archivo") and not data.get("guia_remision"):
            self.add_error("guia_remision", "Indique el número de la guía adjunta.")
        return data


class ConfirmacionRecepcionForm(forms.Form):
    clave = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    general_id = forms.ModelChoiceField(queryset=RegistroEquipo.objects.none(), required=False, label="Fotografía GENERAL existente")
    foto_general = forms.FileField(required=False, label="O cargar fotografía GENERAL (obligatoria)")
    placa = forms.ChoiceField(choices=[("", "Seleccione"), ("CON_PLACA", "El equipo tiene placa"), ("SIN_PLACA", "SIN_PLACA: excepción justificada")])
    placa_id = forms.ModelChoiceField(queryset=RegistroEquipo.objects.none(), required=False, label="Fotografía PLACA existente")
    foto_placa = forms.FileField(required=False, label="O cargar fotografía PLACA (obligatoria si existe)")
    motivo_sin_placa = forms.CharField(required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 3}), label="Justificación SIN_PLACA")
    motivo_sin_guia = forms.CharField(required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 3}), label="Motivo de ausencia de guía de remisión")

    def __init__(self, *args, recepcion, **kwargs):
        super().__init__(*args, **kwargs)
        self.recepcion = recepcion
        fotos = recepcion.evidencias.filter(tipo="FOTO").select_related("documento")
        for name in ("general_id", "placa_id"):
            self.fields[name].queryset = fotos
            self.fields[name].label_from_instance = lambda e: e.documento.nombre_original
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")
            if name in {"foto_general", "foto_placa"}:
                field.widget.attrs["accept"] = ".jpg,.jpeg,.png,.webp"

    def clean(self):
        data = super().clean()
        if not data.get("general_id") and not data.get("foto_general"):
            self.add_error("general_id", "Se requiere al menos una fotografía GENERAL.")
        if data.get("placa") == "CON_PLACA" and not data.get("placa_id") and not data.get("foto_placa"):
            self.add_error("placa_id", "El equipo tiene placa: adjunte su fotografía.")
        if data.get("placa") == "SIN_PLACA" and not (data.get("motivo_sin_placa") or "").strip():
            self.add_error("motivo_sin_placa", "Justifique la ausencia de placa.")
        if not self.recepcion.guia_remision and not (data.get("motivo_sin_guia") or "").strip():
            self.add_error("motivo_sin_guia", "La guía es opcional; su ausencia requiere un motivo.")
        return data

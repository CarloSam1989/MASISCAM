from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from .models import Cliente, RolMasiscam


class ClienteUsuarioField(forms.ModelChoiceField):
    def label_from_instance(self, cliente):
        return f"{cliente.nombre_comercial} — {cliente.razon_social} — {cliente.ruc}"


class UsuarioCamposMixin:
    def __init__(self, *args, empresa, asignacion=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.asignacion = asignacion
        roles = [(codigo, nombre) for codigo, nombre in RolMasiscam.Rol.choices
                 if codigo != RolMasiscam.Rol.CONSULTA]
        inicial = asignacion.rol if asignacion else RolMasiscam.Rol.ADMINISTRADOR
        retirado = inicial not in dict(roles)
        self.fields["rol"] = forms.ChoiceField(
            label="Rol", choices=[("", "Seleccione un rol")] + roles,
            initial="" if retirado else inicial,
            help_text=("Esta cuenta conserva un rol retirado. No se cambia automáticamente; seleccione expresamente un rol vigente para guardar."
                       if retirado else ""),
        )
        self.fields["cliente"] = ClienteUsuarioField(
            label="Cliente asociado", required=False,
            queryset=Cliente.objects.filter(empresa=empresa),
            initial=asignacion.cliente_id if asignacion else None,
            help_text="Busque por nombre, razón social o RUC/cédula. Obligatorio para CLIENTE; admite varios usuarios por cliente.",
            widget=forms.Select(attrs={"data-usuario-cliente": "true"}),
        )
        if self.is_bound and self.data.get(self.add_prefix("rol")) in {
            RolMasiscam.Rol.ADMINISTRADOR, RolMasiscam.Rol.TECNICO,
        }:
            self.data = self.data.copy()
            self.data[self.add_prefix("cliente")] = ""
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
        self.fields["first_name"].required = True
        self.fields["first_name"].label = "Nombre"
        self.fields["username"].label = "Usuario"
        self.fields["email"].label = "Correo (opcional)"

    def clean(self):
        data = super().clean()
        if data.get("rol") == RolMasiscam.Rol.CLIENTE:
            if not data.get("cliente"):
                self.add_error("cliente", "Seleccione un cliente de esta empresa.")
        else:
            data["cliente"] = None
        if (self.instance.is_superuser and data.get("rol")
                and data["rol"] != RolMasiscam.Rol.ADMINISTRADOR):
            self.add_error("rol", "Esta cuenta tiene privilegios de superusuario. Retírelos desde la administración técnica antes de asignar un rol restringido.")
        return data

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya existe un usuario con ese nombre.")
        return username


class AdministradorCrearForm(UsuarioCamposMixin, UserCreationForm):

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "first_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "Contraseña inicial"
        self.fields["password2"].label = "Confirmar contraseña"


class UsuarioEditarForm(UsuarioCamposMixin, forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "email")


class UsuarioClaveForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Nueva contraseña"
        self.fields["new_password2"].label = "Confirmar contraseña"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

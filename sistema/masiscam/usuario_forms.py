from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from .models import RolMasiscam


class UsuarioCamposMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["first_name"].required = True
        self.fields["first_name"].label = "Nombre"
        self.fields["username"].label = "Usuario"
        self.fields["email"].label = "Correo (opcional)"

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya existe un usuario con ese nombre.")
        return username


class AdministradorCrearForm(UsuarioCamposMixin, UserCreationForm):
    rol = forms.ChoiceField(
        label="Rol", initial=RolMasiscam.Rol.ADMINISTRADOR,
        choices=[(codigo, nombre) for codigo, nombre in RolMasiscam.Rol.choices
                 if codigo != RolMasiscam.Rol.CLIENTE],
        help_text="Los usuarios CLIENTE se crean desde su ficha de cliente.",
    )

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

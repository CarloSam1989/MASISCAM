from django.contrib import admin
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from accounts.models import Empresa, Perfil
from .models import Auditoria, Cliente, Documento, Equipo, Proyecto, RolMasiscam
admin.site.register((Cliente, Proyecto, Documento, Auditoria))


class RolMasiscamAdminForm(forms.ModelForm):
    class Meta:
        model = RolMasiscam
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(code, label) for code, label in RolMasiscam.Rol.choices
                   if code in RolMasiscam.ROLES_OPERATIVOS]
        if self.instance.pk and self.instance.rol not in RolMasiscam.ROLES_OPERATIVOS:
            choices.insert(0, (self.instance.rol, self.instance.get_rol_display() + ' (histórico, sin acceso operativo)'))
        self.fields['rol'].choices = [('', 'Seleccione un rol')] + choices

    def clean(self):
        data = super().clean()
        if data.get('rol') in RolMasiscam.ROLES_OPERATIVOS:
            data['cliente'] = None
        return data


@admin.register(RolMasiscam)
class RolMasiscamAdmin(admin.ModelAdmin):
    form = RolMasiscamAdminForm

    def save_model(self, request, obj, form, change):
        anterior = RolMasiscam.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        from .services import auditar
        auditar(empresa=obj.perfil.empresa, usuario=request.user,
                accion='USUARIO_EDITADO' if change else 'USUARIO_CREADO', objeto=obj,
                detalle={'rol_anterior': anterior.rol if anterior else None, 'rol_nuevo': obj.rol,
                         'cliente_anterior': anterior.cliente_id if anterior else None})


@admin.register(Equipo)
class EquipoAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        from .access import rol_masiscam_usuario
        rol = rol_masiscam_usuario(usuario=request.user, empresa=getattr(request, 'empresa_activa', None))
        return (request.user.is_superuser and rol and rol.rol == 'ADMIN'
                and super().has_add_permission(request))


class MasiscamUserCreationForm(AdminUserCreationForm):
    tipo = forms.ChoiceField(label="Tipo de usuario", choices=[
        ("ADMIN", "ADMINISTRADOR"), ("TALLER", "TALLER"), ("OFICINA", "OFICINA"),
    ])
    empresa = forms.ModelChoiceField(
        label="Empresa", queryset=Empresa.objects.filter(activa=True), required=True,
    )

class MasiscamUserAdmin(UserAdmin):
    add_form = MasiscamUserCreationForm
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Acceso MASISCAM", {"fields": ("tipo", "empresa")}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            empresa = form.cleaned_data["empresa"]
            perfil = Perfil.objects.create(user=obj, empresa=empresa)
            RolMasiscam.objects.create(
                perfil=perfil, rol=form.cleaned_data["tipo"],
            )


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), MasiscamUserAdmin)

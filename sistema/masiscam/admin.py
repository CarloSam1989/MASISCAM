from django.contrib import admin
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from accounts.models import Empresa, Perfil
from .models import Auditoria, Cliente, Documento, Equipo, Proyecto, RolMasiscam
admin.site.register((RolMasiscam, Cliente, Proyecto, Documento, Auditoria))


@admin.register(Equipo)
class EquipoAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        from .access import rol_masiscam_usuario
        rol = rol_masiscam_usuario(usuario=request.user, empresa=getattr(request, 'empresa_activa', None))
        # Preserve existing technical recovery for superusers without an
        # operational role, and ADMIN; restricted operational roles cannot add.
        return (request.user.is_superuser and (rol is None or rol.rol == 'ADMIN')
                and super().has_add_permission(request))


class MasiscamUserCreationForm(AdminUserCreationForm):
    tipo = forms.ChoiceField(label="Tipo de usuario", choices=[
        ("ADMIN", "Administrativo"), ("CLIENTE", "Cliente"),
    ])
    empresa = forms.ModelChoiceField(
        label="Empresa", queryset=Empresa.objects.filter(activa=True), required=False,
        help_text="Obligatoria para Administrativo. Para Cliente se utiliza su empresa.",
    )
    cliente = forms.ModelChoiceField(
        label="Cliente existente", required=False,
        queryset=Cliente.objects.filter(empresa__activa=True),
        help_text="Obligatorio para Cliente. Un cliente puede tener varios usuarios.",
    )

    def clean(self):
        data = super().clean()
        cliente = data.get("cliente")
        empresa = data.get("empresa")
        if data.get("tipo") == "CLIENTE":
            if not cliente:
                self.add_error("cliente", "Seleccione un cliente existente.")
            elif empresa and empresa != cliente.empresa:
                self.add_error("empresa", "La empresa debe corresponder al cliente.")
        elif data.get("tipo") == "ADMIN":
            if not empresa:
                self.add_error("empresa", "Seleccione la empresa del administrativo.")
            if cliente:
                self.add_error("cliente", "Un administrativo no se vincula a un cliente.")
        return data


class MasiscamUserAdmin(UserAdmin):
    add_form = MasiscamUserCreationForm
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Acceso MASISCAM", {"fields": ("tipo", "empresa", "cliente")}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            cliente = form.cleaned_data.get("cliente")
            empresa = cliente.empresa if cliente else form.cleaned_data["empresa"]
            perfil = Perfil.objects.create(user=obj, empresa=empresa)
            RolMasiscam.objects.create(
                perfil=perfil, rol=form.cleaned_data["tipo"], cliente=cliente,
            )


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), MasiscamUserAdmin)

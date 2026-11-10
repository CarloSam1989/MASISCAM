from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import Perfil
from .access import PERMISOS_ROL, permiso_masiscam_required
from .models import RolMasiscam
from .services import auditar
from .usuario_forms import AdministradorCrearForm, UsuarioClaveForm, UsuarioEditarForm


ALCANCE_OPERATIVO = {
    "TALLER": "Alta exclusiva de equipos; recepción, planos aplicables, inspección, diagnóstico, repuestos, reparación comercialmente autorizada y calibración de sus equipos con asignación vigente. Sin editar clientes ni planos maestros, autorizar comercialmente, aprobar trabajos o cerrar expedientes.",
    "ADMIN": "Lectura técnica de empresa; administración heredada. Sin decisiones técnicas o comerciales.",
    "CONSULTA": "Lectura histórica de empresa. Sin modificaciones ni aprobaciones.",
    "CLIENTE": "Solo productos propios y documentos autorizados. Sin biblioteca ni operaciones internas.",
    "OFICINA": "Crear y editar clientes de la empresa; recepción, respaldos comerciales, recepción de repuestos y documentos operativos; solicitud de informes. Sin aprobación técnica o comercial.",
    "TECNICO": "Consultar clientes de expedientes asignados, sin modificarlos; expedientes asignados y biblioteca aplicable; edición de sus borradores técnicos, reparación autorizada y calibración. Sin aprobar trabajos ni consultar respaldos comerciales.",
    "PLANTA": "Asignación técnica, biblioteca, revisión técnica independiente, protocolos de calibración y recepción de repuestos. Sin decisión comercial ni cierre administrativo.",
    "GERENCIA": "Propuestas y decisiones comerciales con respaldo y política válida; cierre administrativo con revisión técnica previa. Sin sustituir PLANTA.",
}


def _roles(request):
    return RolMasiscam.objects.filter(perfil__empresa=request.empresa_activa).select_related("perfil__user")


def _gestionable(request, rol):
    usuario = rol.perfil.user
    # A MASISCAM administrator cannot take over Django admin or another company.
    return not usuario.perfiles.exclude(empresa=request.empresa_activa).exists() and (
        request.user.is_superuser or not (usuario.is_superuser or usuario.is_staff)
    )


def _rol(request, pk):
    rol = get_object_or_404(_roles(request), pk=pk)
    if not _gestionable(request, rol):
        raise PermissionDenied("No puede gestionar esta cuenta desde esta empresa.")
    return rol


@require_GET
@permiso_masiscam_required("usuarios")
def usuarios(request):
    roles = list(_roles(request).order_by("perfil__user__username"))
    for rol in roles:
        rol.gestionable = _gestionable(request, rol)
        rol.acceso_activo = rol.activo and rol.perfil.activo and rol.perfil.user.is_active
        rol.permisos_efectivos = sorted(PERMISOS_ROL.get(rol.rol, set())) if rol.acceso_activo else []
        rol.privilegio_tecnico = rol.perfil.user.is_superuser
        rol.alcance_operativo = ALCANCE_OPERATIVO.get(rol.rol, "Sin operaciones autorizadas.") if rol.acceso_activo else "Sin acceso."
    pendientes = len({rol.perfil.user_id for rol in roles if rol.rol == RolMasiscam.Rol.CONSULTA})
    return render(request, "masiscam/usuarios.html", {
        "roles": roles, "consulta_pendientes": pendientes, "empresa_usuarios": request.empresa_activa,
    })


@sensitive_post_parameters("password1", "password2")
@require_http_methods(["GET", "POST"])
@permiso_masiscam_required("usuarios")
def usuario_crear(request):
    form = AdministradorCrearForm(request.POST if request.method == "POST" else None, empresa=request.empresa_activa)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                usuario = form.save()  # UserCreationForm hashes via set_password().
                perfil = Perfil.objects.create(user=usuario, empresa=request.empresa_activa)
                rol = RolMasiscam.objects.create(perfil=perfil, rol=form.cleaned_data["rol"], cliente=form.cleaned_data["cliente"])
                auditar(empresa=request.empresa_activa, usuario=request.user,
                        accion="ADMINISTRADOR_CREADO" if rol.rol == RolMasiscam.Rol.ADMINISTRADOR else "USUARIO_CREADO", objeto=rol)
        except IntegrityError:
            form.add_error(None, "No se pudo guardar el usuario. Compruebe que el nombre de usuario sea único.")
        else:
            messages.success(request, "Usuario creado correctamente.")
            return redirect("masiscam:usuarios")
    return render(request, "masiscam/usuario_form.html", {"form": form, "titulo": "Agregar usuario"})


@require_http_methods(["GET", "POST"])
@permiso_masiscam_required("usuarios")
def usuario_editar(request, pk):
    rol = _rol(request, pk)
    form = UsuarioEditarForm(request.POST if request.method == "POST" else None,
                             instance=rol.perfil.user, empresa=request.empresa_activa, asignacion=rol)
    form.actor_id = request.user.pk
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
                rol.rol = form.cleaned_data["rol"]
                rol.cliente = form.cleaned_data["cliente"]
                rol.save(update_fields=["rol", "cliente"])
                auditar(empresa=request.empresa_activa, usuario=request.user,
                        accion="USUARIO_EDITADO", objeto=rol)
        except IntegrityError:
            form.add_error(None, "No se pudo guardar el usuario. Compruebe que el nombre de usuario sea único.")
        else:
            messages.success(request, "Usuario actualizado correctamente.")
            if rol.perfil.user_id == request.user.pk and rol.rol != RolMasiscam.Rol.ADMINISTRADOR:
                return redirect("masiscam:dashboard")
            return redirect("masiscam:usuarios")
    return render(request, "masiscam/usuario_form.html", {"form": form, "titulo": "Editar usuario"})


@sensitive_post_parameters("new_password1", "new_password2")
@require_http_methods(["GET", "POST"])
@permiso_masiscam_required("usuarios")
def usuario_clave(request, pk):
    rol = _rol(request, pk)
    form = UsuarioClaveForm(rol.perfil.user, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            usuario = form.save()  # SetPasswordForm never reads or displays the old password.
            auditar(empresa=request.empresa_activa, usuario=request.user,
                    accion="USUARIO_CLAVE_RESTABLECIDA", objeto=rol)
        if usuario.pk == request.user.pk:
            update_session_auth_hash(request, usuario)
        messages.success(request, "Contraseña actualizada correctamente.")
        return redirect("masiscam:usuarios")
    return render(request, "masiscam/usuario_form.html", {
        "form": form, "titulo": "Cambiar clave", "usuario_destino": rol.perfil.user,
    })


@require_POST
@permiso_masiscam_required("usuarios")
def usuario_estado(request, pk):
    rol = _rol(request, pk)
    activo = request.POST.get("activo")
    if activo not in {"0", "1"}:
        messages.error(request, "Estado no válido.")
    elif rol.perfil.user_id == request.user.pk and activo == "0":
        messages.error(request, "No puede desactivar su propia cuenta.")
    else:
        with transaction.atomic():
            rol.activo = activo == "1"
            rol.save(update_fields=["activo"])
            rol.perfil.activo = rol.activo
            rol.perfil.save(update_fields=["activo"])
            rol.perfil.user.is_active = rol.activo
            rol.perfil.user.save(update_fields=["is_active"])
            auditar(empresa=request.empresa_activa, usuario=request.user,
                    accion="USUARIO_ACTIVADO" if rol.activo else "USUARIO_DESACTIVADO", objeto=rol)
        messages.success(request, "Estado del usuario actualizado.")
    return redirect("masiscam:usuarios")

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.models import Perfil
from .models import RolMasiscam

PERMISOS_ROL = {
    RolMasiscam.Rol.ADMINISTRADOR: {"ver", "crear", "editar", "archivar", "equipos", "documentos", "reemplazar", "qr", "visibilidad", "historial"},
    RolMasiscam.Rol.TECNICO: {"ver", "crear", "editar", "equipos", "documentos", "reemplazar", "qr", "historial"},
    RolMasiscam.Rol.CONSULTA: {"ver"},
}


def perfil_masiscam_usuario(*, usuario, empresa):
    if not usuario.is_authenticated or empresa is None:
        return None
    return Perfil.objects.filter(user=usuario, empresa=empresa, activo=True).select_related("empresa").first()


def rol_masiscam_usuario(*, usuario, empresa):
    perfil = perfil_masiscam_usuario(usuario=usuario, empresa=empresa)
    if perfil is None or not perfil.empresa.activa:
        return None
    return RolMasiscam.objects.filter(perfil=perfil, activo=True).first()


def tiene_permiso(request, permiso):
    if request.user.is_superuser:
        return True
    asignacion = getattr(request, "rol_masiscam", None)
    return bool(asignacion and permiso in PERMISOS_ROL.get(asignacion.rol, set()))


def masiscam_access_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        empresa = getattr(request, "empresa_activa", None)
        if empresa is None:
            raise PermissionDenied("Debe seleccionar una empresa activa.")
        perfil = perfil_masiscam_usuario(usuario=request.user, empresa=empresa)
        if not request.user.is_superuser and (perfil is None or not perfil.empresa.activa):
            raise PermissionDenied("El perfil no tiene acceso a MASISCAM.")
        request.perfil_masiscam = perfil
        request.rol_masiscam = rol_masiscam_usuario(usuario=request.user, empresa=empresa)
        if not request.user.is_superuser and request.rol_masiscam is None:
            raise PermissionDenied("El perfil no tiene un rol MASISCAM activo.")
        return view_func(request, *args, **kwargs)
    return wrapped


def permiso_masiscam_required(permiso):
    def decorator(view_func):
        @masiscam_access_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not tiene_permiso(request, permiso):
                raise PermissionDenied("El rol MASISCAM no permite esta operación.")
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator

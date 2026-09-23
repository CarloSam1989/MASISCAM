from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from accounts.models import Perfil
from .models import RolMasiscam

PERMISOS_ROL = {
    RolMasiscam.Rol.ADMINISTRADOR: {"ver", "crear", "editar", "archivar", "equipos", "documentos", "reemplazar", "qr", "visibilidad", "historial", "usuarios"},
    RolMasiscam.Rol.TECNICO: {"ver", "crear", "editar", "equipos", "documentos", "reemplazar", "qr", "historial"},
    RolMasiscam.Rol.CONSULTA: {"ver"},
    RolMasiscam.Rol.CLIENTE: {"ver"},
}

# Client roles are scoped globally to prevent changing the active company or
# using another membership to escape the customer boundary.
def acceso_cliente_usuario(usuario):
    if not usuario.is_authenticated:
        return None
    roles = list(RolMasiscam.objects.filter(perfil__user=usuario).filter(
        Q(rol=RolMasiscam.Rol.CLIENTE) | Q(cliente__isnull=False)
    ).select_related("cliente", "perfil__empresa")[:2])
    if not roles:
        return None
    if len(roles) != 1:
        raise PermissionDenied("La cuenta debe estar vinculada a un único cliente.")
    rol = roles[0]
    if (not rol.activo or not rol.perfil.activo or not rol.perfil.empresa.activa
            or not rol.cliente_id or rol.cliente.empresa_id != rol.perfil.empresa_id):
        raise PermissionDenied("El acceso del cliente no está activo.")
    return rol


VISTAS_CLIENTE = {
    "cliente_productos", "ficha_detalle", "equipo_informe", "equipo_publico",
    "equipo_foto_privada", "equipo_foto_publica", "documento_privado", "equipo_documento_privado",
    "documento_publico", "imagen_proyecto_publica",
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
    if getattr(request, "cliente_usuario", None):
        return permiso == "ver"
    if request.user.is_superuser:
        return True
    asignacion = getattr(request, "rol_masiscam", None)
    return bool(asignacion and permiso in PERMISOS_ROL.get(asignacion.rol, set()))


def masiscam_access_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        acceso_cliente = acceso_cliente_usuario(request.user)
        request.cliente_usuario = acceso_cliente.cliente if acceso_cliente else None
        if acceso_cliente and (view_func.__name__ not in VISTAS_CLIENTE or request.method not in {"GET", "HEAD"}):
            raise PermissionDenied("El cliente solo puede consultar sus productos.")
        empresa = getattr(request, "empresa_activa", None)
        if acceso_cliente and empresa != acceso_cliente.perfil.empresa:
            raise PermissionDenied("La empresa no corresponde al cliente.")
        if empresa is None:
            raise PermissionDenied("Debe seleccionar una empresa activa.")
        perfil = perfil_masiscam_usuario(usuario=request.user, empresa=empresa)
        if not request.user.is_superuser and (perfil is None or not perfil.empresa.activa):
            raise PermissionDenied("El perfil no tiene acceso a MASISCAM.")
        request.perfil_masiscam = perfil
        request.rol_masiscam = rol_masiscam_usuario(usuario=request.user, empresa=empresa)
        if not request.user.is_superuser and request.rol_masiscam is None:
            raise PermissionDenied("El perfil no tiene un rol MASISCAM activo.")
        response = view_func(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response
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

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.shortcuts import render

from accounts.models import Perfil
from .models import RolMasiscam


class EquipoNoAsignado(PermissionDenied):
    """Denegación de equipo con un mensaje visible, sin exponer sus datos."""


PERMISOS_ROL = {
    RolMasiscam.Rol.TALLER: {"ver", "equipos_crear"},
    RolMasiscam.Rol.OFICINA: {"ver", "clientes_crear", "clientes_editar"},
    RolMasiscam.Rol.PLANTA: {"ver"},
    RolMasiscam.Rol.GERENCIA: {"ver"},
    RolMasiscam.Rol.ADMINISTRADOR: {"ver", "crear", "editar", "archivar", "equipos", "documentos", "reemplazar", "qr", "visibilidad", "historial", "usuarios", "clientes_crear", "clientes_editar"},
    RolMasiscam.Rol.TECNICO: {"ver", "crear", "editar", "equipos", "documentos", "reemplazar", "qr", "historial"},
    RolMasiscam.Rol.CONSULTA: {"ver"},
    RolMasiscam.Rol.CLIENTE: {"ver"},
}

VISTAS_TALLER = {
    "masiscam.flujo_views": {"detalle", "documento"},
    "masiscam.tecnica_views": {"tecnica_inicio", "tecnica_expediente", "tecnica_nueva_revision", "tecnica_revision", "tecnica_informe", "tecnica_documento", "biblioteca", "biblioteca_referencia", "biblioteca_documento"},
    "masiscam.plano_views": {"plano", "plano_datos", "plano_pieza"},
    "masiscam.operacion_views": {"inicio", "detalle", "operar", "documento"},
    "masiscam.calibracion_views": {"inicio", "expediente", "revision", "documento", "fuente"},
    "masiscam.cierre_views": {"detalle", "documento", "historial"},
    "masiscam.views": {"dashboard", "ficha_crear", "equipo_crear", "clientes", "equipo_foto_privada"},
    "masiscam.taller_views": {"taller_inicio", "taller_estado", "taller_cuenta", "taller_equipo"},
    "masiscam.recepcion_views": {"expedientes", "recepcion_crear", "expediente_detalle",
        "recepcion_finalizar", "recepcion_documento"},
}

VISTAS_OFICINA = {
    'masiscam.oficina_views': {'inicio'},
    'masiscam.flujo_views': {'bandeja','detalle','documento'},
    'masiscam.views': {'dashboard','clientes','cliente_detalle','cliente_crear','cliente_editar',
        'producto_listado','ficha_detalle','equipo_informe','equipo_foto_privada','equipo_documento_privado'},
    'masiscam.recepcion_views': {'expedientes','recepcion_crear','expediente_detalle','recepcion_finalizar',
        'guia_emitir','guia_reintentar','recepcion_documento'},
    'masiscam.tecnica_views': {'tecnica_informe','tecnica_documento','biblioteca_documento'},
    'masiscam.operacion_views': {'inicio','detalle','operar','documento'},
    'masiscam.cierre_views': {'detalle','documento'},
}


def autorizar_creacion_equipo(*, usuario, empresa):
    rol = rol_masiscam_usuario(usuario=usuario, empresa=empresa) if usuario else None
    if not rol or rol.rol != "TALLER" or acceso_cliente_usuario(usuario):
        raise PermissionDenied("Solo TALLER puede registrar equipos mediante el flujo operativo.")
    return rol


def equipos_taller(*, usuario, empresa):
    from .models import Equipo
    rol = rol_masiscam_usuario(usuario=usuario, empresa=empresa)
    qs = Equipo.objects.filter(proyecto__empresa=empresa, registrado_por=usuario,
                               cliente__empresa=empresa).exclude(estado='INACTIVO')
    return qs if rol and rol.rol == "TALLER" and not acceso_cliente_usuario(usuario) else qs.none()


AUDITORES_INACTIVOS = {'ADMIN', 'PLANTA', 'GERENCIA', 'CONSULTA'}


def puede_auditar_inactivos(request):
    rol=getattr(getattr(request,'rol_masiscam',None),'rol',None)
    return bool(request.user.is_authenticated and request.user.is_active and
        (rol in AUDITORES_INACTIVOS or (rol is None and request.user.is_superuser)))


def validar_equipo_operativo(equipo, bloquear=False):
    if bloquear:
        from .models import Equipo
        equipo=Equipo.objects.select_for_update().get(pk=equipo.pk)
    if equipo.estado == 'INACTIVO':
        raise PermissionDenied('Equipo inactivo: solo admite consulta histórica autorizada y reactivación administrativa.')


def validar_consulta_equipo(equipo, rol):
    if rol not in AUDITORES_INACTIVOS:
        validar_equipo_operativo(equipo)


def clientes_autorizados(*, usuario, empresa):
    """Customer boundary shared by HTML, AJAX and customer selectors."""
    from .models import Cliente, ExpedienteMantenimiento
    qs = Cliente.objects.filter(empresa=empresa)
    if not usuario.is_authenticated or not usuario.is_active or not empresa or not empresa.activa:
        return qs.none()
    rol = rol_masiscam_usuario(usuario=usuario, empresa=empresa)
    if rol and rol.rol == RolMasiscam.Rol.TECNICO:
        expedientes = ExpedienteMantenimiento.objects.filter(
            empresa=empresa, asignaciones_tecnicas__tecnico=usuario,
            asignaciones_tecnicas__fin__isnull=True,
            registro__equipo__proyecto__empresa=empresa,
            registro__equipo__cliente_id=F('cliente_contractual_id'),
        )
        return qs.filter(pk__in=expedientes.values('cliente_contractual_id'))
    if rol and rol.rol == RolMasiscam.Rol.CLIENTE:
        return qs.filter(pk=rol.cliente_id)
    return qs if rol or usuario.is_superuser else qs.none()

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
    "dashboard", "producto_listado", "cliente_productos", "ficha_detalle", "equipo_informe", "equipo_publico",
    "equipo_foto_privada", "equipo_foto_publica", "documento_privado", "equipo_documento_privado",
    "documento_publico", "imagen_proyecto_publica",
}


def perfil_masiscam_usuario(*, usuario, empresa):
    if not usuario.is_authenticated or not usuario.is_active or empresa is None:
        return None
    return Perfil.objects.filter(user=usuario, empresa=empresa, activo=True).select_related("empresa").first()


def rol_masiscam_usuario(*, usuario, empresa):
    perfil = perfil_masiscam_usuario(usuario=usuario, empresa=empresa)
    if perfil is None or not perfil.empresa.activa:
        return None
    return RolMasiscam.objects.filter(perfil=perfil, activo=True).first()


def tiene_permiso(request, permiso):
    if not request.user.is_authenticated or not request.user.is_active:
        return False
    if getattr(request, "cliente_usuario", None):
        return permiso == "ver"
    if permiso == "equipos_crear":
        rol = getattr(request, "rol_masiscam", None)
        return bool(rol and rol.rol == "TALLER" and rol.activo)
    asignacion = getattr(request, "rol_masiscam", None)
    if asignacion and asignacion.rol in {"TALLER","OFICINA"}:
        return permiso in PERMISOS_ROL[asignacion.rol]
    if request.user.is_superuser:
        return True
    asignacion = getattr(request, "rol_masiscam", None)
    return bool(asignacion and permiso in PERMISOS_ROL.get(asignacion.rol, set()))


def masiscam_access_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_active:
            raise PermissionDenied("La cuenta no está activa.")
        acceso_cliente = acceso_cliente_usuario(request.user)
        request.cliente_usuario = acceso_cliente.cliente if acceso_cliente else None
        if acceso_cliente and (view_func.__name__ not in VISTAS_CLIENTE or request.method not in {"GET", "HEAD"}):
            raise PermissionDenied("El cliente solo puede consultar sus productos.")
        empresa = getattr(request, "empresa_activa", None)
        if acceso_cliente and empresa != acceso_cliente.perfil.empresa:
            raise PermissionDenied("La empresa no corresponde al cliente.")
        if empresa is None:
            raise PermissionDenied("Debe seleccionar una empresa activa.")
        if not empresa.activa:
            raise PermissionDenied("La empresa no está activa.")
        perfil = perfil_masiscam_usuario(usuario=request.user, empresa=empresa)
        if not request.user.is_superuser and (perfil is None or not perfil.empresa.activa):
            raise PermissionDenied("El perfil no tiene acceso a MASISCAM.")
        request.perfil_masiscam = perfil
        request.rol_masiscam = rol_masiscam_usuario(usuario=request.user, empresa=empresa)
        if not request.user.is_superuser and request.rol_masiscam is None:
            raise PermissionDenied("El perfil no tiene un rol MASISCAM activo.")
        if request.rol_masiscam and request.rol_masiscam.rol=='OFICINA':
            if view_func.__name__ not in VISTAS_OFICINA.get(view_func.__module__,set()):
                raise PermissionDenied('Esta ventana no corresponde al trabajo autorizado de OFICINA.')
        if request.rol_masiscam and request.rol_masiscam.rol == "TALLER":
            if view_func.__name__ not in VISTAS_TALLER.get(view_func.__module__, set()):
                raise PermissionDenied("Esta ventana no está autorizada para TALLER.")
            if (view_func.__module__ in {"masiscam.cierre_views", "masiscam.tecnica_views"}
                    and view_func.__name__ in {"detalle", "biblioteca", "biblioteca_referencia", "biblioteca_documento"}
                    and request.method not in {"GET", "HEAD"}):
                raise PermissionDenied("Esta ventana es de consulta para TALLER.")
            if view_func.__name__ == "clientes" and (
                request.method not in {"GET", "HEAD"} or request.GET.get("formato") != "json"
            ):
                raise PermissionDenied("Utilice la búsqueda de clientes del registro de ingreso.")
        try:
            response = view_func(request, *args, **kwargs)
        except EquipoNoAsignado as error:
            response = render(request, "masiscam/acceso_denegado.html", {"mensaje": str(error)}, status=403)
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

from django import template
from django.urls import reverse

from masiscam.models import Equipo
from masiscam.access import tiene_permiso

register = template.Library()


@register.simple_tag(takes_context=True)
def puede_ver_cierre(context):
    from django.conf import settings
    return bool(getattr(settings,'MASISCAM_CIERRE_LOCAL',False) and puede_ver_tecnica(context))


@register.simple_tag(takes_context=True)
def puede_ver_calibracion(context):
    from django.conf import settings
    return bool(getattr(settings,'MASISCAM_CALIBRACION_LOCAL',False) and puede_ver_tecnica(context))


@register.simple_tag(takes_context=True)
def puede_ver_operacion(context):
    from django.conf import settings
    return bool(getattr(settings,'MASISCAM_OPERACION_LOCAL',False) and puede_ver_tecnica(context))


@register.simple_tag(takes_context=True)
def puede_ver_tecnica(context):
    from django.conf import settings
    request=context.get('request')
    role=getattr(request,'rol_masiscam',None)
    return bool(getattr(settings,'MASISCAM_TECNICA_LOCAL',False) and request and request.user.is_authenticated
        and not getattr(request,'cliente_usuario',None) and role and role.rol in {'TECNICO','OFICINA','PLANTA','GERENCIA','ADMIN','CONSULTA'})


@register.simple_tag(takes_context=True)
def puede_ver_recepcion(context):
    request = context.get("request")
    rol = getattr(request, "rol_masiscam", None)
    return bool(request and request.user.is_authenticated and not getattr(request, "cliente_usuario", None)
                and rol and rol.rol in {"OFICINA", "PLANTA", "GERENCIA", "ADMIN", "CONSULTA"})


@register.simple_tag(takes_context=True)
def puede_gestionar_usuarios(context):
    request = context.get("request")
    return bool(request and request.user.is_authenticated and tiene_permiso(request, "usuarios"))


@register.simple_tag(takes_context=True)
def breadcrumbs(context):
    request = context.get("request")
    if request is None:
        return []
    if getattr(request, 'rol_masiscam', None) and request.rol_masiscam.rol == 'TALLER':
        return [{'label': 'Inicio de taller', 'url': reverse('masiscam:taller_inicio')}]
    vista = request.resolver_match.url_name if request.resolver_match else ""
    items = [("Dashboard", reverse("masiscam:dashboard"))]
    equipo = context.get("equipo")
    cliente = context.get("cliente") or context.get("cliente_seleccionado")
    producto = context.get("producto_codigo")
    proyecto = context.get("proyecto")
    if equipo:
        cliente = equipo.cliente
        producto = equipo.tipo_producto
    if getattr(request, "cliente_usuario", None):
        items = [("Mis productos", reverse("masiscam:cliente_productos"))]
        if producto:
            items.append((Equipo(tipo_producto=producto).producto_singular,
                          reverse("masiscam:producto_listado", args=[producto.lower()])))
        if equipo:
            items.append((equipo.numero_serie or equipo.nombre,
                          reverse("masiscam:ficha_detalle", args=[equipo.pk])))
        if vista in {"equipo_informe", "equipo_publico"}:
            items.append(("Informe", None))
        return [{"label": label, "url": url if i < len(items) - 1 else None}
                for i, (label, url) in enumerate(items)]
    if vista == "usuarios" or vista.startswith("usuario_"):
        items.append(("Usuarios", reverse("masiscam:usuarios")))
        if vista != "usuarios":
            items.append((context.get("titulo", "Usuario"), None))
    elif equipo or producto or vista.startswith("cliente") or vista == "ficha_crear":
        items.append(("Clientes", reverse("masiscam:clientes")))
        if cliente:
            items.append((cliente.nombre_comercial, reverse("masiscam:cliente_detalle", args=[cliente.pk])))
        elif equipo:
            items.append((equipo.razon_social_cliente or "Sin cliente", None))
        if producto:
            url = reverse("masiscam:producto_listado", args=[producto.lower()])
            if cliente:
                url += f"?cliente={cliente.pk}"
            items.append((Equipo(tipo_producto=producto).producto_singular, url))
        if equipo:
            items.append((equipo.numero_serie or f"EQUIPO-{equipo.pk}", reverse("masiscam:ficha_detalle", args=[equipo.pk])))
        acciones = {"ficha_crear": "Crear", "cliente_crear": "Crear", "ficha_editar": "Editar",
                    "cliente_editar": "Editar", "equipo_editar": "Editar", "equipo_etiqueta": "Etiqueta QR",
                    "equipo_publico": "Informe", "registro_crear": "Registros"}
        if vista in acciones:
            items.append((acciones[vista], None))
    elif proyecto or vista in {"proyectos", "proyecto_crear"}:
        items.append(("Proyectos", reverse("masiscam:proyectos")))
        if proyecto:
            items.append((proyecto.nombre, reverse("masiscam:proyecto_detalle", args=[proyecto.pk])))
        if vista not in {"proyectos", "proyecto_detalle", "publico"}:
            items.append((context.get("titulo") or {"historial": "Historial", "etiqueta_qr": "Etiqueta QR"}.get(vista, "Crear"), None))
    # Anonymous reports expose no private navigation links.
    privado = request.user.is_authenticated
    return [{"label": label, "url": url if privado and i < len(items) - 1 else None}
            for i, (label, url) in enumerate(items)]


@register.simple_tag(takes_context=True)
def puede_ver_planos_taller(context):
    from django.conf import settings
    request = context.get('request')
    role = getattr(request, 'rol_masiscam', None)
    return bool(getattr(settings, 'MASISCAM_TECNICA_LOCAL', False) and role and role.rol == 'TALLER')


@register.simple_tag(takes_context=True)
def flujo_operativo(context):
    request=context.get('request');exp=context.get('expediente')
    if not request or request.GET.get('flujo')!='1' or not exp: return None
    role=getattr(request,'rol_masiscam',None)
    if not role or role.rol not in {'TALLER','OFICINA','PLANTA','GERENCIA'}: return None
    from masiscam.flujo_services import resumen
    from django.core.exceptions import PermissionDenied,ValidationError
    try: return resumen(exp,request.user)
    except (PermissionDenied,ValidationError): return None


@register.simple_tag(takes_context=True)
def puede_ver_solicitudes_taller(context):
    from django.conf import settings
    request=context.get('request');role=getattr(request,'rol_masiscam',None)
    return bool(role and role.rol in {'OFICINA','PLANTA','GERENCIA'} and
        getattr(settings,'MASISCAM_TECNICA_LOCAL',False) and getattr(settings,'MASISCAM_OPERACION_LOCAL',False))

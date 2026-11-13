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
        and not getattr(request,'cliente_usuario',None) and role and role.rol in {'TALLER', 'ADMIN', 'OFICINA'})


@register.simple_tag(takes_context=True)
def puede_ver_recepcion(context):
    request = context.get("request")
    rol = getattr(request, "rol_masiscam", None)
    return bool(request and request.user.is_authenticated and not getattr(request, "cliente_usuario", None)
                and rol and rol.rol in {'OFICINA', 'ADMIN'})


@register.simple_tag(takes_context=True)
def puede_gestionar_usuarios(context):
    request = context.get("request")
    return bool(request and request.user.is_authenticated and tiene_permiso(request, "usuarios"))


@register.simple_tag(takes_context=True)
def breadcrumbs(context):
    request = context.get("request")
    if request is None:
        return []
    vista = request.resolver_match.url_name if request.resolver_match else ""
    from django.conf import settings
    role = getattr(getattr(request, 'rol_masiscam', None), 'rol', None)
    if role=='OFICINA':
        from masiscam.oficina_views import SECCIONES
        trail=[('Inicio de oficina',reverse('masiscam:oficina_inicio'))]
        seccion=context.get('seccion') or getattr(request.resolver_match,'kwargs',{}).get('seccion')
        exp_oficina=context.get('expediente')
        if vista.startswith('cliente'):
            trail.append(('Clientes',reverse('masiscam:clientes')))
            if vista!='clientes': trail.append((context.get('titulo') or 'Cliente',None))
        elif exp_oficina:
            trail.append(('Solicitudes',reverse('masiscam:oficina_seccion',args=['solicitudes'])))
            accion=context.get('accion') or {}
            label='Recepción' if vista in {'expediente_detalle','recepcion_finalizar'} else 'Cotización' if accion.get('op') in {'cotizar','aceptacion','respaldo'} else 'Gestión comercial'
            trail.append((label,None))
        elif seccion and seccion!='inicio': trail.append((SECCIONES.get(seccion,'Oficina'),None))
        elif vista=='flujo_bandeja': trail.append(('Solicitudes',None))
        elif vista not in {'dashboard','oficina_inicio'}: trail.append((context.get('titulo') or 'Consulta autorizada',None))
        return [{'label':label,'url':url if i<len(trail)-1 else None} for i,(label,url) in enumerate(trail)]
    exp = context.get('expediente')
    revision = context.get('diagnostico') or context.get('revision') or context.get('calibracion')
    if not exp and revision:
        exp = getattr(revision, 'expediente', None)
    equipo_operativo = context.get('equipo') or (exp.registro.equipo if exp else None)
    labels = {
        'dashboard':'Inicio', 'taller_inicio':'Mis equipos', 'taller_estado':'Mis trabajos',
        'taller_cuenta':'Mi cuenta', 'ficha_crear':'Registrar equipo', 'equipo_crear':'Registrar equipo',
        'expedientes':'Mis trabajos' if role=='TALLER' else 'Expedientes',
        'recepcion_crear':'Registrar recepción', 'expediente_detalle':'Recepción', 'recepcion_finalizar':'Recepción',
        'flujo_detalle':'Etapas del mantenimiento', 'flujo_bandeja':'Solicitudes de taller',
        'tecnica_inicio':'Trabajo técnico', 'tecnica_expediente':'Trabajo técnico',
        'tecnica_revision':'Inspección' if context.get('tipo')=='INSPECCION' else 'Diagnóstico',
        'operacion_inicio':'Gestión de operaciones', 'operacion_detalle':'Repuestos y reparación',
        'operacion_operar':'Actividad de mantenimiento', 'calibracion_inicio':'Calibraciones',
        'calibracion_expediente':'Calibración', 'calibracion_revision':'Mediciones y calibración',
        'calibracion_plantillas':'Protocolos de calibración', 'calibracion_plantilla':'Protocolo de calibración',
        'cierre_dashboard':'Seguimiento', 'cierre_detalle':'Informe final y cierre', 'cierre_historial':'Historial',
        'biblioteca':'Planos y piezas' if role=='TALLER' else 'Biblioteca técnica',
        'biblioteca_referencia':'Referencia técnica', 'biblioteca_referencia_crear':'Nueva referencia',
        'biblioteca_referencia_editar':'Editar referencia', 'biblioteca_componentes':'Componentes',
        'biblioteca_componente_editar':'Componente', 'plano':'Plano y componentes',
        'plano_importar':'Importar plano', 'seleccionar_empresa':'Empresa activa',
    }
    if role == 'TALLER' or exp or vista in labels and vista not in {'dashboard','ficha_crear','equipo_crear'}:
        home = 'taller_inicio' if role=='TALLER' else 'dashboard'
        home_url = reverse('masiscam:'+home) if role or request.user.is_superuser else reverse('accounts:seleccionar_empresa')
        trail = [('Inicio de taller' if role=='TALLER' else 'Inicio', home_url)]
        if exp or equipo_operativo:
            if role=='TALLER':
                trail.append(('Mis equipos',reverse('masiscam:taller_inicio')+'#trabajos-titulo'))
                trail.append((equipo_operativo.nombre,reverse('masiscam:taller_equipo',args=[equipo_operativo.pk])))
            elif exp:
                listing='tecnica_inicio' if role=='TALLER' else 'expedientes'
                trail.append(('Expedientes',reverse('masiscam:'+listing)))
            if exp:
                assigned = role!='TALLER' or exp.asignaciones_tecnicas.filter(tecnico=request.user,fin__isnull=True).exists()
                target = 'tecnica_expediente' if role=='TALLER' else 'flujo_detalle' if role in {'TALLER', 'OFICINA', 'ADMIN'} and assigned and settings.MASISCAM_TECNICA_LOCAL else 'expediente_detalle'
                trail.append((exp.numero,reverse('masiscam:'+target,args=[exp.pk])))
        elif vista.startswith('biblioteca') or vista.startswith('plano'):
            trail.append(('Planos y piezas' if role=='TALLER' else 'Biblioteca técnica',reverse('masiscam:biblioteca')))
        label=labels.get(vista, context.get('titulo') or 'Detalle')
        if vista=='taller_equipo':
            if exp: trail.append(('Etapas del mantenimiento',None))
        elif not trail or label != trail[-1][0]:
            trail.append((label,None))
        # The current page is text; never offer a circular last breadcrumb.
        if vista==home: trail=[('Inicio',None)]
        return [{'label':label,'url':url if request.user.is_authenticated and i<len(trail)-1 else None}
                for i,(label,url) in enumerate(trail)]
    items = [("Inicio", reverse("masiscam:dashboard"))]
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
    if len(items)==1 and vista!='dashboard':
        items.append((context.get('titulo') or 'Detalle',None))
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
    if not role or role.rol not in {'TALLER', 'OFICINA', 'ADMIN'}: return None
    from masiscam.flujo_services import resumen
    from django.core.exceptions import PermissionDenied,ValidationError
    try: return resumen(exp,request.user)
    except (PermissionDenied,ValidationError): return None


@register.simple_tag(takes_context=True)
def puede_ver_solicitudes_taller(context):
    from django.conf import settings
    request=context.get('request');role=getattr(request,'rol_masiscam',None)
    return bool(role and role.rol in {'OFICINA', 'ADMIN'} and
        getattr(settings,'MASISCAM_TECNICA_LOCAL',False) and getattr(settings,'MASISCAM_OPERACION_LOCAL',False))

from django import template
from django.urls import reverse

from masiscam.models import Equipo
from masiscam.access import tiene_permiso

register = template.Library()


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

from .access import paginar_listado
"""Office workspace: projections of existing, authorized commercial records."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Sum, Q, F
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from .access import masiscam_access_required
from .models import (SolicitudRepuestos, PropuestaComercial, DecisionComercial,
    AdquisicionRepuesto, DetalleEnvio, EnvioRepuestos, ExpedienteMantenimiento)

SECCIONES = {
    'solicitudes':'Solicitudes de taller', 'cotizaciones':'Cotizaciones',
    'ventas':'Autorizaciones de servicio',
    'documentos':'Documentación comercial autorizada', 'cuenta':'Mi cuenta',
}
# Solo lectura de registros históricos: MASISCAM ya no compra ni envía repuestos.
HISTORICAS = {'compras':'Compras históricas de repuestos', 'guias':'Guías de envío históricas'}


def datos(empresa):
    scope=Q(expediente__in=ExpedienteMantenimiento.objects.filter(empresa=empresa))&~Q(expediente__registro__equipo__estado='INACTIVO')
    solicitudes=SolicitudRepuestos.objects.filter(scope)
    propuestas=PropuestaComercial.objects.filter(scope).annotate(
        sustituida=Exists(PropuestaComercial.objects.filter(expediente_id=OuterRef('expediente_id'),version__gt=OuterRef('version')))).filter(sustituida=False)
    compras=AdquisicionRepuesto.objects.filter(scope).annotate(
        enviado=Sum('envios__cantidad')).filter(Q(enviado__isnull=True)|Q(enviado__lt=F('cantidad')))
    decisiones=DecisionComercial.objects.filter(scope,decision='APROBADO',propuesta__in=propuestas)
    envios=EnvioRepuestos.objects.filter(scope)
    return solicitudes,propuestas,compras,decisiones,envios


@require_GET
@masiscam_access_required
def inicio(request, seccion='inicio'):
    if not request.rol_masiscam or request.rol_masiscam.rol!='OFICINA':
        raise PermissionDenied('Espacio exclusivo de OFICINA.')
    if seccion!='inicio' and seccion not in SECCIONES and seccion not in HISTORICAS:
        from django.http import Http404
        raise Http404
    solicitudes,propuestas,compras,decisiones,envios=datos(request.empresa_activa)
    pendientes=solicitudes.exclude(diagnostico_id__in=propuestas.values('diagnostico_id'))
    por_aceptar=propuestas.filter(decision__isnull=True)
    indicadores=[
        ('Solicitudes pendientes',pendientes.count(),'solicitudes'),
        ('Cotizaciones pendientes',por_aceptar.count(),'cotizaciones'),
        ('Autorizaciones comerciales pendientes',por_aceptar.count(),'ventas')]
    fuentes={'inicio':solicitudes,'solicitudes':solicitudes,'cotizaciones':propuestas,'compras':AdquisicionRepuesto.objects.filter(
        expediente__in=ExpedienteMantenimiento.objects.filter(empresa=request.empresa_activa)).exclude(expediente__registro__equipo__estado='INACTIVO'),
        'ventas':decisiones,'guias':envios,'documentos':solicitudes,'cuenta':solicitudes.none()}
    qs=fuentes[seccion].select_related('expediente__registro__equipo','expediente__cliente_contractual').order_by('-pk')
    buscar=request.GET.get('q','').strip()[:120]
    pagina=paginar_listado(request,qs,{'expediente__numero':'Expediente','expediente__cliente_contractual__ruc':'RUC','expediente__cliente_contractual__nombre_comercial':'Cliente','expediente__registro__equipo__nombre':'Equipo','expediente__registro__equipo__numero_serie':'Serie'})
    actuales={p.expediente_id:p for p in propuestas.filter(expediente_id__in=[row.expediente_id for row in pagina])}
    from .flujo_services import aprobacion_conjunta_vigente
    for row in pagina:
        propuesta=actuales.get(row.expediente_id)
        row.estado_revision=('Aprobado por ADMINISTRADOR' if aprobacion_conjunta_vigente(propuesta)
            else 'Pendiente de aprobación') if propuesta else 'Pendiente de cotización'
        row.enlace=reverse('masiscam:flujo_detalle',args=[row.expediente_id])
        row.resumen_comercial=(f'{row.cantidad} {row.unidad} · {row.proveedor}' if seccion=='compras' else
            f'Guía {row.numero_guia}' if seccion=='guias' else
            'Aceptación del cliente registrada; su vigencia se verifica antes de operar.' if seccion=='ventas' else
            f'Cotización v{row.version}' if seccion=='cotizaciones' else 'Solicitud recibida de TALLER')
    return render(request,'masiscam/oficina.html',dict(seccion=seccion,titulo=SECCIONES.get(seccion) or HISTORICAS.get(seccion,'Inicio de oficina'),
        indicadores=indicadores,pagina=pagina,buscar=buscar))

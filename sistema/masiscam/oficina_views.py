"""Office workspace: projections of existing, authorized commercial records."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Sum, Q, F
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from .access import masiscam_access_required
from .models import (SolicitudRepuestos, PropuestaComercial, DecisionComercial,
    AdquisicionRepuesto, DetalleEnvio, EnvioRepuestos)

SECCIONES = {
    'solicitudes':'Solicitudes de taller', 'cotizaciones':'Cotizaciones',
    'compras':'Compras', 'ventas':'Ventas', 'guias':'Guías de envío',
    'documentos':'Documentación comercial autorizada', 'cuenta':'Mi cuenta',
}


def datos(empresa):
    scope=Q(expediente__empresa=empresa)&~Q(expediente__registro__equipo__estado='INACTIVO')
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
    if seccion!='inicio' and seccion not in SECCIONES:
        from django.http import Http404
        raise Http404
    solicitudes,propuestas,compras,decisiones,envios=datos(request.empresa_activa)
    pendientes=solicitudes.exclude(diagnostico_id__in=propuestas.values('diagnostico_id'))
    por_aceptar=propuestas.filter(decision__isnull=True)
    indicadores=[
        ('Solicitudes pendientes',pendientes.count(),'solicitudes'),
        ('Cotizaciones pendientes',por_aceptar.count(),'cotizaciones'),
        ('Compras en proceso',compras.values('solicitud_id').distinct().count(),'compras'),
        ('Repuestos pendientes de envío',compras.count(),'guias'),
        ('Autorizaciones comerciales pendientes',por_aceptar.count(),'ventas')]
    fuentes={'inicio':solicitudes,'solicitudes':solicitudes,'cotizaciones':propuestas,'compras':AdquisicionRepuesto.objects.filter(
        expediente__empresa=request.empresa_activa).exclude(expediente__registro__equipo__estado='INACTIVO'),
        'ventas':decisiones,'guias':envios,'documentos':solicitudes,'cuenta':solicitudes.none()}
    qs=fuentes[seccion].select_related('expediente__registro__equipo','expediente__cliente_contractual').order_by('-pk')
    buscar=request.GET.get('q','').strip()[:120]
    if buscar: qs=qs.filter(Q(expediente__numero__icontains=buscar)|Q(expediente__cliente_contractual__ruc__icontains=buscar)|Q(expediente__registro__equipo__numero_serie__icontains=buscar))
    pagina=Paginator(qs,20).get_page(request.GET.get('page'))
    for row in pagina:
        row.enlace=reverse('masiscam:flujo_detalle',args=[row.expediente_id])
        row.resumen_comercial=(f'{row.cantidad} {row.unidad} · {row.proveedor}' if seccion=='compras' else
            f'Guía {row.numero_guia}' if seccion=='guias' else
            'Aceptación del cliente registrada; su vigencia se verifica antes de operar.' if seccion=='ventas' else
            f'Cotización v{row.version}' if seccion=='cotizaciones' else 'Solicitud recibida de TALLER')
    return render(request,'masiscam/oficina.html',dict(seccion=seccion,titulo=SECCIONES.get(seccion,'Inicio de oficina'),
        indicadores=indicadores,pagina=pagina,buscar=buscar))

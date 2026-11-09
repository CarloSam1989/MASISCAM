from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Prefetch, Subquery
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .access import autorizar_creacion_equipo, equipos_taller, masiscam_access_required
from .models import ActividadReparacion, Calibracion, CierreExpediente, Equipo, InformeFinal
from .recepcion_services import expedientes_autorizados


@require_GET
@masiscam_access_required
def taller_inicio(request):
    autorizar_creacion_equipo(usuario=request.user, empresa=request.empresa_activa)
    equipos = equipos_taller(usuario=request.user, empresa=request.empresa_activa).order_by('-creado_en')
    actuales = expedientes_autorizados(request.user, request.empresa_activa).filter(
        registro__equipo_id=OuterRef('pk')).order_by('-correlativo')
    equipos = equipos.annotate(expediente_actual_id=Subquery(actuales.values('pk')[:1]))
    return render(request, 'masiscam/taller_inicio.html', {
        'tipos': Equipo.TipoProducto.choices,
        'pagina': Paginator(equipos, 25).get_page(request.GET.get('page')),
        'expedientes': estados_autorizados(request).order_by('-pk')[:10],
    })


@require_GET
@masiscam_access_required
def taller_estado(request):
    autorizar_creacion_equipo(usuario=request.user, empresa=request.empresa_activa)
    expedientes = estados_autorizados(request)
    numero = request.GET.get('numero', '').strip()[:80]
    if numero:
        expedientes = expedientes.filter(numero=numero)
    return render(request, 'masiscam/taller_estado.html', {
        'pagina': Paginator(expedientes, 25).get_page(request.GET.get('page')), 'numero': numero,
    })


def estados_autorizados(request):
    return expedientes_autorizados(request.user, request.empresa_activa).select_related(
        'registro__equipo', 'recepcion__confirmacion').prefetch_related('etapas_tecnicas',
        Prefetch('calibraciones', queryset=Calibracion.objects.only('id', 'expediente_id', 'estado', 'revision').order_by('-revision'), to_attr='calibraciones_estado'),
        Prefetch('informes_finales', queryset=InformeFinal.objects.only('id', 'expediente_id', 'estado', 'version').order_by('-version'), to_attr='informes_estado'),
    ).annotate(
        cerrado=Exists(CierreExpediente.objects.filter(expediente_id=OuterRef('pk'))),
        tiene_reparacion=Exists(ActividadReparacion.objects.filter(expediente_id=OuterRef('pk'))),
    )


@require_GET
@masiscam_access_required
def taller_cuenta(request):
    autorizar_creacion_equipo(usuario=request.user, empresa=request.empresa_activa)
    return render(request, 'masiscam/taller_cuenta.html')

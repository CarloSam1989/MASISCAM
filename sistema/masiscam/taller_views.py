from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Subquery
from django.shortcuts import render
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.urls import reverse
from django.conf import settings
from django.views.decorators.http import require_GET

from .access import autorizar_creacion_equipo, equipos_taller, masiscam_access_required
from .models import ActividadReparacion, Calibracion, CierreExpediente, Equipo, InformeFinal, EnvioRepuestos
from .recepcion_services import expedientes_autorizados


@require_GET
@masiscam_access_required
def taller_inicio(request):
    autorizar_creacion_equipo(usuario=request.user, empresa=request.empresa_activa)
    equipos = equipos_taller(usuario=request.user, empresa=request.empresa_activa).select_related('cliente').order_by('-creado_en', '-pk')
    expedientes = expedientes_autorizados(request.user, request.empresa_activa).annotate(
        cerrado=Exists(CierreExpediente.objects.filter(expediente_id=OuterRef('pk'))))
    actuales = expedientes.filter(registro__equipo_id=OuterRef('pk')).order_by('-correlativo')
    equipos = equipos.annotate(
        expediente_actual_id=Subquery(actuales.values('pk')[:1]),
        expediente_numero=Subquery(actuales.values('numero')[:1]),
        recepcion_confirmada=Subquery(actuales.values('recepcion__confirmacion__pk')[:1]),
        expediente_cerrado=Subquery(actuales.values('cerrado')[:1]),
    )
    indicadores = expedientes.aggregate(
        activos=Count('pk', filter=Q(cerrado=False)),
        finalizados=Count('pk', filter=Q(cerrado=True)),
        pendientes=Count('pk', filter=Q(recepcion__confirmacion__isnull=True)),
    )
    indicadores['equipos'] = equipos.count()
    indicadores['pendientes'] += equipos.filter(expediente_actual_id__isnull=True).count()
    return render(request, 'masiscam/taller_inicio.html', {
        'tipos': Equipo.TipoProducto.choices,
        'pagina': Paginator(equipos, 12).get_page(request.GET.get('page')),
        'indicadores': indicadores,
        'pendientes': estados_autorizados(request).filter(cerrado=False).order_by('-creado_en')[:6],
        'planos_habilitados': settings.MASISCAM_TECNICA_LOCAL,
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
        repuestos_enviados=Exists(EnvioRepuestos.objects.filter(expediente_id=OuterRef('pk'),confirmacion__isnull=True)),
    )


@require_GET
@masiscam_access_required
def taller_cuenta(request):
    autorizar_creacion_equipo(usuario=request.user, empresa=request.empresa_activa)
    return render(request, 'masiscam/taller_cuenta.html')


@require_GET
@masiscam_access_required
def taller_equipo(request, pk):
    equipo = get_object_or_404(equipos_taller(usuario=request.user, empresa=request.empresa_activa).select_related('cliente'), pk=pk)
    mantenimientos = expedientes_autorizados(request.user, request.empresa_activa).filter(
        registro__equipo=equipo).order_by('-correlativo')
    if request.GET.get('expediente'):
        raw=request.GET['expediente']
        if not raw.isascii() or not raw.isdecimal() or len(raw)>18: raise Http404
        exp=get_object_or_404(mantenimientos,pk=raw)
    else:
        exp=mantenimientos.first()
    asignado=bool(exp and exp.asignaciones_tecnicas.filter(tecnico=request.user,fin__isnull=True).exists())
    if exp and asignado and settings.MASISCAM_TECNICA_LOCAL:
        from .flujo_views import contexto
        return render(request,'masiscam/flujo_detalle.html',contexto(request,exp))
    pasos=[dict(nombre=nombre,estado='Listo' if n==0 else 'Pendiente') for n,nombre in enumerate(
        ['Registrar equipo','Seleccionar piezas','Oficina cotiza y envía','Taller repara','Generar informe'])]
    mensaje='Inicia un expediente de mantenimiento para inspeccionar y seleccionar piezas. No necesitas confirmar la recepción ni contar con autorización comercial para inspeccionar.'
    accion=reverse('masiscam:recepcion_crear')+f'?equipo={equipo.pk}' if not exp else None
    if exp:
        pasos[1]['estado']='Bloqueado'
        mensaje='PLANTA debe asignar el responsable para continuar este expediente.' if not asignado else 'La selección de piezas requiere habilitar el módulo técnico. Comunícalo al responsable del sistema.'
    return render(request,'masiscam/taller_equipo.html',dict(equipo=equipo,expediente=exp,mantenimientos=mantenimientos,
        pasos=pasos,paso=2,mensaje=mensaje,accion=accion))

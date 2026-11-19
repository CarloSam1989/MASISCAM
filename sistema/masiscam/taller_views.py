from .access import paginar_listado
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Subquery
from django.shortcuts import render
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.urls import reverse
from django.conf import settings
from django.views.decorators.http import require_GET, require_POST
from django.contrib import messages
from django.shortcuts import redirect
from django.db import transaction

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
        'pagina': paginar_listado(request,equipos,{'nombre':'Equipo','numero_serie':'Serie','cliente__nombre_comercial':'Cliente','ubicacion':'Ubicacion','tipo_producto':'Tipo'}),
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
        'pagina': paginar_listado(request,expedientes,{'numero':'Expediente','cliente_contractual__nombre_comercial':'Cliente','registro__equipo__nombre':'Equipo','registro__equipo__numero_serie':'Serie'}), 'numero': numero,
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
    planos = None
    if settings.MASISCAM_TECNICA_LOCAL and equipo.tipo_producto == Equipo.TipoProducto.REDUCTOR:
        from .tecnica_services import planos_compatibles_equipo
        planos = planos_compatibles_equipo(usuario=request.user, empresa=request.empresa_activa, equipo=equipo)
    if exp and asignado and settings.MASISCAM_TECNICA_LOCAL:
        from .flujo_views import contexto
        ctx = contexto(request,exp)
        ctx['planos_compatibles'] = planos
        ctx['plano_acceso_expediente'] = True
        return render(request,'masiscam/flujo_detalle.html',ctx)
    pasos=[dict(nombre=nombre,estado='Listo' if n==0 else 'Pendiente') for n,nombre in enumerate(
        ['Registrar equipo','Seleccionar piezas','Oficina cotiza y envía','Taller repara','Generar informe'])]
    mensaje='Inicia un expediente de mantenimiento para inspeccionar y seleccionar piezas. No necesitas confirmar la recepción ni contar con autorización comercial para inspeccionar.'
    accion=reverse('masiscam:recepcion_crear')+f'?equipo={equipo.pk}' if not exp else None
    if exp:
        pasos[1]['estado']='Bloqueado'
        if not asignado:
            mensaje='ADMINISTRADOR debe asignar el responsable para continuar este expediente.'
        elif not settings.MASISCAM_TECNICA_LOCAL:
            mensaje=('Fase técnica deshabilitada: el responsable de configuración debe activar '
                'MASISCAM_TECNICA_ENABLED para habilitar inspección y selección de piezas.')
    return render(request,'masiscam/taller_equipo.html',dict(equipo=equipo,expediente=exp,mantenimientos=mantenimientos,
        pasos=pasos,paso=2,mensaje=mensaje,accion=accion,planos_compatibles=planos,plano_acceso_expediente=asignado))


@require_POST
@masiscam_access_required
def solicitar_plano(request, pk):
    from accounts.models import Empresa
    from .models import Auditoria
    from .tecnica_services import planos_compatibles_equipo
    equipo = get_object_or_404(equipos_taller(usuario=request.user, empresa=request.empresa_activa), pk=pk)
    raw = request.POST.get('referencia', '')
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 18:
        raise Http404
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=request.empresa_activa.pk, activa=True)
        lista = planos_compatibles_equipo(usuario=request.user, empresa=request.empresa_activa, equipo=equipo)
        ref = next((r for r in lista['candidatos'] if r.pk == int(raw)), None)
        if ref is None:
            raise Http404
        if not ref.aprobada and not Auditoria.objects.filter(empresa=request.empresa_activa,
                accion='PLANO_APLICABILIDAD_SOLICITADA', objeto_tipo='ReferenciaTecnica',
                objeto_id=str(ref.pk), detalle__equipo=equipo.pk).exists():
            Auditoria.objects.create(empresa=request.empresa_activa, usuario=request.user,
                proyecto=equipo.proyecto, accion='PLANO_APLICABILIDAD_SOLICITADA',
                objeto_tipo='ReferenciaTecnica', objeto_id=str(ref.pk),
                detalle={'equipo': equipo.pk, 'revision': ref.revision})
    messages.success(request, 'Solicitud registrada para revision de ADMINISTRADOR.' if not ref.aprobada
                     else 'La aplicabilidad ya esta aprobada.')
    return redirect('masiscam:taller_equipo', pk=equipo.pk)

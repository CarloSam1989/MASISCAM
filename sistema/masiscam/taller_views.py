from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Subquery
from django.shortcuts import render
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.urls import reverse
from django.conf import settings
import uuid
from django.views.decorators.http import require_GET

from .access import autorizar_creacion_equipo, equipos_taller, masiscam_access_required
from .models import ActividadReparacion, Calibracion, CierreExpediente, Equipo, InformeFinal
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
    )


@require_GET
@masiscam_access_required
def taller_cuenta(request):
    autorizar_creacion_equipo(usuario=request.user, empresa=request.empresa_activa)
    return render(request, 'masiscam/taller_cuenta.html')


@require_GET
@masiscam_access_required
def taller_equipo(request, pk):
    equipo = get_object_or_404(equipos_taller(usuario=request.user, empresa=request.empresa_activa), pk=pk)
    mantenimientos = expedientes_autorizados(request.user, request.empresa_activa).filter(
        registro__equipo=equipo).order_by('-correlativo')
    if request.GET.get('expediente'):
        try:
            expediente_id = int(request.GET['expediente'])
        except ValueError:
            raise Http404 from None
        exp = get_object_or_404(mantenimientos, pk=expediente_id)
    else:
        exp = mantenimientos.first()
    asignado, cerrado = False, False
    pasos = [('Registro', 'Completado', '', '')]
    if not exp:
        pasos.append(('Recepción', 'Pendiente', reverse('masiscam:recepcion_crear') + f'?equipo={pk}', 'Registrar recepción'))
        pasos.extend((nombre, 'Pendiente de recepción', '', '') for nombre in (
            'Inspección', 'Plano y piezas', 'Diagnóstico', 'Autorización comercial', 'Repuestos',
            'Reparación y montaje', 'Calibración · 16 mediciones', 'Aprobación PLANTA', 'Informe final'))
    else:
        recibido = hasattr(exp.recepcion, 'confirmacion')
        pasos.append(('Recepción', 'Completada' if recibido else 'Pendiente',
                      reverse('masiscam:expediente_detalle', args=[exp.pk]), 'Ver recepción y guía'))
        from . import operacion_services as o
        asignado = exp.asignaciones_tecnicas.filter(tecnico=request.user, fin__isnull=True).exists()
        cerrado = CierreExpediente.objects.filter(expediente=exp).exists()
        habilitado = recibido and asignado and not cerrado
        inspeccion = exp.inspeccion_set.order_by('-revision').first()
        diagnostico = exp.diagnostico_set.order_by('-revision').first()
        tecnica_url = reverse('masiscam:tecnica_expediente', args=[exp.pk])
        for nombre, obj in [('Inspección', inspeccion), ('Diagnóstico', diagnostico)]:
            url = reverse('masiscam:tecnica_revision', args=['INSPECCION' if nombre == 'Inspección' else 'DIAGNOSTICO', obj.pk]) if obj else tecnica_url
            estado = obj.get_estado_display() if obj else 'Pendiente'
            accion = ('Continuar borrador' if not obj.enviado_en else 'Consultar revisión') if obj else 'Iniciar etapa'
            permitido = habilitado and (nombre == 'Inspección' or bool(inspeccion and inspeccion.enviado_en))
            pasos.append((nombre, estado, url if permitido and settings.MASISCAM_TECNICA_LOCAL else '', accion))
        pasos.insert(3, ('Plano y piezas', 'Borrador editable' if diagnostico and not diagnostico.enviado_en else 'Según diagnóstico',
            reverse('masiscam:tecnica_revision', args=['DIAGNOSTICO', diagnostico.pk]) if habilitado and diagnostico and settings.MASISCAM_TECNICA_LOCAL else '', 'Seleccionar piezas'))
        autorizado = False
        operacion_url = reverse('masiscam:operacion_detalle', args=[exp.pk])
        if settings.MASISCAM_OPERACION_LOCAL and asignado:
            try:
                o.exigir_autorizacion(exp)
                autorizado = True
            except (o.ValidationError, o.PermissionDenied):
                pass
        pasos.append(('Autorización comercial', 'Vigente' if autorizado else 'Pendiente de gestión comercial', '', ''))
        reparacion = o.estado(exp) if settings.MASISCAM_OPERACION_LOCAL and asignado else 'Pendiente'
        entregas = exp.recepcionrepuesto_set.count()
        pasos.append(('Repuestos', f'{entregas} entregas registradas' if entregas else 'Sin entregas registradas',
            operacion_url if autorizado and habilitado else '', 'Registrar repuestos'))
        pasos.append(('Reparación y montaje', reparacion.replace('_', ' ').capitalize(),
            operacion_url if autorizado and habilitado else '', 'Continuar trabajo'))
        cal = exp.calibraciones.order_by('-revision').first()
        cal_url = reverse('masiscam:calibracion_revision', args=[cal.pk]) if cal else reverse('masiscam:calibracion_expediente', args=[exp.pk])
        pasos.append(('Calibración · 16 mediciones', cal.get_estado_display() if cal else 'Pendiente',
            cal_url if habilitado and settings.MASISCAM_CALIBRACION_LOCAL and reparacion == 'REPARACION_FINALIZADA' else '', 'Continuar calibración'))
        pasos.append(('Aprobación PLANTA', 'Aprobada' if cal and cal.estado == 'APROBADA' else 'Revisión independiente pendiente', '', ''))
        pasos.append(('Informe final', 'Consultar versiones', reverse('masiscam:cierre_detalle', args=[exp.pk])
            if asignado and settings.MASISCAM_CIERRE_LOCAL else '', 'Ver informe e historial'))
    return render(request, 'masiscam/taller_equipo.html', {'equipo': equipo, 'expediente': exp, 'pasos': pasos,
        'clave': uuid.uuid4(), 'asignado': asignado, 'cerrado': cerrado,
        'mantenimientos': mantenimientos,
        'ver_historial': bool(exp and asignado and settings.MASISCAM_CIERRE_LOCAL)})

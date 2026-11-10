from django.urls import reverse
import uuid
from functools import wraps
from django.conf import settings
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import IntegrityError
from django.http import Http404,FileResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_GET,require_http_methods
from .access import masiscam_access_required
from .models import (ExpedienteMantenimiento,PropuestaComercial,DecisionComercial,RecepcionRepuesto,
    ActividadReparacion,EmisionOperativa,PiezaRequerida,Auditoria,Documento)
from . import operacion_services as svc
from .operacion_forms import OperacionForm,AREAS,LABELS
from .tecnica_views import _error


def local(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if not getattr(settings,'MASISCAM_OPERACION_LOCAL',False):
            raise Http404
        return view(request,*args,**kwargs)
    return wrapped


@require_GET
@local
@masiscam_access_required
def inicio(request):
    rol=svc.autorizar(request.user,request.empresa_activa).rol
    from .tecnica_services import expedientes_operativos
    qs=expedientes_operativos(request.user,request.empresa_activa)
    return render(request,'masiscam/operacion_inicio.html',dict(expedientes=qs.distinct()))


@require_GET
@local
@masiscam_access_required
def detalle(request,pk):
    exp=svc.expediente(request.user,request.empresa_activa,pk)
    rol=svc.autorizar(request.user,request.empresa_activa,expediente=exp).rol
    state=svc.estado(exp)
    auth=DecisionComercial.objects.filter(expediente=exp,decision='APROBADO').order_by('-propuesta__version').first()
    actions=[op for op,roles in AREAS.items() if rol in roles]
    if state not in {'AUTORIZADO','EN_REPARACION','OBSERVADO'} or not svc.politica():
        actions=[op for op in actions if op not in {'actividad','uso','foto','resultado','revision'}]
    else:
        if not auth or not svc.autorizacion_valida(auth):
            actions=[op for op in actions if op not in {'actividad','uso','foto','resultado','revision'}]
    # Only the accepted work scope is operational; never disclose the budget,
    # representative identity or verification evidence to technicians/plant.
    commercial=rol in {'GERENCIA','OFICINA'}
    receipts=list(RecepcionRepuesto.objects.filter(expediente=exp).order_by('-pk'))
    pieces=list(PiezaRequerida.objects.filter(diagnostico__expediente=exp).order_by('pk'))
    for p in pieces:
        p.recibidos=[r for r in receipts if r.pieza_id==p.pk]
    return render(request,'masiscam/operacion_detalle.html',dict(expediente=exp,estado_operativo=state,
        politica=svc.politica(),acciones=[(op,LABELS[op]) for op in actions],comercial=commercial,
        alcance_autorizado=auth.propuesta.alcance if auth and svc.autorizacion_valida(auth) else None,
        propuestas=PropuestaComercial.objects.filter(expediente=exp).order_by('-version') if commercial else [],
        recepciones=receipts,piezas=pieces,actividades=ActividadReparacion.objects.filter(expediente=exp).order_by('pk'),
        emisiones=EmisionOperativa.objects.filter(expediente=exp).order_by('-pk'),
        documentos=svc.documentos_visibles(exp,rol),
        eventos=Auditoria.objects.filter(expediente=exp,accion__in=['REPUESTO_RECIBIDO','REPARACION_INICIADA','REPARACION_RESULTADO','REPARACION_REVISION','REPARACION_EVIDENCIA','OPERACION_DOCUMENTO_GENERADO']).order_by('-pk')[:100]))


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def operar(request,pk,op):
    if op not in AREAS:
        raise Http404
    exp=svc.expediente(request.user,request.empresa_activa,pk,AREAS[op])
    form=OperacionForm(request.POST or None,request.FILES or None,op=op,exp=exp,politica=svc.politica())
    if 'actividad_id' in form.fields:
        form.fields['actividad_id'].queryset=form.fields['actividad_id'].queryset.filter(autor=request.user)
    if request.method=='POST' and form.is_valid():
        data={k:(v.pk if hasattr(v,'pk') else v) for k,v in form.cleaned_data.items()}
        key=data.pop('clave')
        common=dict(usuario=request.user,empresa=request.empresa_activa,clave=key)
        try:
            if op=='propuesta':
                svc.proponer(**common,expediente_id=pk,diagnostico_id=data.pop('diagnostico_id'),archivo=data.pop('archivo'),datos=data)
            elif op=='decision':
                svc.decidir_comercial(**common,propuesta_id=data.pop('propuesta_id'),archivo=data.pop('archivo'),
                    archivo_facultad=data.pop('archivo_facultad'),datos=data)
            elif op=='respaldo':
                svc.respaldo_propuesta(**common,**data)
            elif op=='foto_repuesto':
                svc.evidencia_repuesto(**common,**data)
            elif op=='repuesto':
                svc.recibir_repuesto(**common,expediente_id=pk,archivo=data.pop('archivo'),fotografia=data.pop('fotografia'),datos=data)
            elif op=='actividad':
                svc.iniciar_actividad(**common,expediente_id=pk,datos=data)
            elif op=='uso':
                svc.usar_repuesto(**common,**data)
            elif op=='foto':
                svc.evidencia_actividad(**common,**data)
            elif op=='resultado':
                svc.terminar_actividad(**common,actividad_id=data.pop('actividad_id'),datos=data)
            elif op=='revision':
                svc.revisar_actividad(**common,**data)
            elif op=='documento':
                em=svc.reservar_documento(**common,expediente_id=pk,**data)
                from .operacion_tasks import generar_documento_operativo
                # Reservation survives a broker outage; retry keeps its ID.
                try:
                    generar_documento_operativo.apply_async(args=[em.pk],retry=False)
                except Exception:
                    from django.contrib import messages
                    messages.warning(request,'Reserva persistida. Cola no disponible; reintente la misma emisión.')
        except (ValidationError,IntegrityError,OSError,ObjectDoesNotExist) as exc:
            _error(form,exc)
        else:
            if request.GET.get('flujo') == '1':
                return redirect('masiscam:flujo_detalle', pk=pk)
            if request.rol_masiscam.rol == 'TALLER' and op == 'resultado':
                return redirect(reverse('masiscam:taller_equipo', args=[exp.registro.equipo_id]) + f'?expediente={exp.pk}')
            return redirect('masiscam:operacion_detalle',pk=pk)
    auth=DecisionComercial.objects.filter(expediente=exp,decision='APROBADO').order_by('-propuesta__version').first()
    return render(request,'masiscam/operacion_form.html',dict(form=form,operacion=LABELS[op],expediente=exp,politica=svc.politica(),
        alcance_autorizado=auth.propuesta.alcance if auth and svc.autorizacion_valida(auth) else None),
        status=400 if request.method=='POST' else 200)


@require_GET
@local
@masiscam_access_required
def documento(request,pk,documento_pk):
    exp=svc.expediente(request.user,request.empresa_activa,pk)
    rol=svc.autorizar(request.user,request.empresa_activa,expediente=exp).rol
    doc=get_object_or_404(svc.documentos_visibles(exp,rol),pk=documento_pk)
    try:
        svc.t.documento_valido(doc,request.empresa_activa)
        response=FileResponse(doc.archivo.open('rb'),as_attachment=True,filename=doc.nombre_original,content_type=doc.tipo_mime)
    except (OSError,ValidationError):
        raise Http404('Copia no disponible o no íntegra.') from None
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response

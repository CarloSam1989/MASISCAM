from functools import wraps
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError
from django.http import Http404,FileResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_GET,require_http_methods
from .access import masiscam_access_required
from .models import InformeFinal,Equipo
from .tecnica_forms import BaseForm
from . import cierre_services as s
from .cierre_dashboard import resumen


class AccionForm(BaseForm):
    motivo=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':3}))
    nueva_version=forms.BooleanField(required=False)
    decision=forms.ChoiceField(required=False,choices=[('APROBADO','Aprobar'),('OBSERVADO','Observar')])

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['nueva_version'].widget.attrs['class']='form-check-input'


def local(fn):
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        if not getattr(settings,'MASISCAM_CIERRE_LOCAL',False): raise Http404
        return fn(request,*args,**kwargs)
    return wrapped


@require_GET
@local
@masiscam_access_required
def dashboard(request):
    try: context=resumen(request.user,request.empresa_activa,request.GET)
    except ValidationError as exc:
        return render(request,'masiscam/cierre_dashboard.html',{'error':'; '.join(exc.messages),'filtros':request.GET},status=400)
    from .cierre_dashboard import listado
    from .models import AsignacionTecnica
    scope=listado(request.user,request.empresa_activa)
    options=list(scope.values('cliente_contractual_id','cliente_contractual__nombre_comercial','registro__equipo_id','registro__equipo__nombre'))
    clients=sorted({(str(r['cliente_contractual_id']),r['cliente_contractual__nombre_comercial']) for r in options},key=lambda r:r[1])
    equipment=sorted({(str(r['registro__equipo_id']),r['registro__equipo__nombre']) for r in options},key=lambda r:r[1])
    technicians=AsignacionTecnica.objects.filter(expediente_id__in=scope.values('pk'),fin__isnull=True).values_list('tecnico_id','tecnico__username').distinct()
    return render(request,'masiscam/cierre_dashboard.html',dict(context,filtros=request.GET,clientes_filtro=clients,
        equipos_filtro=equipment,tecnicos_filtro=[(str(pk),name) for pk,name in technicians]))


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def detalle(request,pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    role=s.autorizar(request.user,request.empresa_activa,exp=exp).rol
    form=AccionForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data;op=request.POST.get('op')
        try:
            if op=='reservar':
                em=s.reservar(usuario=request.user,empresa=request.empresa_activa,expediente_id=pk,clave=d['clave'],nueva_version=d['nueva_version'],motivo=d['motivo'])
                from .cierre_tasks import generar_informe_final
                try: generar_informe_final.apply_async(args=[em.pk],retry=False)
                except Exception:
                    from django.contrib import messages
                    messages.warning(request,'Reserva conservada. Cola no disponible; reintente la emisión.')
            elif op in {'revisar','cerrar'}:
                try: informe_id=int(request.POST.get('informe'))
                except (ValueError,TypeError): raise ValidationError('Seleccione una versión documental válida.') from None
                em=get_object_or_404(InformeFinal,pk=informe_id,expediente=exp)
                kwargs=dict(usuario=request.user,empresa=request.empresa_activa,informe_id=em.pk,clave=d['clave'],motivo=d['motivo'])
                if op=='revisar': s.revisar(**kwargs,decision=d['decision'])
                else: s.cerrar(**kwargs)
            else: raise ValidationError('Acción inválida.')
        except (ValidationError,IntegrityError,OSError) as exc:
            form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Operación no confirmada; revise copia persistente y vigencia.')
        else:
            if request.GET.get('flujo') == '1': return redirect('masiscam:flujo_detalle',pk=pk)
            return redirect('masiscam:cierre_detalle',pk=pk)
    blocked=''
    try: s.dependencias(exp)
    except (ValidationError,OSError) as exc: blocked='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Copia documental no verificable.'
    projection=resumen(request.user,request.empresa_activa,{'expediente':str(exp.pk)})['expedientes'][0]
    progress=[('Recepción','FINALIZADO' if projection.recibido else 'PENDIENTE'),
        ('Inspección',projection.inspeccion_estado or 'NO_INICIADO'),('Diagnóstico',projection.diagnostico_estado or 'NO_INICIADO'),
        ('Comercial',projection.comercial_estado or 'PENDIENTE'),('Calibración',projection.calibracion_estado or 'NO_INICIADO'),
        ('Informe final',projection.informe_estado or 'NO_INICIADO'),('Cierre','FINALIZADO' if projection.cerrado else 'PENDIENTE')]
    progress.insert(4,('Repuestos','PENDIENTES' if projection.piezas_pendientes else ('SIN_PENDIENTES' if projection.diagnostico_estado in {'APROBADO','FINALIZADO'} else 'POR_DEFINIR')))
    progress.insert(5,('Reparación','FINALIZADO' if projection.reparacion_aprobada else ('EN_PROCESO' if projection.tiene_actividad else 'NO_INICIADO')))
    progress=[(name,state.replace('_',' ')) for name,state in progress]
    return render(request,'masiscam/cierre_detalle.html',dict(expediente=exp,rol=role,form=form,bloqueo=blocked,progreso=progress,
        cerrado=hasattr(exp,'cierre'),informes=exp.informes_finales.select_related('documento','autor','revision__responsable','cierre__responsable').order_by('-version')),
        status=400 if request.method=='POST' else 200)


@require_GET
@local
@masiscam_access_required
def documento(request,pk,informe_pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    em=get_object_or_404(InformeFinal,pk=informe_pk,expediente=exp,documento__isnull=False)
    try:
        s.t.documento_valido(em.documento,request.empresa_activa)
        if em.documento.hash_sha256!=em.hash_sha256: raise ValidationError('Hash inválido.')
        response=FileResponse(em.documento.archivo.open('rb'),as_attachment=True,filename=em.documento.nombre_original,content_type='application/pdf')
    except (ValidationError,OSError): raise Http404('Documento ausente o no íntegro.') from None
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response


@require_GET
@local
@masiscam_access_required
def historial(request,pk):
    s.autorizar(request.user,request.empresa_activa)
    eq=get_object_or_404(Equipo,pk=pk,proyecto__empresa=request.empresa_activa)
    from .access import validar_consulta_equipo
    validar_consulta_equipo(eq,s.autorizar(request.user,request.empresa_activa).rol)
    from django.db.models import prefetch_related_objects
    rows=resumen(request.user,request.empresa_activa,{'equipo':str(pk)},limite=None)['expedientes']
    if s.autorizar(request.user,request.empresa_activa).rol in s.t.OPERADORES and not rows:
        raise PermissionDenied('No hay expedientes asignados autorizados para este equipo.')
    prefetch_related_objects(rows,'informes_finales__documento','informes_finales__revision__responsable','calibraciones','asignaciones_tecnicas__tecnico','informes_tecnicos','emisionoperativa_set')
    return render(request,'masiscam/cierre_historial.html',dict(equipo=eq,expedientes=rows))

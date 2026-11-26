from .access import paginar_listado
import uuid
from django.urls import reverse
from functools import wraps
from django.conf import settings
from django.core.exceptions import ValidationError,ObjectDoesNotExist,PermissionDenied
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404,FileResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_GET,require_http_methods
from .access import masiscam_access_required
from .models import PlantillaCalibracion,Calibracion,Documento,ExpedienteMantenimiento,DecisionCalibracion
from . import calibracion_services as s
from .calibracion_forms import (PlantillaForm,PuntosFormSet,InicioForm,DatosForm,MedicionForm,EvidenciaForm,
    DecisionForm,InformeForm,ValidarForm,AplicarForm,FormatoCalibracionForm,PreliminarForm)
from .tecnica_views import _error


class _InformeNoGenerado(Exception):
    """Señal interna: la hoja se guardó pero el informe no pudo reservarse; ya se preparó el formulario con el motivo."""


def local(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if not getattr(settings,'MASISCAM_CALIBRACION_LOCAL',False): raise Http404
        return view(request,*args,**kwargs)
    return wrapped


@require_GET
@local
@masiscam_access_required
def inicio(request):
    role=s.autorizar(request.user,request.empresa_activa).rol
    from .tecnica_services import expedientes_operativos
    qs=expedientes_operativos(request.user,request.empresa_activa)
    pagina=paginar_listado(request,qs.distinct(),{'numero':'Expediente','cliente_contractual__nombre_comercial':'Cliente','registro__equipo__nombre':'Equipo','registro__equipo__numero_serie':'Serie'})
    return render(request,'masiscam/calibracion_inicio.html',dict(expedientes=pagina,pagina=pagina,rol=role))


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def plantillas(request):
    role=s.autorizar(request.user,request.empresa_activa).rol
    if request.method=='POST': s.autorizar(request.user,request.empresa_activa,{'ADMIN'})
    form=PlantillaForm(request.POST or None,request.FILES or None,empresa=request.empresa_activa)
    points=PuntosFormSet(request.POST or None,prefix='puntos')
    if request.method=='POST' and form.is_valid() and points.is_valid():
        data=dict(form.cleaned_data);key=data.pop('clave');archivo=data.pop('archivo')
        for field in ('proyecto_id','sustituye_id'):
            data[field]=data[field].pk if data[field] else None
        rows=[]
        for p in points.cleaned_data:
            if not any(v not in (None,'') for v in p.values()): continue
            rows.append({k:str(v) if k in {'minimo','maximo'} and v is not None else v for k,v in p.items() if v not in (None,'')})
        data['definicion']=dict(mediciones=rows,parametros=data.pop('parametros') or [],
            aplicacion={k:data.pop(k) for k in ('marca','modelo','version_equipo','procedimiento_version')})
        try:
            result=s.crear_plantilla(usuario=request.user,empresa=request.empresa_activa,clave=key,datos=data,archivo=archivo)
        except (ValidationError,IntegrityError,OSError) as exc: _error(form,exc)
        else: return redirect('masiscam:calibracion_plantilla',pk=result.pk)
    qs=PlantillaCalibracion.objects.filter(empresa=request.empresa_activa)
    if role in s.t.OPERADORES:
        qs=qs.filter(aplicabilidades__equipo__registros__expediente_mantenimiento__asignaciones_tecnicas__tecnico=request.user,
            aplicabilidades__equipo__registros__expediente_mantenimiento__asignaciones_tecnicas__fin__isnull=True).distinct()
    return render(request,'masiscam/calibracion_plantillas.html',dict(form=form,puntos=points,plantillas=(pagina:=paginar_listado(request,qs)),pagina=pagina,rol=role),status=400 if request.method=='POST' else 200)


def _p(request,pk):
    s.autorizar(request.user,request.empresa_activa)
    p=get_object_or_404(PlantillaCalibracion,pk=pk,empresa=request.empresa_activa)
    role=s.autorizar(request.user,request.empresa_activa).rol
    if role in s.t.OPERADORES and not p.aplicabilidades.filter(equipo__registros__expediente_mantenimiento__asignaciones_tecnicas__tecnico=request.user,
            equipo__registros__expediente_mantenimiento__asignaciones_tecnicas__fin__isnull=True).exists(): raise Http404
    if role == 'TALLER' and not p.aplicabilidades.filter(equipo_id__in=s.t.expedientes_operativos(request.user,request.empresa_activa).values('registro__equipo_id')).exists(): raise Http404
    return p


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def plantilla(request,pk):
    p=_p(request,pk);op=request.POST.get('op','validar')
    form=(AplicarForm(request.POST or None,empresa=request.empresa_activa,plantilla=p) if op=='aplicar' else ValidarForm(request.POST or None))
    if request.method=='POST' and form.is_valid():
        data=dict(form.cleaned_data)
        if 'equipo_id' in data: data['equipo_id']=data['equipo_id'].pk
        try:
            (s.aplicar_plantilla if op=='aplicar' else s.validar_plantilla)(usuario=request.user,empresa=request.empresa_activa,plantilla_id=p.pk,**data)
        except (ValidationError,IntegrityError,OSError) as exc: _error(form,exc)
        else: return redirect('masiscam:calibracion_plantilla',pk=pk)
    return render(request,'masiscam/calibracion_plantilla.html',dict(plantilla=p,form=form,aplicar=AplicarForm(empresa=request.empresa_activa,plantilla=p),
        rol=s.autorizar(request.user,request.empresa_activa).rol,validada=s.plantilla_validada(p),huella=s.huella_plantilla(p),
        pendientes=s.pendientes_protocolo(p)),status=400 if request.method=='POST' else 200)


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def expediente(request,pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    preliminar_op=request.method=='POST' and request.POST.get('op')=='preliminar'
    form=InicioForm(None if preliminar_op else (request.POST or None),exp=exp)
    prelim=PreliminarForm(request.POST if preliminar_op else None)
    if request.method=='POST' and preliminar_op:
        if prelim.is_valid():
            data=dict(prelim.cleaned_data);clave=data.pop('clave')
            try:
                s.guardar_preliminar(usuario=request.user,empresa=request.empresa_activa,expediente_id=pk,clave=clave,datos=data)
            except (ValidationError,IntegrityError,OSError) as exc: _error(prelim,exc)
            else:
                from django.contrib import messages
                messages.success(request,'Datos preliminares guardados. No declaran conformidad ni generan un informe aprobado.')
                return redirect(request.get_full_path())
    elif request.method=='POST' and form.is_valid():
        data=dict(form.cleaned_data);data['plantilla_id']=data['plantilla_id'].pk
        try:
            c=s.iniciar(usuario=request.user,empresa=request.empresa_activa,expediente_id=pk,**data)
        except (ValidationError,IntegrityError,OSError) as exc: _error(form,exc)
        else:
            url=reverse('masiscam:calibracion_revision',args=[c.pk])
            return redirect(url+'?flujo=1' if request.GET.get('flujo') == '1' else url)
    protocolo,faltan=s.protocolo_estado(exp);tiene=exp.calibraciones.exists()
    rol=s.autorizar(request.user,request.empresa_activa).rol
    return render(request,'masiscam/calibracion_expediente.html',dict(expediente=exp,form=form,preliminar=prelim,rol=rol,
        estado_reparacion=s.o.estado(exp),plantillas_disponibles=form.fields['plantilla_id'].queryset.exists(),
        protocolo_valido=protocolo,requisitos_faltantes=faltan,preliminares=s.preliminares(exp),
        tiene_calibracion=tiene,puede_iniciar=(rol=='TALLER' and not tiene) or (rol=='ADMIN' and tiene)),status=400 if request.method=='POST' else 200)


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def revision(request,pk):
    c=get_object_or_404(Calibracion,pk=pk)
    exp=s.expediente(request.user,request.empresa_activa,c.expediente_id)
    role=s.autorizar(request.user,request.empresa_activa,exp=exp).rol
    op=request.POST.get('op','datos')
    formato_post=op=='formato' and request.method=='POST'
    forms=dict(formato=FormatoCalibracionForm(request.POST if formato_post else None,request.FILES if formato_post else None,calibracion=c),
        datos=DatosForm(request.POST if op=='datos' and request.method=='POST' else None,calibracion=c),
        medir=MedicionForm(request.POST if op=='medir' and request.method=='POST' else None,calibracion=c),
        evidencia=EvidenciaForm(request.POST if op=='evidencia' and request.method=='POST' else None,request.FILES or None,initial={'version':c.version_edicion}),
        decision=DecisionForm(request.POST if op=='decision' and request.method=='POST' else None,calibracion=c),
        informe=InformeForm(request.POST if op=='informe' and request.method=='POST' else None))
    from .tecnica_forms import BaseForm
    forms['enviar']=BaseForm(request.POST if op=='enviar' and request.method=='POST' else None)
    form=forms.get(op)
    if request.method=='POST' and form and form.is_valid():
        data=dict(form.cleaned_data);common=dict(usuario=request.user,empresa=request.empresa_activa,calibracion_id=c.pk)
        try:
            if op=='formato':
                from django.core.files.uploadedfile import UploadedFile
                params={code:data.pop(name) for name,code in form.parametros}
                puntos={code:data.pop(name) for name,code in form.puntos}
                clave=data.pop('clave');version=data.pop('version')
                adjunto=dict(clase=data.pop('evidencia_clase') or 'FOTO',leyenda=data.pop('evidencia_leyenda'),archivo=data.pop('evidencia_archivo'))
                s.guardar_formato(**common,clave=clave,version=version,datos=dict(**data,parametros=params),mediciones=puntos,adjunto=adjunto)
                if request.POST.get('accion')=='informe':   # guardar + enviar a la revisión independiente + reservar el informe
                    try:
                        c.refresh_from_db()
                        if not c.enviado_en:
                            c=s.enviar(**common,version=c.version_edicion,clave=uuid.uuid5(clave,'enviar'))
                        em=s.reservar_informe(**common,clave=uuid.uuid5(clave,'informe'))
                        from .calibracion_tasks import generar_informe_calibracion
                        try: generar_informe_calibracion.apply_async(args=[em.pk],retry=False)
                        except Exception:
                            from django.contrib import messages
                            messages.warning(request,'Reserva conservada. Cola no disponible; puede reintentar la misma emisión.')
                    except (ValidationError,IntegrityError,OSError,ObjectDoesNotExist) as exc:
                        # Lo escrito ya quedó guardado: se muestra la hoja actualizada y el motivo por el que no se generó el informe.
                        c.refresh_from_db()
                        form=forms['formato']=FormatoCalibracionForm(None,calibracion=c);form.cleaned_data={};_error(form,exc)
                        raise _InformeNoGenerado from None
            elif op=='datos':
                params={code:data.pop(name) for name,code in form.parametros}
                s.guardar(**common,clave=data.pop('clave'),version=data.pop('version'),datos=dict(**data,parametros=params))
            elif op=='medir': s.medir(**common,**data)
            elif op=='evidencia': s.evidencia(**common,**data)
            elif op=='enviar': s.enviar(**common,version=request.POST.get('version'),**data)
            elif op=='decision':
                evaluations={code:data.pop(name) for name,code in form.evaluaciones}
                s.decidir(**common,evaluaciones=evaluations,**data)
            elif op=='informe':
                em=s.reservar_informe(**common,**data)
                from .calibracion_tasks import generar_informe_calibracion
                try: generar_informe_calibracion.apply_async(args=[em.pk],retry=False)
                except Exception:
                    from django.contrib import messages
                    messages.warning(request,'Reserva conservada. Cola no disponible; puede reintentar la misma emisión.')
        except _InformeNoGenerado: pass
        except (ValidationError,IntegrityError,OSError,ObjectDoesNotExist) as exc: _error(form,exc)
        else:
            if request.GET.get('flujo') == '1':
                if op in {'enviar','decision'} or op=='formato' and request.POST.get('accion')=='informe':
                    return redirect('masiscam:flujo_detalle',pk=exp.pk)
                return redirect(reverse('masiscam:calibracion_revision',args=[pk])+'?flujo=1')
            if role == 'TALLER' and op == 'enviar':
                return redirect(reverse('masiscam:taller_equipo', args=[exp.registro.equipo_id]) + f'?expediente={exp.pk}')
            return redirect('masiscam:calibracion_revision',pk=pk)
    snap=s.snapshot(c)
    operativa=True
    try:
        s._dependencias(c)
    except (ValidationError,PermissionDenied,OSError):
        operativa=False
    return render(request,'masiscam/calibracion_revision.html',dict(calibracion=c,expediente=exp,rol=role,forms=forms,
        filas=snap['mediciones'],vigente=s.vigente(c),operativa=operativa,
        editable=operativa and role in s.t.OPERADORES and c.tecnico_id==request.user.pk and not c.enviado_en),status=400 if request.method=='POST' else 200)


@require_GET
@local
@masiscam_access_required
def documento(request,pk,documento_pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    doc=get_object_or_404(Documento.objects.filter(Q(evidencias_calibracion__calibracion__expediente=exp)|Q(informe_calibracion__calibracion__expediente=exp)).distinct(),pk=documento_pk)
    return _archivo(doc,request.empresa_activa)


@require_GET
@local
@masiscam_access_required
def fuente(request,pk):
    return _archivo(_p(request,pk).documento,request.empresa_activa)


def _archivo(doc,empresa):
    try:
        s.t.documento_valido(doc,empresa)
        response=FileResponse(doc.archivo.open('rb'),as_attachment=True,filename=doc.nombre_original,content_type=doc.tipo_mime)
    except (ValidationError,OSError): raise Http404('Copia ausente o no íntegra.') from None
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response

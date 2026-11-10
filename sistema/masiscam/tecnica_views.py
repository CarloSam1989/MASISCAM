from django.urls import reverse
import uuid
from functools import wraps
from django.contrib import messages
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from .access import masiscam_access_required
from .models import *
from . import tecnica_services as svc
from .tecnica_forms import *


def local(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        from django.conf import settings
        if not getattr(settings,'MASISCAM_TECNICA_LOCAL',False):
            raise Http404
        return view(request,*args,**kwargs)
    return wrapped


def _error(form,exc):
    if isinstance(exc,ValidationError):
        if hasattr(exc,'error_dict'):
            for field,errors in exc.error_dict.items():
                form.add_error(field if field in form.fields else None,errors)
        else:
            form.add_error(None,exc)
    else:
        form.add_error(None,'Conflicto o almacenamiento no disponible; no se confirmó la operación. Recargue o reintente.')


def _ids(data):
    return {key:(value.pk if hasattr(value,'pk') else value) for key,value in data.items()}


def _role(request):
    return svc.autorizar_tecnica(request.user,request.empresa_activa).rol


@require_GET
@local
@masiscam_access_required
def tecnica_inicio(request):
    role=_role(request)
    qs=svc.expedientes_operativos(request.user,request.empresa_activa)
    return render(request,'masiscam/tecnica_inicio.html',{'expedientes':qs.distinct(),'rol_tecnico':role})


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def tecnica_expediente(request,pk):
    exp=svc.expediente_autorizado(request.user,request.empresa_activa,pk)
    form=AsignacionForm(request.POST or None,empresa=request.empresa_activa)
    if request.method=='POST' and form.is_valid():
        try:
            svc.asignar_tecnico(usuario=request.user,empresa=request.empresa_activa,expediente_id=pk,**_ids(form.cleaned_data))
        except (ValidationError,IntegrityError,OSError) as exc:
            _error(form,exc)
        else:
            return redirect('masiscam:tecnica_expediente',pk=pk)
    etapas=list(exp.etapas_tecnicas.all())
    for etapa in etapas:
        etapa.fuente_vigente=svc.vigente(etapa.inspeccion or etapa.diagnostico)
    return render(request,'masiscam/tecnica_expediente.html',dict(expediente=exp,asignacion_form=form,
        asignacion=exp.asignaciones_tecnicas.filter(fin__isnull=True).first(),etapas=etapas,
        inspecciones=Inspeccion.objects.filter(expediente=exp).order_by('-revision'),diagnosticos=Diagnostico.objects.filter(expediente=exp).order_by('-revision'),
        rol_tecnico=_role(request),clave=uuid.uuid4()),status=400 if request.method=='POST' else 200)


@require_POST
@local
@masiscam_access_required
def tecnica_nueva_revision(request,pk,tipo):
    form=BaseForm(request.POST)
    exp=svc.expediente_autorizado(request.user,request.empresa_activa,pk)
    if form.is_valid():
        try:
            obj=svc.crear_revision(usuario=request.user,empresa=request.empresa_activa,expediente_id=pk,tipo=tipo,
                clave=form.cleaned_data['clave'],motivo=request.POST.get('motivo','')[:20000])
        except (ValidationError,IntegrityError,OSError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'No se pudo crear la revisión.')
        else:
            return redirect('masiscam:tecnica_revision',tipo=tipo,pk=obj.pk)
    else:
        messages.error(request,'Clave de solicitud inválida.')
    return redirect('masiscam:tecnica_expediente',pk=exp.pk)


def _revision(request,tipo,pk):
    model=Inspeccion if tipo=='INSPECCION' else Diagnostico if tipo=='DIAGNOSTICO' else None
    if not model:
        raise Http404
    obj=get_object_or_404(model,pk=pk)
    svc.expediente_autorizado(request.user,request.empresa_activa,obj.expediente_id)
    return obj


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def tecnica_revision(request,tipo,pk):
    obj=_revision(request,tipo,pk)
    exp=obj.expediente
    forms={'guardar':RevisionForm(tipo=tipo,revision=obj),
        'evidencia':EvidenciaForm(expediente=exp,revision=obj,usuario=request.user,empresa=request.empresa_activa),'decision':DecisionForm()}
    if tipo=='DIAGNOSTICO':
        forms['pieza']=PiezaForm(usuario=request.user,empresa=request.empresa_activa,expediente=exp,revision=obj)
    if request.method=='POST':
        action=request.POST.get('op')
        if action=='guardar':
            form=RevisionForm(request.POST,tipo=tipo,revision=obj)
        elif action=='evidencia':
            form=EvidenciaForm(request.POST,request.FILES,expediente=exp,revision=obj,usuario=request.user,empresa=request.empresa_activa)
        elif action=='pieza' and tipo=='DIAGNOSTICO':
            form=PiezaForm(request.POST,usuario=request.user,empresa=request.empresa_activa,expediente=exp,revision=obj)
        elif action=='decision' and tipo=='DIAGNOSTICO':
            form=DecisionForm(request.POST)
        else:
            form=BaseForm(request.POST)
        forms[action]=form
        if form.is_valid():
            data=_ids(form.cleaned_data.copy())
            key=data.pop('clave')
            version=data.pop('version',request.POST.get('version'))
            base=dict(usuario=request.user,empresa=request.empresa_activa,clave=key)
            try:
                if action=='guardar':
                    svc.guardar_revision(**base,tipo=tipo,revision_id=pk,version=version,datos=data)
                elif action=='enviar':
                    svc.enviar_revision(**base,tipo=tipo,revision_id=pk,version=version)
                elif action=='evidencia':
                    svc.agregar_evidencia(**base,tipo=tipo,revision_id=pk,version=version,**data)
                elif action=='pieza' and tipo=='DIAGNOSTICO':
                    svc.agregar_pieza(**base,diagnostico_id=pk,version=version,datos=data)
                elif action=='decision' and tipo=='DIAGNOSTICO':
                    svc.decidir_diagnostico(**base,diagnostico_id=pk,**data)
                elif action in {'quitar_evidencia','quitar_pieza'}:
                    svc.quitar_hijo(**base,tipo=tipo,revision_id=pk,version=version,hijo_id=int(request.POST.get('hijo','0')),pieza=action=='quitar_pieza')
                else:
                    raise ValidationError('Operación técnica inválida.')
            except (ValidationError,IntegrityError,OSError,ValueError,ObjectDoesNotExist) as exc:
                _error(form,exc)
            else:
                if action == 'enviar' and request.rol_masiscam.rol == 'TALLER':
                    return redirect(reverse('masiscam:taller_equipo', args=[exp.registro.equipo_id]) + f'?expediente={exp.pk}')
                return redirect('masiscam:tecnica_revision',tipo=tipo,pk=pk)
    role=_role(request)
    editable=role in svc.OPERADORES and obj.tecnico_id==request.user.pk and not obj.enviado_en and svc.vigente(obj)
    for f in forms['guardar'].fields.values():
        f.disabled=not editable
    return render(request,'masiscam/tecnica_revision.html',dict(revision=obj,tipo=tipo,expediente=exp,
        forms=forms,editable=editable,rol_tecnico=role,vigente=svc.vigente(obj),clave=uuid.uuid4(),
        planos=svc.referencias_visibles(request.user,request.empresa_activa).filter(tipo='PLANO').filter(
            Q(aplicabilidades__equipo=exp.registro.equipo,aplicabilidades__activa=True)|Q(partes__piezarequerida__diagnostico=obj.pk)).distinct() if tipo=='DIAGNOSTICO' else []),status=400 if request.method=='POST' else 200)


@require_POST
@local
@masiscam_access_required
def tecnica_informe(request,pk):
    obj=_revision(request,'DIAGNOSTICO',pk)
    form=BaseForm(request.POST)
    if form.is_valid():
        try:
            em=svc.reservar_informe(usuario=request.user,empresa=request.empresa_activa,diagnostico_id=obj.pk,
                clave=form.cleaned_data['clave'],nueva_version=request.POST.get('nueva_version')=='1',motivo=request.POST.get('motivo','')[:20000])
            from .tecnica_tasks import generar_informe_inicial
            from django.db import transaction
            transaction.on_commit(lambda: _encolar_informe(em.pk))
            messages.success(request,'Informe reservado. Solo estará generado cuando el servidor confirme la copia persistente.')
        except (ValidationError,IntegrityError,OSError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'No se pudo reservar el informe.')
    else:
        messages.error(request,'Clave inválida.')
    return redirect('masiscam:tecnica_expediente',pk=obj.expediente_id)


def _encolar_informe(pk):
    from .tecnica_tasks import generar_informe_inicial
    try:
        generar_informe_inicial.apply_async(args=[pk],retry=False)
    except Exception:
        # Reservation persists; reconciliation or explicit retry can queue it.
        pass


@require_GET
@local
@masiscam_access_required
def tecnica_documento(request,pk,documento_pk):
    exp=svc.expediente_autorizado(request.user,request.empresa_activa,pk)
    doc=get_object_or_404(Documento.objects.filter(Q(evidencias_tecnicas__inspeccion__expediente=exp)|
        Q(evidencias_tecnicas__diagnostico__expediente=exp)|Q(informe_tecnico__expediente=exp)).distinct(),pk=documento_pk)
    return _archivo(doc,request.empresa_activa,inline=request.GET.get('ver')=='1')


def _archivo(doc,empresa,inline=False):
    try:
        svc.documento_valido(doc,empresa)
        stream=doc.archivo.open('rb')
    except (ValidationError,OSError):
        from django.http import HttpResponse
        return HttpResponse('Copia persistente no disponible o corrupta; requiere recuperación.',status=503)
    response=FileResponse(stream,as_attachment=not (inline and doc.tipo_mime in {'application/pdf','image/jpeg','image/png','image/webp'}),filename=doc.nombre_original,content_type=doc.tipo_mime)
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    response['X-Frame-Options']='SAMEORIGIN'
    return response


@require_GET
@local
@masiscam_access_required
def biblioteca(request):
    qs=svc.referencias_visibles(request.user,request.empresa_activa)
    for field in ('marca','modelo','ratio','version_fabricante','tipo'):
        value=request.GET.get(field,'').strip()
        if value:
            qs=qs.filter(**{field+'__icontains':value})
    query=request.GET.get('q','').strip()
    if query:
        qs=qs.filter(Q(titulo__icontains=query)|Q(codigo__icontains=query))
    return render(request,'masiscam/tecnica_biblioteca.html',dict(referencias=qs.order_by('codigo','-revision'),rol_tecnico=_role(request)))


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def biblioteca_referencia_form(request,pk=None):
    svc.autorizar_tecnica(request.user,request.empresa_activa,{'PLANTA'})
    obj=get_object_or_404(ReferenciaTecnica,pk=pk,empresa=request.empresa_activa) if pk else None
    initial={f.name:getattr(obj,f.name) for f in obj._meta.fields} if obj else {}
    if obj:
        initial['documento_id']=obj.documento_id
    form=ReferenciaForm(request.POST or None,request.FILES or None,empresa=request.empresa_activa,initial=initial)
    if request.method=='POST' and form.is_valid():
        data=_ids(form.cleaned_data.copy())
        key,file=data.pop('clave'),data.pop('archivo')
        try:
            obj=svc.guardar_referencia(usuario=request.user,empresa=request.empresa_activa,clave=key,datos=data,archivo=file,referencia_id=pk)
        except (ValidationError,IntegrityError,OSError) as exc:
            _error(form,exc)
        else:
            return redirect('masiscam:biblioteca_referencia',pk=obj.pk)
    return render(request,'masiscam/tecnica_form.html',dict(form=form,titulo='Referencia técnica privada'),status=400 if request.method=='POST' else 200)


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def biblioteca_referencia(request,pk):
    ref=get_object_or_404(svc.referencias_visibles(request.user,request.empresa_activa),pk=pk)
    forms={'parte':ParteForm(empresa=request.empresa_activa),'aplicabilidad':AplicabilidadForm(empresa=request.empresa_activa)}
    if request.method=='POST':
        svc.autorizar_tecnica(request.user,request.empresa_activa,{'PLANTA'})
        action=request.POST.get('op')
        form=(ParteForm(request.POST,empresa=request.empresa_activa) if action=='parte' else
              AplicabilidadForm(request.POST,empresa=request.empresa_activa) if action=='aplicabilidad' else BaseForm(request.POST))
        forms[action]=form
        if form.is_valid():
            data=_ids(form.cleaned_data.copy())
            key=data.pop('clave')
            try:
                base=dict(usuario=request.user,empresa=request.empresa_activa,referencia_id=pk,clave=key)
                if action=='parte':
                    svc.guardar_parte(**base,datos=data,parte_id=request.POST.get('parte_id') or None)
                elif action=='quitar_parte':
                    svc.guardar_parte(**base,datos={},parte_id=int(request.POST.get('parte_id','0')),eliminar=True)
                elif action=='aplicabilidad':
                    svc.aplicar_referencia(**base,**data)
                elif action in {'PUBLICADA','RETIRADA'}:
                    svc.estado_referencia(**base,estado=action)
                elif action=='SINCRONIZAR':
                    svc.reintentar_referencia(**base)
                else:
                    raise ValidationError('Operación de biblioteca inválida.')
            except (ValidationError,IntegrityError,OSError,ObjectDoesNotExist,ValueError) as exc:
                _error(form,exc)
            else:
                return redirect('masiscam:biblioteca_referencia',pk=pk)
    return render(request,'masiscam/tecnica_referencia.html',dict(referencia=ref,forms=forms,rol_tecnico=_role(request),clave=uuid.uuid4()),status=400 if request.method=='POST' else 200)


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def biblioteca_componentes(request,pk=None):
    svc.autorizar_tecnica(request.user,request.empresa_activa,{'PLANTA'})
    obj=get_object_or_404(ComponenteTecnico,pk=pk,empresa=request.empresa_activa) if pk else None
    initial={f.name:getattr(obj,f.name) for f in obj._meta.fields} if obj else {}
    form=ComponenteForm(request.POST or None,initial=initial)
    if request.method=='POST' and form.is_valid():
        data=form.cleaned_data.copy()
        key=data.pop('clave')
        try:
            svc.guardar_componente(usuario=request.user,empresa=request.empresa_activa,datos=data,clave=key,componente_id=pk)
        except (ValidationError,IntegrityError) as exc:
            _error(form,exc)
        else:
            return redirect('masiscam:biblioteca_componentes')
    return render(request,'masiscam/tecnica_form.html',dict(form=form,titulo='Catálogo de componentes',componentes=ComponenteTecnico.objects.filter(empresa=request.empresa_activa)),status=400 if request.method=='POST' else 200)


@require_GET
@local
@masiscam_access_required
def biblioteca_documento(request,pk):
    ref=get_object_or_404(svc.referencias_visibles(request.user,request.empresa_activa),pk=pk)
    return _archivo(ref.documento,request.empresa_activa,inline=request.GET.get('ver')=='1')

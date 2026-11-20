from .access import paginar_listado
from django.urls import reverse
import uuid
from functools import wraps
from django.contrib import messages
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models import Q, F, Value, Case, When, IntegerField, OuterRef, Subquery, Exists, Count
from django.db.models.functions import Greatest, Coalesce, Cast
from django.http import FileResponse, Http404, JsonResponse
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
    # Read-only projection: never use the list's badges to authorize an operation.
    def ultimo(model, field, order='-pk'):
        return Subquery(model.objects.filter(expediente_id=OuterRef('pk')).order_by(order).values(field)[:1])
    etapa=EtapaExpediente.objects.filter(expediente_id=OuterRef('pk'),codigo='DIAGNOSTICO')
    asignacion=AsignacionTecnica.objects.filter(expediente_id=OuterRef('pk'),fin__isnull=True)
    qs=qs.select_related('registro__equipo','cliente_contractual').annotate(
        responsable=Subquery(asignacion.order_by('-pk').values('tecnico__username')[:1]),
        diagnostico_estado=Subquery(etapa.values('diagnostico__estado')[:1]),
        diagnostico_id_actual=Subquery(etapa.values('diagnostico_id')[:1]),
        diagnostico_edicion=Subquery(etapa.values('diagnostico__version_edicion')[:1]),
        diagnostico_enviado=Subquery(etapa.values('diagnostico__enviado_en')[:1]),
        inspeccion_estado=ultimo(Inspeccion,'estado','-revision'),
        calibracion_estado=ultimo(Calibracion,'estado','-revision'),
        informe_estado=ultimo(InformeFinal,'estado','-version'),
        cerrado=Exists(CierreExpediente.objects.filter(expediente_id=OuterRef('pk'))),
        propuesta_actual=ultimo(PropuestaComercial,'pk','-version'),
        propuesta_version=ultimo(PropuestaComercial,'version','-version'),
        decision_admin=ultimo(RevisionAdministrativa,'decision'),
        propuesta_revisada=ultimo(RevisionAdministrativa,'propuesta_id'),
        version_revisada=ultimo(RevisionAdministrativa,'propuesta__version'),
        diagnostico_revisado=ultimo(RevisionAdministrativa,'diagnostico_id'),
        edicion_revisada=Cast(ultimo(RevisionAdministrativa,'snapshot__edicion'),IntegerField()),
        reparacion_iniciada=Exists(ActividadReparacion.objects.filter(expediente_id=OuterRef('pk'))),
        ultima_actividad=Greatest(F('creado_en'),ultimo(Auditoria,'creado_en'),
            ultimo(PropuestaComercial,'creado_en'),ultimo(RevisionAdministrativa,'creado_en'),
            ultimo(Inspeccion,'creado_en'),ultimo(Diagnostico,'creado_en'),
            ultimo(Calibracion,'creado_en'),ultimo(InformeFinal,'creado_en'),ultimo(CierreExpediente,'creado_en')),
    ).annotate(
        piezas_total=Coalesce(Subquery(PiezaRequerida.objects.filter(diagnostico_id=OuterRef('diagnostico_id_actual'))
            .order_by().values('diagnostico_id').annotate(total=Count('pk')).values('total')[:1]),Value(0)),
        solicitud_enviada=Exists(SolicitudRepuestos.objects.filter(diagnostico_id=OuterRef('diagnostico_id_actual'))),
        revision_pendiente=Case(When(propuesta_actual__isnull=True,then=Value(0)),
            When(propuesta_actual=F('propuesta_revisada'),diagnostico_id_actual=F('diagnostico_revisado'),
                 diagnostico_edicion=F('edicion_revisada'),then=Value(0)),default=Value(1),output_field=IntegerField()),
    )
    estado=request.GET.get('estado','')
    if estado=='revision':qs=qs.filter(revision_pendiente=1,cerrado=False)
    elif estado=='sin_cotizacion':qs=qs.filter(propuesta_actual__isnull=True,cerrado=False)
    elif estado=='devuelto':qs=qs.filter(decision_admin='DEVUELTO',cerrado=False)
    elif estado=='finalizado':qs=qs.filter(cerrado=True)
    qs=qs.order_by('cerrado','-revision_pendiente','-ultima_actividad','-pk')
    pagina=paginar_listado(request,qs.distinct(),{'numero':'Expediente','cliente_contractual__nombre_comercial':'Cliente',
        'registro__equipo__nombre':'Equipo','registro__equipo__numero_serie':'Serie','responsable':'Responsable',
        'diagnostico_estado':'Estado de diagnóstico','ultima_actividad':'Última actividad'})
    for e in pagina:
        e.mantenimiento='Finalizado' if e.cerrado else ('Informe final' if e.informe_estado else
            'Calibración' if e.calibracion_estado else 'Reparación' if e.reparacion_iniciada else 'Diagnóstico' if e.diagnostico_estado else
            'Inspección' if e.inspeccion_estado else 'Recepción')
        e.proxima_accion=('Consultar informe final' if e.cerrado else
            'TALLER: enviar diagnóstico para revisión' if e.revision_pendiente and not e.diagnostico_enviado else
            'Revisar diagnóstico y cotización' if e.revision_pendiente else
            'Corregir devolución' if e.decision_admin=='DEVUELTO' else 'Revisar requisitos comerciales y operativos' if e.propuesta_actual else
            'Preparar cotización' if e.solicitud_enviada else 'Completar diagnóstico y selección' if e.diagnostico_estado else
            'Completar inspección' if e.inspeccion_estado else 'Completar recepción e iniciar inspección')
    return render(request,'masiscam/tecnica_inicio.html',dict(expedientes=pagina,pagina=pagina,rol_tecnico=role))


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def tecnica_expediente(request,pk):
    exp=svc.expediente_autorizado(request.user,request.empresa_activa,pk)
    if _role(request)=='ADMIN' and request.GET.get('avanzado')!='1':
        return redirect('masiscam:flujo_detalle',pk=pk)
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
                if request.GET.get('flujo') == '1':
                    return redirect('masiscam:flujo_detalle',pk=exp.pk)
                if action == 'enviar' and request.rol_masiscam.rol == 'TALLER':
                    return redirect(reverse('masiscam:taller_equipo', args=[exp.registro.equipo_id]) + f'?expediente={exp.pk}')
                return redirect('masiscam:tecnica_revision',tipo=tipo,pk=pk)
    role=_role(request)
    editable=role in svc.OPERADORES and obj.tecnico_id==request.user.pk and not obj.enviado_en and svc.vigente(obj)
    for f in forms['guardar'].fields.values():
        f.disabled=not editable
    if request.GET.get('flujo') == '1' and tipo == 'INSPECCION':
        from .flujo_views import contexto as contexto_flujo
        error_key=({'guardar':'inspeccion','evidencia':'evidencia_inspeccion'}.get(action,'enviar_inspeccion')
            if request.method=='POST' else 'evidencia_inspeccion')
        return render(request,'masiscam/flujo_detalle.html',
            contexto_flujo(request,exp,{error_key:form}) if request.method=='POST' else contexto_flujo(request,exp),
            status=400 if request.method=='POST' else 200)
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
        response=HttpResponse('El archivo no está disponible o no supera la verificación de integridad. Solicita su recuperación al responsable.',status=503,content_type='text/plain; charset=utf-8')
    else:
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
    from django.db.models import Count
    pagina=paginar_listado(request,qs.annotate(n_partes=Count('partes',distinct=True)).order_by('codigo','-revision'),{'codigo':'Codigo','titulo':'Titulo','marca':'Marca','modelo':'Modelo','estado':'Estado'})
    for r in pagina:
        r.clave_revision=uuid.uuid4()
    return render(request,'masiscam/tecnica_biblioteca.html',dict(referencias=pagina,pagina=pagina,rol_tecnico=_role(request)))


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def biblioteca_referencia_form(request,pk=None):
    svc.autorizar_tecnica(request.user,request.empresa_activa,{'ADMIN','OFICINA'})
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
    if ref.tipo == 'PLANO' and ref.expediente_trabajo_id:
        from .plano_services import decidir_plano_trabajo, revision_plano, corregir_plano_trabajo
        from .flujo_forms import PlanoTrabajoForm
        correccion = _role(request) == 'TALLER'
        form = (PlanoTrabajoForm(request.POST if request.method=='POST' else None,
                                 request.FILES if request.method=='POST' else None) if correccion
                else BaseForm(request.POST if request.method=='POST' else None))
        if request.method == 'POST':
            if not correccion:
                svc.autorizar_tecnica(request.user, request.empresa_activa, {'ADMIN'})
            if form.is_valid():
                try:
                    if correccion:
                        nueva = corregir_plano_trabajo(usuario=request.user, empresa=request.empresa_activa,
                            referencia_id=ref.pk, **form.cleaned_data)
                    else:
                        decidir_plano_trabajo(usuario=request.user, empresa=request.empresa_activa,
                            referencia_id=ref.pk, clave=form.cleaned_data['clave'],
                            revision=request.POST.get('revision'),
                            decision={'aprobar_plano':'APROBADO', 'rechazar_plano':'RECHAZADO'}.get(request.POST.get('op')),
                            motivo=request.POST.get('motivo', ''))
                except (ValidationError, IntegrityError, OSError) as exc:
                    _error(form, exc)
                else:
                    messages.success(request, 'Corrección guardada como nueva versión pendiente de revisión.' if correccion
                        else 'Plano aprobado y aplicable al equipo.' if request.POST['op']=='aprobar_plano'
                        else 'Plano devuelto a TALLER para corrección.')
                    return redirect('masiscam:biblioteca_referencia', pk=nueva.pk if correccion else ref.pk)
        superada = ReferenciaTecnica.objects.filter(empresa=ref.empresa, codigo=ref.codigo, revision__gt=ref.revision).exists()
        return render(request, 'masiscam/tecnica_plano_revision.html', dict(referencia=ref,
            form=form, clave=uuid.uuid4(), revision_plano=revision_plano(ref), version_superada=superada,
            rol_tecnico=_role(request)), status=400 if request.method=='POST' else 200)
    forms={'parte':ParteForm(empresa=request.empresa_activa),'aplicabilidad':AplicabilidadForm(empresa=request.empresa_activa)}
    if request.method=='POST':
        action=request.POST.get('op')
        svc.autorizar_tecnica(request.user,request.empresa_activa,{'ADMIN', 'OFICINA'} if action in {'parte','quitar_parte','PUBLICADA','revision'} else {'ADMIN'})
        datos_post=request.POST
        if action=='parte' and request.POST.get('campos') and request.POST.get('parte_id','').isdecimal():
            # Guardado parcial: solo se aplican los campos modificados; el resto conserva el valor vigente
            # (otra pestaña o usuario pudo haberlo cambiado después de cargar esta pantalla).
            actual=ref.partes.filter(pk=request.POST['parte_id']).first()
            if actual:
                datos_post=request.POST.copy()
                cambiados=set(request.POST['campos'].split(','))
                for campo in ('posicion','numero_parte','descripcion','unidad','cantidad_referencia','pagina'):
                    if campo not in cambiados:
                        valor=getattr(actual,campo)
                        datos_post[campo]='' if valor is None else str(valor)
        form=(ParteForm(datos_post,empresa=request.empresa_activa) if action=='parte' else
              AplicabilidadForm(request.POST,empresa=request.empresa_activa) if action=='aplicabilidad' else BaseForm(request.POST))
        forms[action]=form
        ajax=request.headers.get('x-requested-with')=='XMLHttpRequest' and action in {'parte','quitar_parte'}
        if ajax and not form.is_valid():
            return JsonResponse({'ok':False,'errores':{c:[str(e) for e in errs] for c,errs in form.errors.items() if c!='clave'}},status=400)
        if form.is_valid():
            data=_ids(form.cleaned_data.copy())
            key=data.pop('clave')
            try:
                base=dict(usuario=request.user,empresa=request.empresa_activa,referencia_id=pk,clave=key)
                if action=='parte':
                    parte_guardada=svc.guardar_parte(**base,datos=data,parte_id=request.POST.get('parte_id') or None)
                elif action=='revision':
                    from .plano_services import crear_revision_catalogo
                    nueva=crear_revision_catalogo(**base)
                    messages.success(request,'Catálogo en edición: modifique los datos o las piezas y vuelva a activarlo.')
                    return redirect('masiscam:biblioteca_referencia',pk=nueva.pk)
                elif action=='quitar_parte':
                    svc.guardar_parte(**base,datos={},parte_id=int(request.POST.get('parte_id','0')),eliminar=True)
                elif action=='aplicabilidad':
                    svc.aplicar_referencia(**base,**data)
                elif action in {'PUBLICADA','RETIRADA'}:
                    svc.estado_referencia(**base,estado=action)
                    messages.success(request,'Catálogo activo.' if action=='PUBLICADA' else 'Catálogo inactivo.')
                elif action=='SINCRONIZAR':
                    svc.reintentar_referencia(**base)
                else:
                    raise ValidationError('Operación de biblioteca inválida.')
            except (ValidationError,IntegrityError,OSError,ObjectDoesNotExist,ValueError) as exc:
                _error(form,exc)
                if ajax:
                    return JsonResponse({'ok':False,'errores':{c:[str(e) for e in errs] for c,errs in form.errors.items() if c!='clave'}},status=400)
            else:
                if ajax:
                    vigente=ref.partes.filter(pk=parte_guardada).first() if action=='parte' else None
                    return JsonResponse({'ok':True,'parte_id':parte_guardada if action=='parte' else None,
                        'valores':{c:('' if getattr(vigente,c) is None else str(getattr(vigente,c))) for c in
                                   ('posicion','numero_parte','descripcion','unidad','cantidad_referencia','pagina')} if vigente else None})
                return redirect('masiscam:biblioteca_referencia',pk=pk)
    editable=ref.estado=='BORRADOR' and ref.tipo=='PLANO' and _role(request) in {'ADMIN','OFICINA'}
    historial=[]
    if _role(request)=='ADMIN':
        from .models import Auditoria
        codigo_revs=list(ReferenciaTecnica.objects.filter(empresa=request.empresa_activa,codigo=ref.codigo).values_list('pk',flat=True))
        historial=list(Auditoria.objects.filter(empresa=request.empresa_activa,objeto_tipo='BibliotecaTecnica',
            objeto_id__in=[str(i) for i in codigo_revs]).select_related('usuario').order_by('-pk')[:100])
        historial+=list(Auditoria.objects.filter(empresa=request.empresa_activa,accion='BIBLIOTECA_PARTE_CAMBIO',
            detalle__referencia__in=codigo_revs).select_related('usuario').order_by('-pk')[:200])
        historial.sort(key=lambda e:e.pk,reverse=True)
    filas=[(p,uuid.uuid4()) for p in ref.partes.order_by('pk')] if editable else []
    return render(request,'masiscam/tecnica_referencia.html',dict(referencia=ref,forms=forms,rol_tecnico=_role(request),clave=uuid.uuid4(),
        editable_partes=editable,filas_partes=filas,historial=historial,revisiones=ReferenciaTecnica.objects.filter(empresa=request.empresa_activa,codigo=ref.codigo).order_by('-revision')),status=400 if request.method=='POST' else 200)


@require_http_methods(['GET','POST'])
@local
@masiscam_access_required
def biblioteca_componentes(request,pk=None):
    svc.autorizar_tecnica(request.user,request.empresa_activa,{'ADMIN'})
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
    return render(request,'masiscam/tecnica_form.html',dict(form=form,titulo='Catálogo de componentes',componentes=(pagina:=paginar_listado(request,ComponenteTecnico.objects.filter(empresa=request.empresa_activa))),pagina=pagina),status=400 if request.method=='POST' else 200)


@require_GET
@local
@masiscam_access_required
def biblioteca_documento(request,pk):
    ref=get_object_or_404(svc.referencias_visibles(request.user,request.empresa_activa),pk=pk)
    return _archivo(ref.documento,request.empresa_activa,inline=request.GET.get('ver')=='1')


@require_GET
@local
@masiscam_access_required
def catalogo_vista_previa(request):
    """Catálogos activos de la empresa con la configuración indicada: PDF, piezas y marcadores disponibles."""
    svc.autorizar_tecnica(request.user,request.empresa_activa,{'TALLER','ADMIN','OFICINA'})
    datos={c:request.GET.get(c,'').strip()[:120] for c in ('marca','modelo','ratio','rotacion','operacion')}
    if not all(datos[c] for c in ('marca','modelo','ratio','rotacion')):
        return JsonResponse({'candidatos':[]})
    candidatos=[]
    for r in svc.catalogos_activos_compatibles(request.empresa_activa,**datos)[:10]:
        mapa=r.mapas.order_by('-version').first()
        candidatos.append(dict(pk=r.pk,codigo=r.codigo,revision=r.revision,titulo=r.titulo,piezas=r.partes.count(),
            marcadores=mapa.posiciones.count() if mapa else 0,
            pdf=reverse('masiscam:catalogo_documento',args=[r.pk])+'?ver=1'))
    return JsonResponse({'candidatos':candidatos})


@require_GET
@local
@masiscam_access_required
def catalogo_documento(request,pk):
    """PDF original de un catálogo activo, para la vista previa al registrar un reductor."""
    svc.autorizar_tecnica(request.user,request.empresa_activa,{'TALLER','ADMIN','OFICINA'})
    ref=get_object_or_404(ReferenciaTecnica,pk=pk,empresa=request.empresa_activa,tipo='PLANO',estado='PUBLICADA',expediente_trabajo__isnull=True)
    return _archivo(ref.documento,request.empresa_activa,inline=True)

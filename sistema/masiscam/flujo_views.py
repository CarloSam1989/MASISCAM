from .access import paginar_listado
import uuid
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError
from django.db.models import Count, Q, Case, When, Value, IntegerField, Exists, OuterRef
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET,require_http_methods
from .access import masiscam_access_required
from . import flujo_services as s, tecnica_services as t, operacion_services as o
from .flujo_forms import CotizacionForm,AdquisicionForm,EnvioForm,ConfirmarEnvioForm,PiezaManualForm
from .flujo_forms import PlanoTrabajoForm, EnviarPiezasForm, AplicarPlanoExistenteForm
from .tecnica_forms import BaseForm,RevisionForm,EvidenciaForm
from .tecnica_views import _error,_archivo
from .operacion_forms import OperacionForm
from .models import (SolicitudRepuestos,EnvioRepuestos,Documento,ExpedienteMantenimiento,
    ReferenciaTecnica, AplicabilidadReferenciaEquipo)


def planos_aplicables(request, exp):
    visibles = t.referencias_visibles(request.user, request.empresa_activa)
    aplicabilidad = AplicabilidadReferenciaEquipo.objects.filter(
        referencia_id=OuterRef('pk'), equipo_id=exp.registro.equipo_id, activa=True)
    return ReferenciaTecnica.objects.filter(empresa=exp.empresa, empresa_id=request.empresa_activa.pk,
        tipo='PLANO', pk__in=visibles.values('pk')).annotate(
            aplicable=Exists(aplicabilidad),
            prioridad=Case(When(estado='PUBLICADA', then=Value(0)), default=Value(1), output_field=IntegerField()))\
        .filter(Q(estado='PUBLICADA', aplicable=True) | Q(estado='BORRADOR', expediente_trabajo=exp))\
        .annotate(partes_count=Count('partes', distinct=True)).order_by('prioridad', '-revision', 'codigo', '-pk')


@require_GET
@masiscam_access_required
def bandeja(request):
    role=t.autorizar_tecnica(request.user,request.empresa_activa,{'OFICINA', 'ADMIN'}).rol
    from django.core.paginator import Paginator
    qs=SolicitudRepuestos.objects.filter(expediente__in=ExpedienteMantenimiento.objects.filter(empresa=request.empresa_activa)).select_related(
        'expediente__cliente_contractual','expediente__registro__equipo','autor','diagnostico').order_by('-creado_en')
    qs=qs.exclude(expediente__registro__equipo__estado='INACTIVO')
    buscar=request.GET.get('q','').strip()[:120]
    pagina=paginar_listado(request,qs,{'expediente__numero':'Expediente','expediente__cliente_contractual__ruc':'RUC','expediente__cliente_contractual__nombre_comercial':'Cliente','expediente__registro__equipo__nombre':'Equipo','expediente__registro__equipo__numero_serie':'Serie'})
    for obj in pagina:
        obj.estado_operativo='Diagnóstico pendiente de ADMINISTRADOR' if not t._aprobado(obj.diagnostico) else 'En gestión de oficina'
        if obj.envios.exists(): obj.estado_operativo='Repuestos enviados' if obj.envios.filter(confirmacion__isnull=True).exists() else 'Recibidos en taller'
    return render(request,'masiscam/flujo_bandeja.html',dict(pagina=pagina,buscar=buscar,rol=role))


def contexto(request,exp,errores=None):
    from django.conf import settings
    context=s.resumen(exp,request.user)
    if context['rol']=='OFICINA':
        comercial=context['propuesta'] if context['solicitud'] and context['propuesta'] and context['propuesta'].diagnostico_id==context['solicitud'].diagnostico_id else None
        context['pasos']=[dict(nombre=n,estado='Pendiente') for n in
            ('Solicitud de taller','Cotización','Aceptación del cliente','Adquisición y envío','Entrega a taller')]
        context['paso']=1
        if context['solicitud']: context['pasos'][0]['estado']='Listo';context['paso']=2
        if comercial: context['pasos'][1]['estado']='Listo';context['paso']=3
        if comercial and hasattr(comercial,'decision'):
            context['pasos'][2]['estado']=comercial.decision.get_decision_display()
            if comercial.decision.decision=='APROBADO': context['paso']=4
        if context['solicitud'] and context['solicitud'].envios.exists():
            context['pasos'][3]['estado']='Enviado';context['paso']=5
        context['pasos'][context['paso']-1]['estado']='En proceso'
        if comercial and hasattr(comercial,'decision') and comercial.decision.decision=='RECHAZADO':
            context['pasos'][2]['estado']='Rechazado'
        if context['solicitud'] and context['solicitud'].envios.exists() and not context['solicitud'].envios.filter(confirmacion__isnull=True).exists():
            context['pasos'][4]['estado']='Listo'
    context['historial_disponible']=all(getattr(settings,'MASISCAM_'+fase+'_LOCAL',False)
        for fase in ('OPERACION','CALIBRACION','CIERRE'))
    historica=False
    if request.GET.get('solicitud'):
        raw=request.GET['solicitud']
        if not raw.isascii() or not raw.isdecimal() or len(raw)>18: raise Http404
        seleccion=get_object_or_404(SolicitudRepuestos,pk=raw,expediente=exp)
        if seleccion!=context['solicitud']:
            historica=True
            context.update(solicitud=seleccion,diagnostico=seleccion.diagnostico,accion=None,
                mensaje='Solicitud histórica congelada. Consulta de piezas y entregas; no admite modificaciones.')
    diag=context['diagnostico'];sol=context['solicitud'];prop=context['propuesta']
    forms={}
    if context['rol']=='ADMIN':
        forms['aplicar_plano']=AplicarPlanoExistenteForm(empresa=request.empresa_activa, usuario=request.user)
    if not historica and context['rol']=='TALLER' and diag and not diag.enviado_en and t.vigente(diag):
        forms['diagnostico']=RevisionForm(tipo='DIAGNOSTICO',revision=diag)
        if not sol:
            forms['enviar_piezas']=EnviarPiezasForm(initial={'version':diag.version_edicion})
    if not historica and context['rol']=='TALLER' and not sol:
        forms['plano_trabajo']=PlanoTrabajoForm()
    ins=context['inspeccion']
    if not historica and context['rol']=='TALLER' and ins and not ins.enviado_en and t.vigente(ins):
        forms['inspeccion']=RevisionForm(tipo='INSPECCION',revision=ins)
        forms['evidencia_inspeccion']=EvidenciaForm(expediente=exp,revision=ins,
            usuario=request.user,empresa=request.empresa_activa)
    if not historica and sol and context['rol']=='OFICINA':
        forms['cotizar']=CotizacionForm(solicitud=sol,propuesta=prop)
        try:
            s.autorizacion(sol)
        except ValidationError:
            pass
        else:
            if sol.snapshot['piezas']:
                forms['adquirir']=AdquisicionForm(solicitud=sol)
                forms['enviar']=EnvioForm(solicitud=sol)
        if prop and prop.diagnostico_id==sol.diagnostico_id and not hasattr(prop,'decision') and o._propuesta_vigente(prop):
            forms['aceptacion']=OperacionForm(op='decision',exp=exp,politica=o.politica())
            forms['aceptacion'].fields['propuesta_id'].queryset=o.PropuestaComercial.objects.filter(pk=prop.pk)
            if not prop.evidencias.exists():
                forms['respaldo']=OperacionForm(op='respaldo',exp=exp,politica=o.politica())
                forms['respaldo'].fields['propuesta_id'].queryset=o.PropuestaComercial.objects.filter(pk=prop.pk)
            from django.forms import HiddenInput
            for nombre in ('aceptacion','respaldo'):
                if nombre not in forms: continue
                forms[nombre].initial['propuesta_id']=prop.pk
                forms[nombre].fields['propuesta_id'].widget=HiddenInput()
    envio=None
    if not historica and sol and context['rol']=='TALLER':
        envio=sol.envios.filter(confirmacion__isnull=True).order_by('pk').first()
        if envio: forms['recibir']=ConfirmarEnvioForm(envio=envio)
    if errores: forms.update(errores)
    for nombre,form in forms.items(): form.auto_id=f'flujo_{nombre}_%s'
    form_action=context['accion']['op'] if context['accion'] else None
    planos=list(planos_aplicables(request, exp))
    if context['rol']=='TALLER' and not sol:
        if ins:
            context['accion']=None
            context['mensaje']='Selecciona las piezas directamente en el visor; no necesitas confirmación de recepción ni autorización comercial.'
        else:
            context['mensaje']='Inicia la inspección o selecciona piezas desde el plano. No necesitas confirmación de recepción ni autorización comercial.'
    elif context['rol']=='ADMIN' and ins and not ins.enviado_en and diag and not diag.enviado_en:
        context['accion']=None
        context['mensaje']='TALLER registra las piezas en el visor. ADMINISTRADOR aprueba o rechaza el plano.'
    versiones_plano = {}
    for plano in planos:
        if plano.codigo not in versiones_plano or plano.revision > versiones_plano[plano.codigo].revision:
            versiones_plano[plano.codigo] = plano
    pendientes_plano = [p for p in versiones_plano.values() if p.expediente_trabajo_id and p.estado == 'BORRADOR']
    if context['rol'] == 'ADMIN' and pendientes_plano:
        pendiente = pendientes_plano[0]
        context['accion'] = dict(texto='Revisar plano',
            url=reverse('masiscam:biblioteca_referencia', args=[pendiente.pk]), op=None)
        context['mensaje'] = 'Visualiza el PDF y aprueba o rechaza su uso para este equipo. No se exige un despiece completo.'
    from .models import Auditoria
    decisiones = Auditoria.objects.filter(empresa=exp.empresa, expediente=exp,
        accion='PLANO_REVISADO', objeto_tipo='ReferenciaTecnica').order_by('pk')
    rechazos = {}
    for decision in decisiones:
        rechazos[decision.objeto_id] = decision.detalle
    for plano in planos:
        ultima = rechazos.get(str(plano.pk), {})
        plano.motivo_rechazo = ultima.get('motivo') if ultima.get('decision') == 'RECHAZADO' else None
    oficina_estado='Pendiente'; oficina_bloqueo=''; oficina_envio=False
    if context['rol']=='OFICINA' and sol:
        if prop and prop.diagnostico_id==sol.diagnostico_id: oficina_estado='Cotizado'
        try:
            auth=s.autorizacion(sol)
        except ValidationError as error:
            oficina_bloqueo='; '.join(error.messages)
        else:
            oficina_estado='Autorizado'
            if sol.envios.filter(lineas__adquisicion__autorizacion=auth).exists(): oficina_estado='Enviado a TALLER'
            oficina_envio=any(s.disponible(a)>0 for a in sol.adquisiciones.filter(autorizacion=auth))
            if not oficina_envio:
                oficina_bloqueo=('Registre repuestos disponibles para preparar un envío; las salidas existentes se conservan.'
                    if sol.snapshot['piezas'] else 'Solicitud sin repuestos: no requiere adquisiciones ni envíos; se mantienen los requisitos técnicos antes de reparar.')
    documentos_comerciales=[]
    if context['rol'] in {'OFICINA', 'ADMIN'}:
        for version in o.PropuestaComercial.objects.filter(expediente=exp).prefetch_related('evidencias__documento').order_by('-version'):
            documentos_comerciales.append(dict(id=version.pk,version=version.version,referencia=version.snapshot.get('presupuesto_referencia',''),
                actual=bool(prop and version.pk==prop.pk), fecha=version.creado_en,
                total=version.snapshot.get('cotizacion',{}).get('total',''),
                pdf=next((ev for ev in version.evidencias.all() if ev.clave==uuid.uuid5(version.clave,'cotizacion-pdf-v1')),
                    next((ev for ev in version.evidencias.all() if ev.documento.tipo_mime=='application/pdf'),None)),
                generar=version.snapshot.get('cotizacion',{}).get('moneda')=='USD' and not version.snapshot.get('propuesta_origen') and not any(ev.clave==uuid.uuid5(version.clave,'cotizacion-pdf-v1') for ev in version.evidencias.all()),
                documentos=[ev for ev in version.evidencias.all() if ev.documento.tipo_mime=='application/pdf']))
    from .models import RevisionAdministrativa
    revision_administrativa=RevisionAdministrativa.objects.filter(expediente=exp).select_related('autor','propuesta','diagnostico').order_by('-pk').first()
    revision_vigente=bool(prop and s.aprobacion_conjunta_vigente(prop) and revision_administrativa and revision_administrativa.decision=='APROBADO')
    if context['rol']=='OFICINA' and prop and prop.snapshot.get('revision_conjunta_requerida') and not revision_vigente:
        oficina_estado='Pendiente de aprobación'
        context['pasos'][2]=dict(nombre='Revisión de ADMINISTRADOR',estado='Pendiente')
        context['paso']=3
    if context['rol']=='ADMIN':
        pdfs=list(prop.evidencias.filter(documento__tipo_mime='application/pdf').select_related('documento')) if prop else []
        faltantes=[]
        if not diag or not t.vigente(diag) or not diag.enviado_en:
            faltantes.append('TALLER debe completar y enviar el diagnóstico vigente para revisión.')
        if not prop:
            faltantes.append('OFICINA debe presentar la cotización vigente para revisión.')
        else:
            if diag and prop.diagnostico_id != diag.pk:
                faltantes.append('OFICINA debe presentar una cotización vinculada al diagnóstico vigente.')
            if not pdfs:
                faltantes.append('OFICINA debe adjuntar el PDF de la cotización vigente.')
            if not prop.snapshot.get('presupuesto_referencia'):
                faltantes.append('OFICINA debe registrar el número de cotización.')
            for ev in pdfs:
                try:
                    t.documento_valido(ev.documento, exp.empresa)
                except (ValidationError, PermissionDenied):
                    faltantes.append('OFICINA debe corregir el PDF de cotización: no está disponible o válido.')
                    break
        if diag and prop and (request.user.pk in t._autores(diag) or request.user.pk == prop.autor_id):
            faltantes.append('Otro ADMINISTRADOR debe revisar el expediente: no puede revisar su propio trabajo.')
        if revision_vigente:
            faltantes.append('ADMINISTRADOR ya aprobó conjuntamente las versiones vigentes.')
        context.update(revision_faltantes=faltantes,
            cotizacion_pdf=next((ev for ev in pdfs if ev.clave==uuid.uuid5(prop.clave,'cotizacion-pdf-v1')),pdfs[0] if pdfs else None),
            diagnostico_datos=[(diag._meta.get_field(name).verbose_name,getattr(diag,name)) for name in t.DIA_FIELDS if getattr(diag,name)] if diag else [],
            evidencias_diagnostico=[ev for ev in t._evidencias(diag) if not ev.pieza_id] if diag else [])
    cotizacion_bloqueo=('La cotización con repuestos registrados conserva sus importes y autorización; los cambios requieren una revisión de alcance.'
        if sol and sol.adquisiciones.exists() else '')
    context.update(expediente=exp,equipo=exp.registro.equipo,inspeccion=ins,forms=forms,envio=envio,
        oficina_estado=oficina_estado,oficina_bloqueo=oficina_bloqueo,oficina_envio=oficina_envio,
        diagnostico_pendiente=bool(diag and not diag.enviado_en),
        cotizacion_habilitada=not bool(cotizacion_bloqueo),cotizacion_bloqueo=cotizacion_bloqueo,
        documentos_comerciales=documentos_comerciales,
        revision_administrativa=revision_administrativa,revision_vigente=revision_vigente,
        mantenimientos=t.expedientes_operativos(request.user,request.empresa_activa).filter(registro__equipo=exp.registro.equipo).order_by('-correlativo') if context['rol']=='TALLER' else [],
        errores_generales=[str(error) for form in (errores or {}).values() for errors in form.errors.values() for error in errors],
        form_principal=forms.get(form_action),clave=uuid.uuid4(),
        envios=sol.envios.select_related('documento','confirmacion').order_by('-pk') if sol else [],
        piezas=diag.piezas.prefetch_related('fotografias__documento').order_by('pk') if diag and (context['rol']=='TALLER' or diag.enviado_en or sol) else [],
        planos=planos, planos_actuales=list(versiones_plano.values()),
        taller_simple=context['rol']=='TALLER' and not sol and not historica,
        plano_activo=next(iter(planos),None),
        adquisiciones=sol.adquisiciones.select_related('pieza','documento').order_by('pk') if sol else [])
    return context


def plano_aplicable_url(request,exp,diagnostico):
    plano=planos_aplicables(request, exp).first()
    if not plano:
        return None
    return reverse('masiscam:plano',args=[plano.pk])+f'?diagnostico={diagnostico.pk}&flujo=1'


@require_http_methods(['GET','POST'])
@masiscam_access_required
def detalle(request,pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    template='masiscam/flujo_revision_admin.html' if request.rol_masiscam.rol=='ADMIN' else 'masiscam/flujo_detalle.html'
    if request.method=='GET': return render(request,template,contexto(request,exp))
    op=request.POST.get('op','');ctx=s.resumen(exp,request.user);diag=ctx['diagnostico'];sol=ctx['solicitud']
    if request.GET.get('solicitud') and (not sol or request.GET['solicitud']!=str(sol.pk)):
        raise PermissionDenied('La solicitud histórica es de solo lectura.')
    if op=='aplicar_plano':
        t.autorizar_tecnica(request.user, request.empresa_activa, {'ADMIN'}, exp)
        form=AplicarPlanoExistenteForm(request.POST, empresa=request.empresa_activa, usuario=request.user);nombre=op
    elif op=='plano_trabajo':
        form=PlanoTrabajoForm(request.POST,request.FILES);nombre=op
    elif op=='enviar_piezas' and diag:
        form=EnviarPiezasForm(request.POST);nombre=op
    elif op in {'guardar','solicitud'} and diag and (not diag.enviado_en or 'fallas' in request.POST):
        form=RevisionForm(request.POST,tipo='DIAGNOSTICO',revision=diag);nombre='diagnostico'
    elif op=='manual' and diag:
        form=PiezaManualForm(request.POST,request.FILES);nombre='manual'
    elif op=='cotizar' and sol: form=CotizacionForm(request.POST,request.FILES,solicitud=sol,propuesta=ctx['propuesta']);nombre=op
    elif op=='adquirir' and sol: form=AdquisicionForm(request.POST,request.FILES,solicitud=sol);nombre=op
    elif op=='enviar' and sol: form=EnvioForm(request.POST,solicitud=sol);nombre=op
    elif op=='recibir' and sol:
        raw=request.POST.get('envio','')
        if not raw.isascii() or not raw.isdecimal() or len(raw)>18: raise Http404
        envio=get_object_or_404(EnvioRepuestos,pk=raw,solicitud=sol)
        form=ConfirmarEnvioForm(request.POST,envio=envio);nombre=op
    elif op in {'aceptacion','respaldo'} and sol:
        form=OperacionForm(request.POST,request.FILES,op='decision' if op=='aceptacion' else 'respaldo',exp=exp,politica=o.politica());nombre=op
    else: form=BaseForm(request.POST);nombre='inicio_inspeccion' if op=='inspeccion' else op
    if form.is_valid():
        data=dict(form.cleaned_data);key=data.pop('clave');common=dict(usuario=request.user,empresa=request.empresa_activa)
        diagnostico_para_plano=None
        try:
            if op=='aplicar_plano':
                t.aplicar_referencia(**common, referencia_id=data['referencia_id'].pk,
                    equipo_id=exp.registro.equipo_id, fundamento=data['fundamento'], clave=key)
            elif op=='plano_trabajo':
                from .plano_services import cargar_plano_trabajo, corregir_plano_trabajo
                raw=request.POST.get('referencia_id','')
                if raw:
                    from .models import ReferenciaTecnica
                    if not raw.isascii() or not raw.isdecimal() or len(raw)>18: raise Http404
                    ref=get_object_or_404(ReferenciaTecnica,pk=raw,empresa=exp.empresa,expediente_trabajo=exp)
                    ref=corregir_plano_trabajo(**common,referencia_id=ref.pk,clave=key,**data)
                else:
                    ref=cargar_plano_trabajo(**common,expediente_id=exp.pk,clave=key,**data)
                diag=exp.etapas_tecnicas.get(codigo='DIAGNOSTICO').diagnostico
                return redirect(reverse('masiscam:plano',args=[ref.pk])+f'?diagnostico={diag.pk}&flujo=1')
            elif op=='enviar_piezas' and diag:
                s.enviar_solicitud(**common,diagnostico_id=diag.pk,clave=key,preliminar=True,**data)
            elif op=='inspeccion':
                _,diagnostico_para_plano=t.iniciar_inspeccion(**common,expediente_id=exp.pk,clave=key)
            elif op=='diagnostico':
                if ctx['inspeccion'] and not ctx['inspeccion'].enviado_en:
                    diagnostico_para_plano=t.preparar_diagnostico(**common,expediente_id=exp.pk,clave=key)
                else:
                    diagnostico_para_plano=t.crear_revision(**common,expediente_id=exp.pk,tipo='DIAGNOSTICO',clave=key)
            elif op=='guardar' and diag:
                t.guardar_revision(**common,tipo='DIAGNOSTICO',revision_id=diag.pk,version=data.pop('version'),clave=key,datos=data)
            elif op=='solicitud' and diag:
                s.enviar_solicitud(**common,diagnostico_id=diag.pk,version=data.pop('version',request.POST.get('version')),clave=key,datos=data or None)
            elif op=='manual' and diag:
                from .plano_services import guardar_seleccion
                fotos=data.pop('fotografias');version=data.pop('version')
                guardar_seleccion(**common,diagnostico_id=diag.pk,version=version,clave=key,
                    datos=dict(data,origen='MANUAL'),fotografias=fotos)
            elif op=='cotizar' and sol: s.cotizar(**common,solicitud_id=sol.pk,clave=key,**form.datos())
            elif op in {'aprobar_expediente','devolver_expediente'}:
                s.revisar_expediente(**common,expediente_id=exp.pk,clave=key,
                    diagnostico_id=int(request.POST.get('diagnostico_id','0')),propuesta_id=int(request.POST.get('propuesta_id','0')),
                    decision='APROBADO' if op=='aprobar_expediente' else 'DEVUELTO',
                    motivo=request.POST.get('motivo',''),destino=request.POST.get('destino','AMBOS'))
            elif op=='pdf_cotizacion':
                raw=request.POST.get('propuesta_id','')
                if not raw.isascii() or not raw.isdecimal() or len(raw)>18: raise Http404
                propuesta=get_object_or_404(o.PropuestaComercial,pk=raw,expediente=exp)
                s.generar_cotizacion_pdf(**common,propuesta_id=propuesta.pk)
            elif op=='aceptacion' and sol:
                vigente=o.PropuestaComercial.objects.filter(expediente=exp).order_by('-version').first()
                propuesta=data.pop('propuesta_id')
                if not vigente or propuesta.pk!=vigente.pk or propuesta.diagnostico_id!=sol.diagnostico_id: raise PermissionDenied('Propuesta ajena o sustituida.')
                o.decidir_comercial(**common,propuesta_id=propuesta.pk,clave=key,archivo=data.pop('archivo'),archivo_facultad=data.pop('archivo_facultad'),datos=data)
            elif op=='respaldo' and sol:
                propuesta=data.pop('propuesta_id')
                if propuesta.expediente_id!=exp.pk or propuesta.diagnostico_id!=sol.diagnostico_id: raise PermissionDenied('Propuesta ajena.')
                o.respaldo_propuesta(**common,propuesta_id=propuesta.pk,clave=key,**data)
            elif op=='adquirir' and sol:
                data['pieza_id']=data['pieza_id'].pk
                s.adquirir(**common,solicitud_id=sol.pk,clave=key,**data)
            elif op=='enviar' and sol:
                s.enviar_repuestos(**common,solicitud_id=sol.pk,clave=key,numero_guia=data['numero_guia'],fecha=data['fecha'],lineas=form.filas())
            elif op=='recibir' and sol:
                s.confirmar_envio(**common,envio_id=envio.pk,clave=key,fecha=data['fecha'],lineas=form.filas(),observaciones=data['observaciones'])
            elif op=='final':
                s.expediente(request.user,request.empresa_activa,pk,{'TALLER', 'OFICINA'})
                cal=ctx['calibracion']
                if not cal: raise ValidationError('Calibración aprobada pendiente.')
                informe=s.generar_final_automatico(cal.pk,propagar=True)
                if informe: s.evento(exp,request.user,'FINAL_REINTENTADO',informe=informe.pk,calibracion=cal.pk)
            else: raise ValidationError('Acción no disponible en este paso.')
        except (ValidationError,IntegrityError,OSError,ValueError) as error: _error(form,error)
        else:
            if op=='cotizar':
                messages.success(request,'Cotización enviada correctamente. Pendiente de aprobación del ADMINISTRADOR')
                return redirect('masiscam:oficina_inicio')
            messages.success(request,'Avance registrado. El siguiente paso está actualizado.')
            if diagnostico_para_plano:
                url_plano=plano_aplicable_url(request,exp,diagnostico_para_plano)
                if url_plano:
                    return redirect(url_plano)
            return redirect('masiscam:flujo_detalle',pk=exp.pk)
    return render(request,template,contexto(request,exp,{nombre:form}),status=400)


@require_GET
@masiscam_access_required
def documento(request,pk,documento_pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    role=t.autorizar_tecnica(request.user,request.empresa_activa).rol
    ids=set()
    for obj in SolicitudRepuestos.objects.filter(expediente=exp):
        for pieza in obj.snapshot['piezas']:
            ids.update(f['documento'] for f in pieza['fotos'])
    ids.update(EnvioRepuestos.objects.filter(expediente=exp).values_list('documento_id',flat=True))
    if role in {'OFICINA'}:
        ids.update(s.AdquisicionRepuesto.objects.filter(expediente=exp,documento__isnull=False).values_list('documento_id',flat=True))
    if role in {'OFICINA', 'ADMIN'}:
        ids.update(o.PropuestaComercial.objects.filter(expediente=exp).values_list('evidencias__documento_id',flat=True))
    doc=get_object_or_404(Documento,pk=documento_pk,pk__in=ids)
    return _archivo(doc,request.empresa_activa,inline=request.GET.get('ver')=='1')

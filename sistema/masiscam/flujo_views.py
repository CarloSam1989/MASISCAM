import uuid
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET,require_http_methods
from .access import masiscam_access_required
from . import flujo_services as s, tecnica_services as t, operacion_services as o
from .flujo_forms import CotizacionForm,AdquisicionForm,EnvioForm,ConfirmarEnvioForm,PiezaManualForm
from .tecnica_forms import BaseForm,RevisionForm
from .tecnica_views import _error,_archivo
from .operacion_forms import OperacionForm
from .models import SolicitudRepuestos,EnvioRepuestos,Documento


@require_GET
@masiscam_access_required
def bandeja(request):
    role=t.autorizar_tecnica(request.user,request.empresa_activa,{'OFICINA','PLANTA','GERENCIA'}).rol
    from django.core.paginator import Paginator
    qs=SolicitudRepuestos.objects.filter(expediente__empresa=request.empresa_activa).select_related(
        'expediente__cliente_contractual','expediente__registro__equipo','autor','diagnostico').order_by('-creado_en')
    buscar=request.GET.get('q','').strip()[:120]
    if buscar: qs=qs.filter(Q(expediente__numero__icontains=buscar)|Q(expediente__cliente_contractual__ruc__icontains=buscar)|Q(expediente__registro__equipo__numero_serie__icontains=buscar))
    pagina=Paginator(qs,20).get_page(request.GET.get('page'))
    for obj in pagina:
        obj.estado_operativo='Diagnóstico pendiente de PLANTA' if not t._aprobado(obj.diagnostico) else 'En gestión de oficina'
        if obj.envios.exists(): obj.estado_operativo='Repuestos enviados' if obj.envios.filter(confirmacion__isnull=True).exists() else 'Recibidos en taller'
    return render(request,'masiscam/flujo_bandeja.html',dict(pagina=pagina,buscar=buscar,rol=role))


def contexto(request,exp,errores=None):
    context=s.resumen(exp,request.user)
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
    if not historica and context['rol']=='TALLER' and diag and not diag.enviado_en and t.vigente(diag):
        forms['diagnostico']=RevisionForm(tipo='DIAGNOSTICO',revision=diag)
        forms['manual']=PiezaManualForm(initial={'version':diag.version_edicion})
    if not historica and sol and context['rol']=='OFICINA':
        forms['cotizar']=CotizacionForm()
        try:
            s.autorizacion(sol)
        except ValidationError:
            pass
        else:
            if sol.snapshot['piezas']:
                forms['adquirir']=AdquisicionForm(solicitud=sol)
                forms['enviar']=EnvioForm(solicitud=sol)
        if prop and prop.diagnostico_id==sol.diagnostico_id and not hasattr(prop,'decision'):
            forms['aceptacion']=OperacionForm(op='decision',exp=exp,politica=o.politica())
            forms['aceptacion'].fields['propuesta_id'].queryset=o.PropuestaComercial.objects.filter(pk=prop.pk)
            forms['respaldo']=OperacionForm(op='respaldo',exp=exp,politica=o.politica())
            forms['respaldo'].fields['propuesta_id'].queryset=o.PropuestaComercial.objects.filter(pk=prop.pk)
    envio=None
    if not historica and sol and context['rol']=='TALLER':
        envio=sol.envios.filter(confirmacion__isnull=True).order_by('pk').first()
        if envio: forms['recibir']=ConfirmarEnvioForm(envio=envio)
    if errores: forms.update(errores)
    for nombre,form in forms.items(): form.auto_id=f'flujo_{nombre}_%s'
    form_action=context['accion']['op'] if context['accion'] else None
    context.update(expediente=exp,equipo=exp.registro.equipo,forms=forms,envio=envio,
        mantenimientos=t.expedientes_operativos(request.user,request.empresa_activa).filter(registro__equipo=exp.registro.equipo).order_by('-correlativo') if context['rol']=='TALLER' else [],
        errores_generales=[str(error) for form in (errores or {}).values() for errors in form.errors.values() for error in errors],
        form_principal=forms.get(form_action),clave=uuid.uuid4(),
        planos=t.referencias_visibles(request.user,request.empresa_activa).filter(tipo='PLANO',estado='PUBLICADA',
            aplicabilidades__equipo=exp.registro.equipo,aplicabilidades__activa=True).distinct() if diag else [],
        envios=sol.envios.select_related('documento','confirmacion').order_by('-pk') if sol else [],
        piezas=diag.piezas.prefetch_related('fotografias__documento').order_by('pk') if diag and (context['rol']=='TALLER' or diag.enviado_en) else [],
        adquisiciones=sol.adquisiciones.select_related('pieza','documento').order_by('pk') if sol else [])
    return context


@require_http_methods(['GET','POST'])
@masiscam_access_required
def detalle(request,pk):
    exp=s.expediente(request.user,request.empresa_activa,pk)
    if request.method=='GET': return render(request,'masiscam/flujo_detalle.html',contexto(request,exp))
    op=request.POST.get('op','');ctx=s.resumen(exp,request.user);diag=ctx['diagnostico'];sol=ctx['solicitud']
    if request.GET.get('solicitud') and (not sol or request.GET['solicitud']!=str(sol.pk)):
        raise PermissionDenied('La solicitud histórica es de solo lectura.')
    if op in {'guardar','solicitud'} and diag and (not diag.enviado_en or 'fallas' in request.POST):
        form=RevisionForm(request.POST,tipo='DIAGNOSTICO',revision=diag);nombre='diagnostico'
    elif op=='manual' and diag:
        form=PiezaManualForm(request.POST,request.FILES);nombre='manual'
    elif op=='cotizar' and sol: form=CotizacionForm(request.POST,request.FILES);nombre=op
    elif op=='adquirir' and sol: form=AdquisicionForm(request.POST,request.FILES,solicitud=sol);nombre=op
    elif op=='enviar' and sol: form=EnvioForm(request.POST,solicitud=sol);nombre=op
    elif op=='recibir' and sol:
        raw=request.POST.get('envio','')
        if not raw.isascii() or not raw.isdecimal() or len(raw)>18: raise Http404
        envio=get_object_or_404(EnvioRepuestos,pk=raw,solicitud=sol)
        form=ConfirmarEnvioForm(request.POST,envio=envio);nombre=op
    elif op in {'aceptacion','respaldo'} and sol:
        form=OperacionForm(request.POST,request.FILES,op='decision' if op=='aceptacion' else 'respaldo',exp=exp,politica=o.politica());nombre=op
    else: form=BaseForm(request.POST);nombre=op
    if form.is_valid():
        data=dict(form.cleaned_data);key=data.pop('clave');common=dict(usuario=request.user,empresa=request.empresa_activa)
        try:
            if op in {'inspeccion','diagnostico'}:
                t.crear_revision(**common,expediente_id=exp.pk,tipo='INSPECCION' if op=='inspeccion' else 'DIAGNOSTICO',clave=key)
            elif op=='guardar' and diag:
                t.guardar_revision(**common,tipo='DIAGNOSTICO',revision_id=diag.pk,version=data.pop('version'),clave=key,datos=data)
            elif op=='solicitud' and diag:
                s.enviar_solicitud(**common,diagnostico_id=diag.pk,version=data.pop('version',request.POST.get('version')),clave=key,datos=data or None)
            elif op=='manual' and diag:
                from .plano_services import guardar_seleccion
                fotos=data.pop('fotografias');version=data.pop('version')
                guardar_seleccion(**common,diagnostico_id=diag.pk,version=version,clave=key,
                    datos=dict(data,origen='MANUAL'),fotografias=fotos)
            elif op=='cotizar' and sol: s.cotizar(**common,solicitud_id=sol.pk,clave=key,**data)
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
                s.expediente(request.user,request.empresa_activa,pk,{'TALLER','OFICINA'})
                cal=ctx['calibracion']
                if not cal: raise ValidationError('Calibración aprobada pendiente.')
                informe=s.generar_final_automatico(cal.pk,propagar=True)
                if informe: s.evento(exp,request.user,'FINAL_REINTENTADO',informe=informe.pk,calibracion=cal.pk)
            else: raise ValidationError('Acción no disponible en este paso.')
        except (ValidationError,IntegrityError,OSError,ValueError) as error: _error(form,error)
        else:
            messages.success(request,'Avance registrado. El siguiente paso está actualizado.')
            return redirect('masiscam:flujo_detalle',pk=exp.pk)
    return render(request,'masiscam/flujo_detalle.html',contexto(request,exp,{nombre:form}),status=400)


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
    if role in {'OFICINA','GERENCIA'}:
        ids.update(s.AdquisicionRepuesto.objects.filter(expediente=exp,documento__isnull=False).values_list('documento_id',flat=True))
    doc=get_object_or_404(Documento,pk=documento_pk,pk__in=ids)
    return _archivo(doc,request.empresa_activa,inline=request.GET.get('ver')=='1')

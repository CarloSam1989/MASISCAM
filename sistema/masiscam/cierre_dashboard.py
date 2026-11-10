"""Batched, tenant-scoped indicators. No file reads or per-row queries."""
from collections import Counter
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db.models import Exists,OuterRef,Subquery
from django.utils.dateparse import parse_date
from .models import (ExpedienteMantenimiento,ConfirmacionRecepcion,Inspeccion,Diagnostico,Calibracion,
    InformeFinal,CierreExpediente,PropuestaComercial,DecisionComercial,ActividadReparacion,RevisionReparacion,
    RecepcionRepuesto,PiezaRequerida,Auditoria,AsignacionTecnica,ResultadoReparacion)
from . import cierre_services as s


def listado(user,empresa,filters=None):
    role=s.autorizar(user,empresa).rol
    qs=ExpedienteMantenimiento.objects.filter(empresa=empresa,registro__equipo__proyecto__empresa=empresa)
    from .access import AUDITORES_INACTIVOS
    if role not in AUDITORES_INACTIVOS: qs=qs.exclude(registro__equipo__estado='INACTIVO')
    if role=='TALLER':
        from .recepcion_services import expedientes_autorizados
        qs=qs.filter(pk__in=expedientes_autorizados(user,empresa).values('pk'))
    if role in s.t.OPERADORES: qs=qs.filter(asignaciones_tecnicas__tecnico=user,asignaciones_tecnicas__fin__isnull=True)
    filters=filters or {}
    if filters.get('empresa') and str(filters['empresa'])!=str(empresa.pk):
        raise PermissionDenied('El filtro no permite cambiar la empresa activa.')
    for key,path in (('expediente','pk'),('cliente','cliente_contractual_id'),('equipo','registro__equipo_id'),('tecnico','asignaciones_tecnicas__tecnico_id')):
        if filters.get(key):
            try: value=int(filters[key])
            except (ValueError,TypeError): raise ValidationError('Filtro de identificador inválido.') from None
            criteria={path:value}
            if key=='tecnico': criteria['asignaciones_tecnicas__fin__isnull']=True
            qs=qs.filter(**criteria)
    for key,lookup in (('desde','creado_en__date__gte'),('hasta','creado_en__date__lte')):
        if filters.get(key):
            try: date=parse_date(filters[key])
            except ValueError: date=None
            if not date: raise ValidationError('Fecha de filtro inválida.')
            qs=qs.filter(**{lookup:date})
    def latest(model,field,order):
        return Subquery(model.objects.filter(expediente_id=OuterRef('pk')).order_by(order).values(field)[:1])
    qs=qs.annotate(cerrado=Exists(CierreExpediente.objects.filter(expediente_id=OuterRef('pk'))),
        recibido=Exists(ConfirmacionRecepcion.objects.filter(recepcion__expediente_id=OuterRef('pk'))),
        inspeccion_estado=latest(Inspeccion,'estado','-revision'),diagnostico_estado=latest(Diagnostico,'estado','-revision'),
        calibracion_estado=latest(Calibracion,'estado','-revision'),informe_estado=latest(InformeFinal,'estado','-version'),
        propuesta_id=latest(PropuestaComercial,'pk','-version'),
        propuesta_diagnostico=latest(PropuestaComercial,'diagnostico_id','-version'),
        diagnostico_actual=latest(Diagnostico,'pk','-revision'),
        calibracion_diagnostico=latest(Calibracion,'diagnostico_id','-revision'),
        calibracion_reparacion=latest(Calibracion,'reparacion_snapshot','-revision'),
        calibracion_actual=latest(Calibracion,'pk','-revision'),
        informe_calibracion=latest(InformeFinal,'calibracion_id','-version'),
        tiene_actividad=Exists(ActividadReparacion.objects.filter(expediente_id=OuterRef('pk'))),
        tiene_recepcion=Exists(RecepcionRepuesto.objects.filter(expediente_id=OuterRef('pk'))),
        requiere_piezas=Exists(PiezaRequerida.objects.filter(diagnostico__expediente_id=OuterRef('pk'))))
    # Independent subqueries reflect the latest proposal, not an old approval.
    qs=qs.annotate(comercial_estado=Subquery(DecisionComercial.objects.filter(propuesta_id=OuterRef('propuesta_id')).values('decision')[:1]))
    if role == 'TALLER': qs=qs.filter(pk__in=s.t.expedientes_operativos(user,empresa).values('pk'))
    return qs.select_related('registro__equipo','cliente_contractual','empresa').distinct().order_by('-creado_en','-pk')


def etapa(exp):
    if exp.cerrado: return 'FINALIZADO'
    if not exp.recibido: return 'RECEPCION_PENDIENTE'
    if not exp.inspeccion_estado or exp.inspeccion_estado=='EN_PROCESO': return 'INSPECCION'
    if exp.diagnostico_estado not in {'APROBADO','FINALIZADO'}: return 'DIAGNOSTICO_PENDIENTE'
    if exp.comercial_estado!='APROBADO': return 'COMERCIAL_PENDIENTE'
    if exp.propuesta_diagnostico!=exp.diagnostico_actual: return 'COMERCIAL_OBSERVADO'
    if exp.calibracion_estado and exp.calibracion_diagnostico!=exp.diagnostico_actual: return 'CALIBRACION_OBSERVADA'
    if exp.calibracion_estado and getattr(exp,'fuente_reparacion_cambiada',False): return 'CALIBRACION_OBSERVADA'
    if exp.informe_estado and exp.informe_calibracion!=exp.calibracion_actual:
        return 'INFORME_OBSERVADO' if exp.calibracion_estado=='APROBADA' else 'CALIBRACION_'+(exp.calibracion_estado or 'PENDIENTE')
    if exp.informe_estado: return 'INFORME_'+exp.informe_estado
    if exp.calibracion_estado=='APROBADA': return 'INFORME_PENDIENTE'
    if exp.calibracion_estado: return 'CALIBRACION_'+exp.calibracion_estado
    if getattr(exp,'piezas_pendientes',exp.requiere_piezas and not exp.tiene_recepcion): return 'REPUESTOS_PENDIENTES'
    if exp.tiene_actividad: return 'CALIBRACION_PENDIENTE' if getattr(exp,'reparacion_aprobada',False) else 'REPARACION'
    return 'REPARACION_PENDIENTE'


def resumen(user,empresa,filters=None,limite=100):
    qs=listado(user,empresa,filters)
    # Bound the screen and explicitly expose the cap. Counts are computed from
    # the complete scoped projection; related objects are fetched only for page.
    rows=list(qs)
    # A fixed batch checks the last result/review of every current activity.
    acts=ActividadReparacion.objects.filter(expediente_id__in=[r.pk for r in rows]).annotate(
        resultado_actual=Subquery(ResultadoReparacion.objects.filter(actividad_id=OuterRef('pk')).order_by('-version').values('pk')[:1]))
    acts=acts.annotate(revision_actual=Subquery(RevisionReparacion.objects.filter(resultado_id=OuterRef('resultado_actual')).values('decision')[:1]))
    repairs={};sources={}
    for a in acts.order_by('pk').values('pk','expediente_id','autorizacion__propuesta_id','revision_actual','resultado_actual'):
        key=(a['expediente_id'],a['autorizacion__propuesta_id'])
        repairs.setdefault(key,[]).append(a['revision_actual'])
        sources.setdefault(key,[]).append(dict(actividad=a['pk'],resultado=a['resultado_actual']))
    requirements={};received={}
    for p in PiezaRequerida.objects.filter(diagnostico_id__in=[r.diagnostico_actual for r in rows]).values('pk','diagnostico_id','numero_parte','unidad','cantidad'):
        requirements.setdefault(p['diagnostico_id'],[]).append(p)
    for r in RecepcionRepuesto.objects.filter(expediente_id__in=[r.pk for r in rows]).values('pieza_id','numero_parte','unidad','cantidad'):
        key=(r['pieza_id'],r['numero_parte'],r['unidad'])
        received[key]=received.get(key,0)+r['cantidad']
    for exp in rows:
        statuses=repairs.get((exp.pk,exp.propuesta_id),[])
        exp.reparacion_aprobada=bool(statuses) and all(v=='APROBADO' for v in statuses)
        exp.fuente_reparacion_cambiada=bool(exp.calibracion_estado and exp.calibracion_reparacion!=sources.get((exp.pk,exp.propuesta_id),[]))
        exp.piezas_pendientes=any(not p['unidad'] or received.get((p['pk'],p['numero_parte'],p['unidad']),0)<p['cantidad'] for p in requirements.get(exp.diagnostico_actual,[]))
        exp.etapa_actual=etapa(exp)
        exp.alerta_documental=not exp.recibido or exp.informe_estado=='BORRADOR'
    state=(filters or {}).get('estado','')
    if state: rows=[r for r in rows if r.etapa_actual==state]
    counts=Counter(r.etapa_actual for r in rows)
    events=Auditoria.objects.filter(expediente_id__in=[r.pk for r in rows]).select_related('usuario','expediente').order_by('-creado_en','-pk')[:20]
    return dict(expedientes=rows[:limite] if limite else rows,total=len(rows),conteos=sorted(counts.items()),abiertos=sum(not r.cerrado for r in rows),
        finalizados=sum(r.cerrado for r in rows),alertas=sum(r.alerta_documental for r in rows),actividades=events,
        comercial_habilitado=bool(s.o.politica()),protocolos_habilitados=bool(getattr(s.settings,'MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL',[])))

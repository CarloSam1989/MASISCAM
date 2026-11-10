"""Local final review and administrative closure; no policy bypasses."""
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from . import tecnica_services as t, operacion_services as o, calibracion_services as c
from .models import (InformeFinal,RevisionInformeFinal,CierreExpediente,ExpedienteMantenimiento,
    Calibracion,Documento,ConfirmacionRecepcion,RecepcionRepuesto,ActividadReparacion,EvidenciaOperativa,
    EmisionCalibracion,EmisionOperativa)
from .recepcion_services import verificar_copia_persistente,evento


def autorizar(user,empresa,roles=None,exp=None):
    if not getattr(settings,'MASISCAM_CIERRE_LOCAL',False):
        raise PermissionDenied('Fase 6 habilitada únicamente en sandbox local autorizado.')
    return c.autorizar(user,empresa,roles,exp)


def expediente(user,empresa,pk,roles=None,lock=False):
    autorizar(user,empresa,roles)
    qs=ExpedienteMantenimiento.objects.select_for_update() if lock else ExpedienteMantenimiento.objects.all()
    exp=qs.filter(pk=pk,empresa=empresa).first()
    if not exp: raise PermissionDenied('Expediente no disponible.')
    if lock:
        from .access import validar_equipo_operativo
        validar_equipo_operativo(exp.registro.equipo,bloquear=True)
    autorizar(user,empresa,roles,exp)
    return exp


def abierto(exp):
    if CierreExpediente.objects.filter(expediente=exp).exists():
        raise ValidationError('Expediente cerrado: solo lectura. Un nuevo mantenimiento requiere otro expediente.')


def dependencias(exp):
    confirmation=ConfirmacionRecepcion.objects.filter(recepcion__expediente=exp).first()
    from .models import SolicitudRepuestos
    # The workshop handoff records intake separately from technical approval.
    # Legacy expedientes keep their original documentary prerequisite.
    if not confirmation and not SolicitudRepuestos.objects.filter(expediente=exp).exists():
        raise ValidationError('Recepción pendiente de confirmación.')
    if confirmation:
        verificar_copia_persistente(confirmation.guia.documento)
    auth,repair=c._reparacion(exp)
    cal=Calibracion.objects.filter(expediente=exp).order_by('-revision').first()
    if not cal:
        raise ValidationError('Calibración/protocolo aplicable pendiente; no hay exención industrial aprobada.')
    c._dependencias(cal)
    if cal.estado!='APROBADA' or not hasattr(cal,'decision') or cal.decision.decision!='APROBADA':
        raise ValidationError('Calibración vigente sin aprobación técnica independiente.')
    if cal.mediciones.filter(valor__isnull=False).count()!=16:
        raise ValidationError('Faltan mediciones verificables.')
    diag=auth.propuesta.diagnostico
    if not t._aprobado(diag):
        raise ValidationError('Diagnóstico aprobado no vigente.')
    # Parts are physical receipts, not an inventory allocation. Require the
    # exact diagnosis/part/unit coverage already used by the repair service.
    from django.db.models import Sum
    quantities={(r['pieza_id'],r['numero_parte'],r['unidad']):r['n'] for r in RecepcionRepuesto.objects.filter(expediente=exp).values('pieza_id','numero_parte','unidad').annotate(n=Sum('cantidad'))}
    for p in diag.piezas.all():
        qty=quantities.get((p.pk,p.numero_parte,p.unidad),0)
        if not p.unidad or qty<p.cantidad:
            raise ValidationError('Repuestos aplicables pendientes; no se admite una excepción implícita.')
    return auth,cal


def snapshot(exp):
    auth,cal=dependencias(exp)
    diag=auth.propuesta.diagnostico
    tech=t._snapshot_informe(diag)
    activities=[]
    for a in ActividadReparacion.objects.filter(autorizacion=auth).order_by('pk'):
        r=a.resultados.order_by('-version').first()
        activities.append(dict(id=a.pk,autor=a.autor.username,inicio=a.inicio.isoformat(),resultado_id=r.pk,
            fin=r.fin.isoformat(),trabajo=a.trabajo,componentes=a.componentes,resultado=r.resultado,
            trabajo_realizado=r.trabajo_realizado,observaciones=r.observaciones,revision=r.version,
            aprobacion=dict(id=r.revision.pk,responsable=r.revision.autor.username,fecha=r.revision.creado_en.isoformat(),motivo=r.revision.motivo),
            usos=[dict(recepcion=u.recepcion_id,cantidad=str(u.cantidad),unidad=u.recepcion.unidad) for u in a.usos.order_by('pk')]))
    evidence=[]
    def add(doc,section,caption,photo=False):
        t.documento_valido(doc,exp.empresa)
        evidence.append(dict(documento=doc.pk,hash=doc.hash_sha256,seccion=section,leyenda=caption,foto=photo))
    for e in exp.recepcion.evidencias.select_related('documento').order_by('pk'):
        add(e.documento,'Recepción',e.documento.titulo,e.tipo=='FOTO')
    for e in t._evidencias(diag):
        add(e.documento,'Inspección / diagnóstico',e.leyenda,e.documento.tipo_mime.startswith('image/'))
    for e in EvidenciaOperativa.objects.filter(expediente=exp).select_related('documento','actividad').order_by('pk'):
        # Commercial evidence remains an access-controlled reference, never
        # embedded in a technical report downloadable by the assigned technician.
        if (e.actividad_id and e.actividad.autorizacion_id==auth.pk) or e.recepcion_id:
            add(e.documento,'Intervención / repuestos',e.leyenda,e.clase in {'FOTO','ANTES','DURANTE','DESPUES'})
    for e in cal.evidencias.select_related('documento').order_by('pk'):
        add(e.documento,'Calibración',e.leyenda,e.clase=='FOTO')
    annexes=[]
    confirmation=ConfirmacionRecepcion.objects.filter(recepcion__expediente=exp).first()
    reports=[('Informe inicial',auth.propuesta.informe.documento),('Protocolo',cal.plantilla.documento)]
    if confirmation:
        reports.insert(0,('Guía de ingreso',confirmation.guia.documento))
    for em in EmisionCalibracion.objects.filter(calibracion=cal,estado='GENERADA').select_related('documento').order_by('version'):
        reports.append((f'Calibración v{em.version}',em.documento))
    for em in EmisionOperativa.objects.filter(expediente=exp,estado='GENERADA').select_related('documento').order_by('tipo','version'):
        ids=[r['id'] for r in em.snapshot.get('registros',[])]
        current=([a['id'] for a in activities] if em.tipo=='REPARACION' else list(RecepcionRepuesto.objects.filter(expediente=exp).order_by('pk').values_list('pk',flat=True)))
        revisions_match=(em.tipo!='REPARACION' or [(r['id'],r.get('version')) for r in em.snapshot.get('registros',[])]==[(a['id'],a['revision']) for a in activities])
        if ids==current and revisions_match and (em.tipo!='REPARACION' or em.snapshot.get('autorizacion')==auth.pk):
            reports.append((f'{em.tipo} v{em.version}',em.documento))
    for title,doc in reports:
        t.documento_valido(doc,exp.empresa)
        annexes.append(dict(titulo=title,documento=doc.pk,hash=doc.hash_sha256))
    result = dict(plantilla='final-v1',numero=exp.numero,empresa=exp.empresa_id,recepcion=exp.recepcion.snapshot,
        confirmacion=dict(placa=confirmation.placa,motivo_sin_placa=confirmation.motivo_sin_placa,
            motivo_sin_guia=confirmation.motivo_sin_guia,responsable=confirmation.responsable.username,
            fecha=confirmation.confirmado_en.isoformat(),politica=confirmation.politica) if confirmation else dict(estado='Recepción provisional sin confirmar'),
        tecnico=tech,autorizacion=dict(id=auth.pk,fecha=auth.creado_en.isoformat(),responsable=auth.autor.username),
        repuestos=[dict(id=r.pk,pieza=r.pieza_id,descripcion=r.descripcion,numero_parte=r.numero_parte,cantidad=str(r.cantidad),
            unidad=r.unidad,fecha=r.fecha.isoformat(),procedencia=r.procedencia,guia=r.guia,discrepancia=r.discrepancia) for r in RecepcionRepuesto.objects.filter(expediente=exp).order_by('pk')],
        reparaciones=activities,calibracion=c.snapshot(cal),evidencias=evidence,anexos=annexes,
        fecha_finalizacion=cal.decision.creado_en.isoformat(),
        aviso='Informe técnico privado. No sustituye autorizaciones comerciales ni acredita aceptación del cliente. No contiene firma certificada.')

    from .flujo_services import snapshot_final
    flujo = snapshot_final(exp)
    if flujo:
        result['flujo_operativo'] = flujo
        vistos={a['documento'] for a in result['anexos']}
        for pieza in diag.piezas.select_related('parte__referencia__documento'):
            if pieza.parte_id:
                ref=pieza.parte.referencia
                if ref.documento_id not in vistos:
                    t.documento_valido(ref.documento,exp.empresa)
                    result['anexos'].append(dict(titulo=f'Plano utilizado {ref.codigo} r{ref.revision}',
                        documento=ref.documento_id,hash=ref.documento.hash_sha256))
                    vistos.add(ref.documento_id)
        result['cotizacion'] = dict(referencia=auth.propuesta.snapshot.get('presupuesto_referencia',''),
            alcance=auth.propuesta.alcance, version=auth.propuesta.version, decision=auth.decision,
            fecha_cliente=auth.fecha_cliente.isoformat(), responsable=auth.autor.username)
        for evidencia in auth.propuesta.evidencias.all():
            t.documento_valido(evidencia.documento,exp.empresa)
            result['anexos'].append(dict(titulo='Presupuesto documentado',documento=evidencia.documento_id,hash=evidencia.documento.hash_sha256))
        for evidencia in auth.evidencias.all():
            t.documento_valido(evidencia.documento,exp.empresa)
            result['anexos'].append(dict(titulo='Respaldo de la aceptaci?n',documento=evidencia.documento_id,hash=evidencia.documento.hash_sha256))
    return result


def vigente(informe):
    if InformeFinal.objects.filter(expediente=informe.expediente,version__gt=informe.version).exists():
        raise ValidationError('Informe sustituido por una versión posterior.')
    if t._digest(snapshot(informe.expediente))!=informe.huella:
        raise ValidationError('Fuentes modificadas o aprobaciones retiradas; requiere nueva versión y revisión.')


@transaction.atomic
def reservar(*,usuario,empresa,expediente_id,clave,nueva_version=False,motivo=''):
    exp=expediente(usuario,empresa,expediente_id,{'OFICINA','TECNICO','TALLER'},True)
    if t.autorizar_tecnica(usuario,empresa).rol == 'TALLER' and not exp.solicitudrepuestos_set.exists():
        raise PermissionDenied('La generaci?n operativa requiere una solicitud de taller.')
    payload=dict(nueva_version=bool(nueva_version),motivo=motivo)
    old=t._retry(exp,clave,'FINAL_RESERVADO',payload)
    if old: return InformeFinal.objects.get(pk=old)
    abierto(exp)
    snap=snapshot(exp)
    prev=exp.informes_finales.order_by('-version').first()
    if prev and not nueva_version:
        vigente(prev)
        t._event(exp,usuario,clave,'FINAL_RESERVADO',payload,prev.pk)
        return prev
    if nueva_version and not motivo.strip():
        raise ValidationError('Nueva versión exige motivo; la anterior se conserva.')
    obj=t._save(InformeFinal(expediente=exp,calibracion_id=snap['calibracion']['calibracion'],version=prev.version+1 if prev else 1,
        sustituye=prev,motivo=motivo,snapshot=snap,huella=t._digest(snap),autor=usuario,clave=clave))
    t._event(exp,usuario,clave,'FINAL_RESERVADO',payload,obj.pk)
    return obj


def generar(informe_id):
    from .cierre_pdf import construir_informe
    em=InformeFinal.objects.get(pk=informe_id)
    autorizar(em.autor,em.expediente.empresa,{'OFICINA','TECNICO','TALLER'},em.expediente)
    if em.documento_id:
        verificar_copia_persistente(em.documento);return em.documento_id
    photos=[]
    for item in em.snapshot['evidencias']:
        doc=Documento.objects.get(pk=item['documento'])
        t.documento_valido(doc,em.expediente.empresa)
        if doc.hash_sha256!=item['hash']: raise ValidationError('Evidencia alterada.')
        if item['foto']:
            with doc.archivo.open('rb') as f: photos.append((item,f.read()))
    data=construir_informe(dict(em.snapshot,version=em.version),photos)
    with transaction.atomic():
        exp=ExpedienteMantenimiento.objects.select_for_update().get(pk=em.expediente_id)
        em=InformeFinal.objects.select_for_update().get(pk=informe_id)
        if em.documento_id:
            verificar_copia_persistente(em.documento);return em.documento_id
        abierto(exp);vigente(em)
        file=ContentFile(data,name=f'{exp.numero}-final-v{em.version}.pdf');file.content_type='application/pdf'
        doc=t.guardar_archivo(usuario=em.autor,empresa=exp.empresa,proyecto=exp.registro.equipo.proyecto,
            equipo=exp.registro.equipo,archivo=file,categoria='INFORMES')
        em.documento=doc;em.hash_sha256=doc.hash_sha256;em.enviado_en=timezone.now();em.estado='PENDIENTE_REVISION';em.error=''
        em.save(update_fields=['documento','hash_sha256','enviado_en','estado','error'])
        evento(exp,em.autor,'FINAL_PDF_GENERADO',informe=em.pk,version=em.version,hash=em.hash_sha256)
        if settings.GOOGLE_DRIVE_ENABLED: transaction.on_commit(lambda:t._encolar_documento(doc.pk))
        return doc.pk


def autores(em):
    ids=t._autores(em.calibracion.diagnostico)|c._autores(em.calibracion)
    ids.update(ActividadReparacion.objects.filter(expediente=em.expediente).values_list('autor_id',flat=True))
    ids.update(InformeFinal.objects.filter(expediente=em.expediente,version__lte=em.version).values_list('autor_id',flat=True))
    from .models import Inspeccion,Diagnostico
    ids.update(Inspeccion.objects.filter(expediente=em.expediente).values_list('tecnico_id',flat=True))
    ids.update(Diagnostico.objects.filter(expediente=em.expediente).values_list('tecnico_id',flat=True))
    ids.update(Calibracion.objects.filter(expediente=em.expediente).values_list('tecnico_id',flat=True))
    return ids


@transaction.atomic
def revisar(*,usuario,empresa,informe_id,clave,decision,motivo):
    em=InformeFinal.objects.get(pk=informe_id)
    exp=expediente(usuario,empresa,em.expediente_id,{'PLANTA'},True);em.refresh_from_db()
    payload=dict(informe=em.pk,decision=decision,motivo=motivo)
    old=t._retry(exp,clave,'FINAL_REVISION',payload)
    if old: return RevisionInformeFinal.objects.get(pk=old)
    abierto(exp);vigente(em)
    if usuario.pk in autores(em): raise PermissionDenied('Revisión independiente: no se aprueba trabajo propio ni sus correcciones.')
    if em.estado!='PENDIENTE_REVISION' or decision not in {'APROBADO','OBSERVADO'} or not motivo.strip():
        raise ValidationError('Informe enviado, decisión y fundamento obligatorios.')
    verificar_copia_persistente(em.documento)
    if em.documento.hash_sha256!=em.hash_sha256: raise ValidationError('Hash de emisión no válido.')
    obj=t._save(RevisionInformeFinal(informe=em,decision=decision,motivo=motivo,responsable=usuario,clave=clave))
    em.estado=decision;em.save(update_fields=['estado'])
    t._event(exp,usuario,clave,'FINAL_REVISION',payload,obj.pk)
    return obj


@transaction.atomic
def cerrar(*,usuario,empresa,informe_id,clave,motivo):
    em=InformeFinal.objects.get(pk=informe_id)
    exp=expediente(usuario,empresa,em.expediente_id,{'GERENCIA'},True);em.refresh_from_db()
    payload=dict(informe=em.pk,motivo=motivo)
    old=t._retry(exp,clave,'EXPEDIENTE_CERRADO',payload)
    if old: return CierreExpediente.objects.get(pk=old)
    existing=CierreExpediente.objects.filter(expediente=exp).first()
    if existing:
        if existing.informe_id!=em.pk: raise ValidationError('Expediente ya cerrado con otra emisión.')
        return existing
    vigente(em)
    if not motivo.strip() or em.estado!='APROBADO' or not hasattr(em,'revision') or em.revision.decision!='APROBADO':
        raise ValidationError('Cierre requiere informe aprobado independientemente y motivo.')
    if usuario.pk in autores(em) or usuario.pk==em.revision.responsable_id:
        raise PermissionDenied('Cierre administrativo independiente del trabajo y su revisión técnica.')
    verificar_copia_persistente(em.documento)
    if em.documento.hash_sha256!=em.hash_sha256: raise ValidationError('Hash final incoherente.')
    obj=t._save(CierreExpediente(expediente=exp,informe=em,responsable=usuario,motivo=motivo,huella=em.huella,clave=clave))
    em.estado='FINALIZADO';em.save(update_fields=['estado'])
    t._event(exp,usuario,clave,'EXPEDIENTE_CERRADO',payload,obj.pk)
    return obj

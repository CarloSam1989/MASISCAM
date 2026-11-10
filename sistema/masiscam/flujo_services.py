"""Orquestación del relevo operativo, sin sustituir las reglas de las fases 2–6."""
import uuid
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum, Max
from django.utils import timezone
from . import tecnica_services as t, operacion_services as o
from .models import (SolicitudRepuestos, AdquisicionRepuesto, EnvioRepuestos, DetalleEnvio,
    ConfirmacionEnvio, RecepcionLineaEnvio, Diagnostico, PiezaRequerida, Documento)
from .recepcion_services import _hash, verificar_copia_persistente, evento

LECTORES = {'TALLER','OFICINA','PLANTA','GERENCIA'}


def expediente(usuario, empresa, pk, roles=None, lock=False):
    return o.expediente(usuario, empresa, pk, roles or LECTORES, lock)


def solicitud(usuario, empresa, pk, roles=None, lock=False):
    obj = SolicitudRepuestos.objects.filter(pk=pk).first()
    if not obj:
        raise PermissionDenied('Solicitud no disponible.')
    exp = expediente(usuario, empresa, obj.expediente_id, roles, lock)
    if t._digest(obj.snapshot) != obj.huella:
        raise ValidationError('La solicitud no conserva su huella original.')
    return obj, exp


def _filas(diag):
    filas = []
    for pieza in diag.piezas.order_by('pk'):
        fotos = []
        for ev in pieza.fotografias.select_related('documento').order_by('pk'):
            t.documento_valido(ev.documento, diag.expediente.empresa)
            fotos.append(dict(documento=ev.documento_id, hash=ev.documento.hash_sha256, leyenda=ev.leyenda))
        filas.append(dict(pieza=pieza.pk, codigo=pieza.numero_parte, descripcion=pieza.descripcion,
            cantidad=str(pieza.cantidad), unidad=pieza.unidad, condicion=pieza.condicion,
            accion=pieza.accion, observaciones=pieza.observaciones, origen=pieza.snapshot, fotos=fotos))
    return filas


@transaction.atomic
def enviar_solicitud(*, usuario, empresa, diagnostico_id, version, clave, datos=None):
    diag = Diagnostico.objects.get(pk=diagnostico_id)
    exp = expediente(usuario, empresa, diag.expediente_id, {'TALLER'}, True)
    diag.refresh_from_db()
    try: version=int(version)
    except (ValueError,TypeError): raise ValidationError('Versión de borrador inválida.') from None
    payload = dict(diagnostico=diag.pk, version=version, datos=datos)
    old = t._retry(exp, clave, 'SOLICITUD_OFICINA_ENVIADA', payload)
    if old:
        return SolicitudRepuestos.objects.get(pk=old)
    if diag.tecnico_id != usuario.pk or not t.vigente(diag):
        raise PermissionDenied('Solo el responsable asignado envía su diagnóstico vigente.')
    previa = SolicitudRepuestos.objects.filter(diagnostico=diag).first()
    if previa:
        if datos is not None and (set(datos)-set(t.DIA_FIELDS) or any(str(value or '')!=str(getattr(diag,field,None) or '') for field,value in datos.items())):
            raise ValidationError('La solicitud está congelada. PLANTA debe autorizar una revisión para modificarla.')
        t._event(exp,usuario,clave,'SOLICITUD_OFICINA_ENVIADA',payload,previa.pk)
        return previa
    if not diag.piezas.exists() and diag.repuestos!='SIN_REPUESTOS' and (datos or {}).get('repuestos')!='SIN_REPUESTOS':
        raise ValidationError('Seleccione piezas o declare expresamente que no se requieren repuestos.')
    if not diag.enviado_en:
        if datos is not None:
            diag=t.guardar_revision(usuario=usuario,empresa=empresa,tipo='DIAGNOSTICO',revision_id=diag.pk,
                version=version,clave=uuid.uuid5(uuid.UUID(str(clave)),'guardar'),datos=datos)
        diag = t.enviar_revision(usuario=usuario, empresa=empresa, tipo='DIAGNOSTICO', revision_id=diag.pk,
            version=diag.version_edicion, clave=uuid.uuid5(uuid.UUID(str(clave)), 'diagnostico'))
    snap = dict(expediente=exp.numero, cliente=exp.recepcion.snapshot,
        diagnostico=diag.pk, revision=diag.revision, responsable=usuario.username, piezas=_filas(diag))
    obj = t._save(SolicitudRepuestos(expediente=exp, autor=usuario, clave=clave, diagnostico=diag,
        version=(exp.solicitudrepuestos_set.aggregate(n=Max('version'))['n'] or 0)+1,
        snapshot=snap, huella=t._digest(snap)))
    t._event(exp,usuario,clave,'SOLICITUD_OFICINA_ENVIADA',payload,obj.pk)
    return obj


def vigente(obj):
    if not t.vigente(obj.diagnostico) or _filas(obj.diagnostico) != obj.snapshot['piezas']:
        raise ValidationError('Solicitud sustituida o fuentes distintas; se requiere una nueva revisión.')


def autorizacion(obj):
    vigente(obj)
    auth = o.exigir_autorizacion(obj.expediente)
    if auth.propuesta.diagnostico_id != obj.diagnostico_id:
        raise ValidationError('La autorización no corresponde a la solicitud vigente.')
    if auth.propuesta.snapshot.get('propuesta_origen'):
        raise ValidationError('Aceptación parcial: revise la solicitud y cotización con las piezas y cantidades aceptadas. No se compra ni envía automáticamente el listado original completo.')
    return auth


@transaction.atomic
def cotizar(*, usuario, empresa, solicitud_id, clave, referencia, observaciones='', archivo=None):
    obj, exp = solicitud(usuario,empresa,solicitud_id,{'OFICINA','GERENCIA'},True)
    vigente(obj)
    # Reuse an existing proposal for this diagnosis; no retyping or parallel budget.
    anterior = o.PropuestaComercial.objects.filter(expediente=exp).order_by('-version').first()
    if anterior and anterior.diagnostico_id == obj.diagnostico_id:
        return anterior
    if not t._aprobado(obj.diagnostico):
        raise ValidationError('PLANTA debe aprobar el diagnóstico antes de cotizar.')
    informe = t.reservar_informe(usuario=usuario,empresa=empresa,diagnostico_id=obj.diagnostico_id,
        clave=uuid.uuid5(uuid.UUID(str(clave)), 'informe'))
    t.generar_informe(informe.pk)
    alcance = '\n'.join(f"{p['cantidad']} {p['unidad']} · {p['codigo'] or 'Código pendiente'} · {p['descripcion']} · {p['accion']}" for p in obj.snapshot['piezas'])
    if not obj.snapshot['piezas']:
        alcance='Trabajo declarado sin repuestos.\n'+obj.diagnostico.recomendaciones+'\n'+obj.diagnostico.conclusiones
    return o.proponer(usuario=usuario,empresa=empresa,expediente_id=exp.pk,diagnostico_id=obj.diagnostico_id,
        clave=clave, datos=dict(alcance=alcance,observaciones=observaciones,presupuesto_referencia=referencia),archivo=archivo)


@transaction.atomic
def adquirir(*, usuario, empresa, solicitud_id, pieza_id, cantidad, proveedor, referencia, fecha, clave,
             archivo=None, observaciones=''):
    obj, exp = solicitud(usuario,empresa,solicitud_id,{'OFICINA'},True)
    auth = autorizacion(obj)
    n = o._cantidad(cantidad); o._fecha(fecha)
    payload = dict(solicitud=obj.pk,pieza=pieza_id,cantidad=str(n),proveedor=proveedor,referencia=referencia,
        fecha=fecha.isoformat(),observaciones=observaciones,hash=_hash(archivo) if archivo else None)
    old = t._retry(exp,clave,'REPUESTO_ADQUIRIDO',payload)
    if old: return AdquisicionRepuesto.objects.get(pk=old)
    pieza = PiezaRequerida.objects.filter(pk=pieza_id,diagnostico=obj.diagnostico).first()
    if not pieza or not pieza.unidad or not proveedor.strip() or not referencia.strip():
        raise ValidationError('Pieza de la solicitud, unidad, proveedor/procedencia y referencia documental obligatorios.')
    comprado = obj.adquisiciones.filter(pieza=pieza).aggregate(n=Sum('cantidad'))['n'] or Decimal('0')
    if comprado+n > pieza.cantidad:
        raise ValidationError('La adquisición supera la cantidad solicitada. Se requiere una revisión y autorización del nuevo alcance.')
    doc = t.guardar_archivo(usuario=usuario,empresa=empresa,proyecto=exp.registro.equipo.proyecto,
        equipo=exp.registro.equipo,archivo=archivo,categoria='OTROS') if archivo else None
    result = t._save(AdquisicionRepuesto(expediente=exp,autor=usuario,clave=clave,solicitud=obj,pieza=pieza,
        autorizacion=auth,fecha=fecha,proveedor=proveedor.strip(),referencia=referencia.strip(),cantidad=n,
        unidad=pieza.unidad,documento=doc,observaciones=observaciones.strip()))
    t._event(exp,usuario,clave,'REPUESTO_ADQUIRIDO',payload,result.pk)
    return result


def disponible(adquisicion):
    return adquisicion.cantidad - (adquisicion.envios.aggregate(n=Sum('cantidad'))['n'] or Decimal('0'))


@transaction.atomic
def enviar_repuestos(*,usuario,empresa,solicitud_id,numero_guia,fecha,lineas,clave):
    obj, exp = solicitud(usuario,empresa,solicitud_id,{'OFICINA'},True)
    auth = autorizacion(obj); o._fecha(fecha)
    payload = dict(solicitud=obj.pk,guia=numero_guia,fecha=fecha.isoformat(),lineas=lineas)
    old = t._retry(exp,clave,'REPUESTOS_ENVIADOS',payload)
    if old: return EnvioRepuestos.objects.get(pk=old)
    if not numero_guia.strip() or not isinstance(lineas,list) or not 1 <= len(lineas) <= 500:
        raise ValidationError('Número de guía y al menos una cantidad enviada obligatorios.')
    filas=[]; ids=set()
    for fila in lineas:
        if not isinstance(fila,dict): raise ValidationError('Línea de envío inválida.')
        adq = obj.adquisiciones.filter(pk=fila.get('adquisicion'),autorizacion=auth).first()
        if not adq or adq.pk in ids:
            raise ValidationError('Adquisición ajena, duplicada o autorización sustituida.')
        n = o._cantidad(fila.get('cantidad'))
        if n > disponible(adq): raise ValidationError('La cantidad enviada supera la adquisición disponible.')
        ids.add(adq.pk)
        filas.append(dict(adquisicion=adq.pk,pieza=adq.pieza_id,codigo=adq.pieza.numero_parte,
            descripcion=adq.pieza.descripcion,cantidad=str(n),unidad=adq.unidad,proveedor=adq.proveedor))
    snap=dict(numero=exp.numero,cliente=exp.recepcion.snapshot,solicitud=obj.pk,version_solicitud=obj.version,
        solicitud_sha=obj.huella,
        guia=numero_guia.strip(),fecha=fecha.isoformat(),responsable=usuario.username,lineas=filas)
    from .flujo_pdf import guia_repuestos
    archivo = ContentFile(guia_repuestos(snap),name=f'{exp.numero}-repuestos-{clave}.pdf');archivo.content_type='application/pdf'
    doc=t.guardar_archivo(usuario=usuario,empresa=empresa,proyecto=exp.registro.equipo.proyecto,
        equipo=exp.registro.equipo,archivo=archivo,categoria='OTROS')
    result=t._save(EnvioRepuestos(expediente=exp,autor=usuario,clave=clave,solicitud=obj,fecha=fecha,
        numero_guia=numero_guia.strip(),snapshot=snap,documento=doc,hash_sha256=doc.hash_sha256))
    for fila in filas:
        t._save(DetalleEnvio(envio=result,adquisicion_id=fila['adquisicion'],cantidad=fila['cantidad'],unidad=fila['unidad']))
    t._event(exp,usuario,clave,'REPUESTOS_ENVIADOS',payload,result.pk)
    return result


@transaction.atomic
def confirmar_envio(*,usuario,empresa,envio_id,fecha,lineas,clave,observaciones=''):
    envio=EnvioRepuestos.objects.get(pk=envio_id)
    obj, exp=solicitud(usuario,empresa,envio.solicitud_id,{'TALLER'},True)
    vigente(obj); o._fecha(fecha)
    payload=dict(envio=envio.pk,fecha=fecha.isoformat(),lineas=lineas,observaciones=observaciones)
    old=t._retry(exp,clave,'ENVIO_RECIBIDO_TALLER',payload)
    if old: return ConfirmacionEnvio.objects.get(pk=old)
    if ConfirmacionEnvio.objects.filter(envio=envio).exists(): raise ValidationError('El envío ya fue confirmado; su recepción es histórica.')
    t.documento_valido(envio.documento,empresa)
    if envio.documento.hash_sha256 != envio.hash_sha256: raise ValidationError('Guía alterada.')
    detalles={d.pk:d for d in envio.lineas.select_related('adquisicion__pieza')}
    try:
        ids=[int(f['detalle']) for f in lineas]
    except (ValueError,KeyError,TypeError): raise ValidationError('Líneas de recepción inválidas.') from None
    if len(ids)!=len(set(ids)) or set(ids)!=set(detalles):
        raise ValidationError('Confirme todas las líneas de este envío, sin duplicados ni líneas ajenas.')
    result=t._save(ConfirmacionEnvio(expediente=exp,autor=usuario,clave=clave,envio=envio,fecha=fecha,observaciones=observaciones))
    for fila in lineas:
        det=detalles[int(fila['detalle'])]; adq=det.adquisicion
        try: n=Decimal(str(fila['cantidad']))
        except (InvalidOperation,KeyError): raise ValidationError('Cantidad recibida inválida.') from None
        if not n.is_finite() or n<0: raise ValidationError('Cantidad recibida no negativa obligatoria.')
        if n: n=o._cantidad(n)
        diferencia=str(fila.get('discrepancia','')).strip()
        if n!=det.cantidad and not diferencia:
            raise ValidationError('Documente faltantes, excedentes o diferencias antes de confirmar.')
        recepcion=None
        if n:
            recepcion=o.recibir_repuesto(usuario=usuario,empresa=empresa,expediente_id=exp.pk,
                clave=uuid.uuid5(uuid.UUID(str(clave)),str(det.pk)), datos=dict(pieza_id=adq.pieza_id,
                procedencia=adq.proveedor,entregado_por=envio.autor.username,numero_parte=adq.pieza.numero_parte,
                descripcion=adq.pieza.descripcion,cantidad=str(n),unidad=det.unidad,fecha=fecha,
                guia=envio.numero_guia,discrepancia=diferencia or observaciones))
        t._save(RecepcionLineaEnvio(confirmacion=result,detalle=det,recepcion=recepcion,cantidad=n,discrepancia=diferencia))
    t._event(exp,usuario,clave,'ENVIO_RECIBIDO_TALLER',payload,result.pk)
    return result


def snapshot_final(exp):
    """Optional additive section. Old reports with no request remain byte-for-byte versioned."""
    solicitudes=list(SolicitudRepuestos.objects.filter(expediente=exp).order_by('version'))
    if not solicitudes: return None
    rows=[]
    for obj in solicitudes:
        envios=[]
        for envio in obj.envios.select_related('documento').order_by('pk'):
            t.documento_valido(envio.documento,exp.empresa)
            if envio.documento.hash_sha256 != envio.hash_sha256:
                raise ValidationError('La guía no coincide con su SHA-256 original.')
            confirmacion=ConfirmacionEnvio.objects.filter(envio=envio).first()
            envios.append(dict(id=envio.pk,guia=envio.numero_guia,fecha=envio.fecha.isoformat(),hash=envio.hash_sha256,
                documento=envio.documento_id,lineas=envio.snapshot['lineas'],
                recibido=confirmacion.fecha.isoformat() if confirmacion else None,
                diferencias=list(confirmacion.lineas.order_by('pk').values('detalle_id','recepcion_id','cantidad','discrepancia')) if confirmacion else []))
        for compra in obj.adquisiciones.select_related('documento'):
            if compra.documento_id: t.documento_valido(compra.documento,exp.empresa)
        rows.append(dict(id=obj.pk,version=obj.version,huella=obj.huella,solicitud=obj.snapshot,
            adquisiciones=[dict(id=a.pk,pieza=a.pieza_id,cantidad=str(a.cantidad),unidad=a.unidad,proveedor=a.proveedor,
                referencia=a.referencia,fecha=a.fecha.isoformat(),autorizacion=a.autorizacion_id,
                documento=a.documento_id,hash=a.documento.hash_sha256 if a.documento_id else None)
                for a in obj.adquisiciones.select_related('documento').order_by('pk')], envios=envios))
    import json
    return json.loads(json.dumps(rows,default=str))


def generar_final_automatico(calibracion_id, propagar=False):
    from .models import Calibracion,Auditoria
    from . import cierre_services as cierre
    cal=Calibracion.objects.select_related('expediente__empresa','tecnico').get(pk=calibracion_id)
    if not SolicitudRepuestos.objects.filter(diagnostico_id=cal.diagnostico_id).exists(): return
    try:
        with transaction.atomic():
            exp=cierre.expediente(cal.tecnico,cal.expediente.empresa,cal.expediente_id,{'TALLER','TECNICO'},True)
            key=uuid.uuid5(uuid.NAMESPACE_URL,f'masiscam:final:calibracion:{cal.pk}')
            em=exp.informes_finales.filter(clave=key).first()
            if not em:
                prev=exp.informes_finales.order_by('-version').first()
                nueva=bool(prev and prev.huella!=t._digest(cierre.snapshot(exp)))
                em=cierre.reservar(usuario=cal.tecnico,empresa=exp.empresa,expediente_id=exp.pk,
                    clave=key,nueva_version=nueva,motivo='Fuentes aprobadas actualizadas tras revisión de calibración' if nueva else '')
            cierre.generar(em.pk)
            if not Auditoria.objects.filter(expediente=exp,accion='FINAL_GENERACION_AUTOMATICA',detalle__informe=em.pk).exists():
                evento(exp,cal.decision.responsable,'FINAL_GENERACION_AUTOMATICA',informe=em.pk,
                    calibracion=cal.pk,aprobacion=cal.decision.pk,responsable_datos=cal.tecnico_id,
                    automatica=True)
            return em
    except Exception as error:
        if propagar:
            if isinstance(error,(ValidationError,PermissionDenied)): raise
            raise ValidationError('No se pudo generar el PDF. Las fuentes y aprobaciones se conservan; puede reintentar la generación.') from error
        # Approval stays valid even if generation needs retry; the wizard exposes the blocker.
        import logging
        logging.getLogger(__name__).exception('Informe automático pendiente de reintento, calibración %s',cal.pk)


def resumen(exp, usuario):
    """Five visible steps and one next action; all writes still reauthorize."""
    from django.urls import reverse
    from .models import Inspeccion, Calibracion, InformeFinal, ActividadReparacion
    from . import calibracion_services as c, cierre_services as cierre
    role=t.autorizar_tecnica(usuario,exp.empresa,expediente=exp).rol
    ins=Inspeccion.objects.filter(expediente=exp).order_by('-revision').first()
    diag=Diagnostico.objects.filter(expediente=exp).order_by('-revision').first()
    sol=SolicitudRepuestos.objects.filter(diagnostico=diag).first() if diag else None
    prop=o.PropuestaComercial.objects.filter(expediente=exp).order_by('-version').first()
    cal=Calibracion.objects.filter(expediente=exp).order_by('-revision').first()
    informe=InformeFinal.objects.filter(expediente=exp).order_by('-version').first()
    pasos=[dict(nombre=n,estado='Pendiente') for n in ['Registrar equipo','Seleccionar piezas','Oficina cotiza y envía','Taller repara','Generar informe']]
    pasos[0]['estado']='Listo'
    accion=None; mensaje=''; paso=2; bloqueo=False
    def action(texto,op=None,url=None): return dict(texto=texto,op=op,url=url)
    if not hasattr(exp.recepcion,'confirmacion'):
        mensaje='Completa la recepción y la guía de ingreso para iniciar el trabajo técnico.'
        if role=='TALLER': accion=action('Completar recepción',url=reverse('masiscam:expediente_detalle',args=[exp.pk]))
    elif not ins:
        mensaje='Registra la inspección inicial. No se solicitan reparaciones ni calibraciones en esta etapa.'
        if role=='TALLER': accion=action('Iniciar inspección',op='inspeccion')
    elif not ins.enviado_en:
        mensaje='Guarda y envía los hallazgos de inspección antes de preparar las piezas.'
        if role=='TALLER': accion=action('Completar inspección',url=reverse('masiscam:tecnica_revision',args=['INSPECCION',ins.pk])+'?flujo=1')
    elif not diag:
        mensaje='Prepara la selección de piezas sobre un plano aplicable o mediante registro manual.'
        if role=='TALLER': accion=action('Seleccionar piezas',op='diagnostico')
    elif not sol and not diag.enviado_en:
        mensaje='Selecciona las piezas y documenta tus observaciones. Al enviar se congela la solicitud y se avisa a OFICINA.'
        if role=='TALLER': accion=action('Enviar solicitud de repuestos a oficina',op='solicitud')
    elif not sol:
        mensaje='El diagnóstico está enviado. Puedes entregar a OFICINA su listado congelado sin duplicar piezas.'
        if role=='TALLER' and diag.piezas.exists(): accion=action('Enviar solicitud de repuestos a oficina',op='solicitud')
        elif role=='PLANTA': accion=action('Revisar diagnóstico',url=reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',diag.pk])+'?flujo=1')
        else: mensaje='Diagnóstico sin solicitud de repuestos. Conserva su flujo técnico existente.'
    else:
        paso=3; pasos[1]['estado']='Listo'
        try: vigente(sol)
        except ValidationError as error: mensaje='; '.join(error.messages); bloqueo=True
        if not mensaje and role=='TALLER' and sol.envios.filter(confirmacion__isnull=True).exists():
            mensaje='Repuestos enviados. Confirma las cantidades físicas; la reparación seguirá exigiendo autorización comercial vigente.'
            accion=action('Confirmar repuestos recibidos',op='recibir')
        elif not mensaje and not t._aprobado(diag):
            mensaje='Solicitud recibida en OFICINA. PLANTA debe aprobar el diagnóstico antes de cotizar.'
            bloqueo=role!='PLANTA'
            if role=='PLANTA': accion=action('Revisar diagnóstico',url=reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',diag.pk])+'?flujo=1')
        elif not mensaje and (not prop or prop.diagnostico_id!=diag.pk):
            mensaje='OFICINA prepara la cotización a partir de las piezas solicitadas, sin reescribirlas.'
            if role=='OFICINA': accion=action('Preparar cotización',op='cotizar')
        elif not mensaje:
            try: autorizacion(sol); auth=True
            except ValidationError as error: auth=False; mensaje='; '.join(error.messages)
            if not auth:
                if not hasattr(prop,'decision') and role=='OFICINA': accion=action('Registrar respuesta del cliente',op='aceptacion')
                bloqueo=not bool(o.politica()) or hasattr(prop,'decision')
            else:
                pendientes=sol.envios.filter(confirmacion__isnull=True).order_by('pk')
                faltan=any((o.RecepcionRepuesto.objects.filter(expediente=exp,pieza=p,numero_parte=p.numero_parte,unidad=p.unidad).aggregate(n=Sum('cantidad'))['n'] or 0)<p.cantidad for p in diag.piezas.all())
                hay_disponibles=any(disponible(a)>0 for a in sol.adquisiciones.all())
                if pendientes.exists():
                    mensaje='Repuestos enviados. TALLER debe confirmar las cantidades realmente recibidas.'
                    if role=='TALLER': accion=action('Confirmar repuestos recibidos',op='recibir')
                elif faltan:
                    mensaje='OFICINA debe completar la adquisición y el envío de los repuestos pendientes.'
                    if role=='OFICINA': accion=action('Preparar guía y marcar enviados' if hay_disponibles else 'Registrar adquisición',op='enviar' if hay_disponibles else 'adquirir')
                else:
                    paso=4; pasos[2]['estado']='Listo'
                    estado=o.estado(exp)
                    if estado!='REPARACION_FINALIZADA':
                        pendientes_revision=ActividadReparacion.objects.filter(expediente=exp,autorizacion__propuesta=prop).order_by('pk')
                        sin_resultado=any(not a.resultados.exists() for a in pendientes_revision)
                        mensaje='Registra la reparación, el montaje, las fotografías y los repuestos utilizados.'
                        if role=='TALLER':
                            op='resultado' if sin_resultado else 'actividad'
                            if pendientes_revision.exists() and not sin_resultado: mensaje='PLANTA debe revisar las actividades terminadas.'
                            else: accion=action('Registrar resultado de reparación' if sin_resultado else 'Iniciar reparación',url=reverse('masiscam:operacion_operar',args=[exp.pk,op])+'?flujo=1')
                        elif role=='PLANTA': accion=action('Revisar reparación',url=reverse('masiscam:operacion_operar',args=[exp.pk,'revision'])+'?flujo=1')
                    elif not cal:
                        mensaje='La reparación está aprobada. Inicia la calibración con un protocolo exacto autorizado; no se presume una exención.'
                        protocolos=c.PlantillaCalibracion.objects.filter(empresa=exp.empresa,aplicabilidades__equipo=exp.registro.equipo).distinct()
                        if not any(c.plantilla_validada(p,exp.registro.equipo) for p in protocolos):
                            mensaje='Bloqueado: falta un protocolo técnico exacto validado y autorizado para este equipo. No existe una exención industrial aprobada.'
                            bloqueo=True
                        elif role=='TALLER': accion=action('Preparar calibración',url=reverse('masiscam:calibracion_expediente',args=[exp.pk])+'?flujo=1')
                    elif cal.estado!='APROBADA':
                        mensaje='Completa las mediciones y envía la calibración a la revisión independiente de PLANTA.'
                        if role in {'TALLER','PLANTA'}: accion=action('Revisar calibración' if role=='PLANTA' else 'Continuar calibración',url=reverse('masiscam:calibracion_revision',args=[cal.pk])+'?flujo=1')
                    else:
                        paso=5; pasos[3]['estado']='Listo'
                        mensaje='El informe PDF se genera con las fuentes aprobadas y conserva su revisión final independiente.'
                        if not informe and role in {'TALLER','OFICINA'}: accion=action('Generar informe',op='final')
                        elif informe:
                            if informe.documento_id: pasos[4]['estado']='Listo'
                            if informe.estado=='PENDIENTE_REVISION' and role=='PLANTA': accion=action('Revisar informe final',url=reverse('masiscam:cierre_detalle',args=[exp.pk])+'?flujo=1')
                            elif informe.estado=='APROBADO' and role=='GERENCIA': accion=action('Revisar cierre administrativo',url=reverse('masiscam:cierre_detalle',args=[exp.pk])+'?flujo=1')
                            elif role in {'TALLER','OFICINA'}: accion=action('Descargar informe final',url=reverse('masiscam:cierre_documento',args=[exp.pk,informe.pk])) if informe.documento_id else action('Reintentar generación del informe',op='final')
    if pasos[paso-1]['estado']!='Listo': pasos[paso-1]['estado']='Bloqueado' if bloqueo else ('En proceso' if accion else 'Esperando oficina' if paso==3 else 'Pendiente')
    if hasattr(exp,'cierre'):
        accion=action('Registrar nuevo mantenimiento',url=reverse('masiscam:recepcion_crear')+f'?equipo={exp.registro.equipo_id}') if role=='TALLER' else action('Descargar informe final',url=reverse('masiscam:cierre_documento',args=[exp.pk,exp.cierre.informe_id]))
        mensaje='Mantenimiento cerrado. Consulta de documentos e historial.'
        for p in pasos: p['estado']='Listo'
    return dict(pasos=pasos,paso=paso,accion=accion,mensaje=mensaje,bloqueo=bloqueo,
        inspeccion=ins,diagnostico=diag,solicitud=sol,propuesta=prop,calibracion=cal,informe=informe,rol=role)

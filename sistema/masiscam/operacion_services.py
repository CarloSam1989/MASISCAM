"""Local phase 4. Serialize all writes on the existing maintenance root."""
import uuid
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Sum, Q, OuterRef, Subquery
from django.utils import timezone
from . import tecnica_services as t
from .models import (PropuestaComercial, DecisionComercial, RecepcionRepuesto, ActividadReparacion,
    UsoRepuesto, ResultadoReparacion, RevisionReparacion, EvidenciaOperativa, EmisionOperativa,
    Diagnostico, EmisionInformeTecnico, Documento, PiezaRequerida, ExpedienteMantenimiento)
from .recepcion_services import _hash, validar_fotografias, verificar_copia_persistente, evento


def autorizar(usuario, empresa, roles=None, expediente=None):
    if not getattr(settings, 'MASISCAM_OPERACION_LOCAL', False):
        raise PermissionDenied('Fase 4 deshabilitada fuera del entorno local autorizado.')
    return t.autorizar_tecnica(usuario, empresa, roles, expediente)


def expediente(usuario, empresa, pk, roles=None, lock=False):
    autorizar(usuario, empresa, roles)
    exp = t.expediente_autorizado(usuario, empresa, pk, roles, lock)
    return exp


def politica():
    """None until owner approves a concrete acceptance procedure, not a switch."""
    p = getattr(settings, 'MASISCAM_POLITICA_COMERCIAL_LOCAL', None)
    required = ('version', 'aprobacion_propietario', 'procedimiento', 'vigente_desde')
    if not isinstance(p, dict) or any(not isinstance(p.get(k), str) or not p[k].strip() for k in required):
        return None
    try:
        since = timezone.datetime.fromisoformat(p['vigente_desde'])
        if timezone.is_naive(since) or since > timezone.now():
            return None
    except ValueError:
        return None
    return {k:p[k] for k in required}


def _texto(data, fields):
    for field in fields:
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValidationError(f'Complete {field}.')


def _cantidad(value):
    try:
        n = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValidationError('Cantidad decimal inválida.') from None
    if not n.is_finite() or n <= 0 or n >= Decimal('100000000000000') or n.as_tuple().exponent < -6:
        raise ValidationError('Cantidad positiva; máximo 14 enteros y seis decimales sin redondeo.')
    return n


def _fecha(value):
    if not isinstance(value, timezone.datetime) or timezone.is_naive(value):
        raise ValidationError('Fecha y hora con zona horaria requeridas.')
    return value


def _datos(data, allowed):
    if set(data) - set(allowed):
        raise ValidationError('Campos no permitidos; no se aceptan estados, autores o aprobaciones del navegador.')


def _evidencia(obj, user, archivo, clase, leyenda, clave):
    if not archivo:
        return None
    if clase != 'DOCUMENTO':
        validar_fotografias([archivo])
    exp = obj.expediente
    # The user supplies bytes, not an arbitrary document ID. Reuse an identical
    # protected receipt photo from this same maintenance without copying bytes
    # or changing its original metadata/Drive identity.
    doc = Documento.objects.filter(proyecto=exp.registro.equipo.proyecto, hash_sha256=_hash(archivo),
        evidencia_recepcion__recepcion__expediente=exp).first()
    if doc:
        if clase=='DOCUMENTO' or doc.categoria!='FOTOGRAFIAS':
            raise ValidationError('La fotografía original no sustituye un documento comercial.')
        t.documento_valido(doc,exp.empresa)
    else:
        doc = t.guardar_archivo(usuario=user, empresa=exp.empresa, proyecto=exp.registro.equipo.proyecto,
            equipo=exp.registro.equipo, archivo=archivo, categoria='OTROS' if clase=='DOCUMENTO' else 'FOTOGRAFIAS')
    field = {PropuestaComercial:'propuesta', DecisionComercial:'decision', RecepcionRepuesto:'recepcion', ActividadReparacion:'actividad'}[type(obj)]
    result = t._save(EvidenciaOperativa(expediente=exp, autor=user, clave=clave, documento=doc,
        clase=clase, leyenda=leyenda, **{field:obj}))
    if settings.GOOGLE_DRIVE_ENABLED:
        transaction.on_commit(lambda:t._encolar_documento(doc.pk))
    return result


def _propuesta_vigente(p):
    return t._aprobado(p.diagnostico) and not PropuestaComercial.objects.filter(expediente=p.expediente,version__gt=p.version).exists()


def autorizacion_valida(auth):
    p = politica()
    verificacion=auth.politica.get('verificacion_cliente', {})
    return bool(p and auth.decision=='APROBADO' and all(auth.politica.get(k)==v for k,v in p.items())
        and verificacion.get('confirmada') is True and verificacion.get('alcance')==auth.propuesta.alcance
        and verificacion.get('presupuesto')==auth.propuesta.snapshot.get('presupuesto_referencia')
        and verificacion.get('diagnostico')==auth.propuesta.diagnostico_id
        and verificacion.get('propuesta')==auth.propuesta_id and verificacion.get('version')==auth.propuesta.version
        and verificacion.get('informe_hash')==auth.propuesta.informe.hash_sha256
        and _propuesta_vigente(auth.propuesta))


def exigir_autorizacion(exp):
    auth = DecisionComercial.objects.filter(expediente=exp, decision='APROBADO').order_by('-propuesta__version').first()
    if not auth or not autorizacion_valida(auth):
        raise ValidationError('Reparación bloqueada: falta política aprobada y autorización comercial vigente para el diagnóstico exacto.')
    verificar_presupuesto(auth.propuesta)
    # Check acceptance evidence and the exact initial report before each action.
    for e in auth.evidencias.select_related('documento'):
        t.documento_valido(e.documento, exp.empresa)
    if not auth.evidencias.exists():
        raise ValidationError('Autorización sin respaldo verificable.')
    verificacion=auth.politica['verificacion_cliente']
    if verificacion.get('calidad')=='REPRESENTANTE' and not auth.evidencias.filter(
            documento__hash_sha256=verificacion.get('facultad_hash')).exists():
        raise ValidationError('Facultades del representante sin respaldo íntegro.')
    t.documento_valido(auth.propuesta.informe.documento, exp.empresa)
    return auth


def estado(exp):
    p = PropuestaComercial.objects.filter(expediente=exp).order_by('-version').first()
    if not p:
        return 'PENDIENTE_AUTORIZACION'
    if not _propuesta_vigente(p):
        return 'OBSERVADO'
    d = DecisionComercial.objects.filter(propuesta=p).first()
    if not d:
        return 'PENDIENTE_AUTORIZACION'
    if d.decision=='RECHAZADO':
        return 'RECHAZADO'
    if not autorizacion_valida(d):
        return 'OBSERVADO'
    acts=ActividadReparacion.objects.filter(autorizacion=d).annotate(
        ultimo=Subquery(ResultadoReparacion.objects.filter(actividad_id=OuterRef('pk')).order_by('-version').values('pk')[:1]))
    statuses=list(acts.annotate(revision_actual=Subquery(RevisionReparacion.objects.filter(resultado_id=OuterRef('ultimo')).values('decision')[:1])).values_list('revision_actual',flat=True))
    if not statuses:
        return 'AUTORIZADO'
    if 'OBSERVADO' in statuses: return 'OBSERVADO'
    return 'REPARACION_FINALIZADA' if all(v=='APROBADO' for v in statuses) else 'EN_REPARACION'


@transaction.atomic
def proponer(*, usuario, empresa, expediente_id, diagnostico_id, clave, datos, archivo=None):
    exp = expediente(usuario, empresa, expediente_id, {'GERENCIA'}, True)
    _datos(datos, ('alcance','observaciones','presupuesto_referencia'))
    payload = dict(diagnostico=diagnostico_id, datos=datos, hash=_hash(archivo) if archivo else None)
    old = t._retry(exp, clave, 'COMERCIAL_PROPUESTA', payload)
    if old:
        return PropuestaComercial.objects.get(pk=old)
    diag = Diagnostico.objects.filter(pk=diagnostico_id, expediente=exp).first()
    if not diag or not t._aprobado(diag):
        raise ValidationError('Se requiere diagnóstico vigente aprobado.')
    informe = EmisionInformeTecnico.objects.filter(diagnostico=diag,estado='GENERADA').order_by('-version').first()
    if not informe:
        raise ValidationError('Emita el informe inicial antes de presentar una propuesta.')
    t.documento_valido(informe.documento, empresa)
    _texto(datos, ('alcance',))
    data = dict(datos)
    referencia = data.pop('presupuesto_referencia', '')
    if not isinstance(referencia, str) or len(referencia) > 200:
        raise ValidationError('Referencia de presupuesto inválida; máximo 200 caracteres.')
    anterior = PropuestaComercial.objects.filter(expediente=exp).order_by('-version').first()
    result = t._save(PropuestaComercial(expediente=exp,autor=usuario,clave=clave,diagnostico=diag,informe=informe,
        version=anterior.version+1 if anterior else 1,sustituye=anterior, snapshot=dict(
            numero=exp.numero,cliente=exp.recepcion.snapshot,diagnostico=diag.pk,revision=diag.revision,
            informe=informe.pk,hash=informe.hash_sha256,
            presupuesto_referencia=referencia.strip()), **data))
    _evidencia(result, usuario, archivo, 'DOCUMENTO', 'Propuesta / presupuesto aportado; no acredita aceptación.', uuid.uuid4())
    t._event(exp, usuario, clave, 'COMERCIAL_PROPUESTA', payload, result.pk)
    return result


@transaction.atomic
def decidir_comercial(*, usuario, empresa, propuesta_id, clave, datos, archivo, archivo_facultad=None):
    p = PropuestaComercial.objects.get(pk=propuesta_id)
    exp = expediente(usuario, empresa, p.expediente_id, {'GERENCIA'}, True)
    basicos=('decision','fecha_cliente','representante_cliente','motivo')
    verificacion_campos=('identidad','calidad','calidad_representante','medio_verificacion','canal',
        'respaldo_verificacion','verificacion_confirmada','alcance_autorizado')
    _datos(datos, basicos+verificacion_campos)
    payload = dict(propuesta=p.pk,datos=datos,hash=_hash(archivo) if archivo else None,
        facultad=_hash(archivo_facultad) if archivo_facultad else None)
    old = t._retry(exp,clave,'COMERCIAL_DECISION',payload)
    if old:
        return DecisionComercial.objects.get(pk=old)
    _texto(datos, ('representante_cliente','motivo'))
    if datos.get('decision') not in {'APROBADO','PARCIAL','RECHAZADO'} or not archivo or not _propuesta_vigente(p):
        raise ValidationError('Decisión, respaldo y propuesta vigente obligatorios.')
    _fecha(datos.get('fecha_cliente'))
    _texto(datos,('identidad','medio_verificacion','respaldo_verificacion'))
    if (datos.get('calidad') not in {'TITULAR','REPRESENTANTE'} or
            datos.get('canal') not in {'PRESENCIAL_FIRMADA','ELECTRONICA_VERIFICABLE'}):
        raise ValidationError('Identidad, calidad y canal documentados obligatorios también para registrar el rechazo.')
    if any(not isinstance(datos.get(k,''),str) or len(datos.get(k,''))>20000
            for k in verificacion_campos if k!='verificacion_confirmada'):
        raise ValidationError('Datos de verificación o alcance inválidos.')
    pol = politica()
    positiva=datos['decision'] in {'APROBADO','PARCIAL'}
    if positiva and not pol:
        raise ValidationError('Aceptación comercial deshabilitada: procedimiento pendiente de aprobación expresa del propietario.')
    verificacion={}
    if not positiva:
        verificacion={k:datos.get(k,'') for k in verificacion_campos if k not in {'verificacion_confirmada','alcance_autorizado'}}
        verificacion.update(confirmada=False,tipo='RECHAZADO',registrado_por=usuario.pk,
            registrado_en=timezone.now().isoformat(),alcance=p.alcance,presupuesto=p.snapshot.get('presupuesto_referencia',''),
            diagnostico=p.diagnostico_id,propuesta=p.pk,version=p.version)
    if positiva:
        verificar_presupuesto(p)
        if (datos.get('calidad') not in {'TITULAR','REPRESENTANTE'} or
                datos.get('canal') not in {'PRESENCIAL_FIRMADA','ELECTRONICA_VERIFICABLE'} or
                datos.get('verificacion_confirmada') is not True):
            raise ValidationError('GERENCIA debe verificar identidad, facultades, canal y suficiencia del respaldo; no basta declarar aceptación.')
        if datos['calidad']=='REPRESENTANTE':
            _texto(datos,('calidad_representante',))
            if not archivo_facultad:
                raise ValidationError('Adjunte las facultades documentadas del representante.')
        if datos['decision']=='PARCIAL':
            _texto(datos,('alcance_autorizado',))
            if datos['alcance_autorizado'].strip()==p.alcance.strip():
                raise ValidationError('Identifique el alcance reducido; para el mismo alcance utilice aceptación total.')
        elif datos.get('alcance_autorizado','').strip():
            raise ValidationError('Una modificación del alcance exige aceptación parcial en nueva versión.')
        verificacion={k:datos.get(k,'') for k in verificacion_campos if k not in {'verificacion_confirmada','alcance_autorizado'}}
        verificacion.update(confirmada=True,tipo=datos['decision'],verificado_por=usuario.pk,
            verificado_en=timezone.now().isoformat(),facultad_hash=_hash(archivo_facultad) if archivo_facultad else None)
    if DecisionComercial.objects.filter(propuesta=p).exists():
        raise ValidationError('Decisión inmutable; registre otra propuesta para reconsideración.')
    if datos['decision']=='PARCIAL':
        origen=p
        p=t._save(PropuestaComercial(expediente=exp,autor=usuario,clave=uuid.uuid4(),diagnostico=origen.diagnostico,
            informe=origen.informe,version=origen.version+1,sustituye=origen,alcance=datos['alcance_autorizado'].strip(),
            observaciones='Alcance parcial expresamente aceptado; no hereda aprobaciones.',
            snapshot=dict(origen.snapshot,propuesta_origen=origen.pk)))
        for evidencia in origen.evidencias.select_related('documento'):
            t._save(EvidenciaOperativa(expediente=exp,autor=usuario,clave=uuid.uuid4(),propuesta=p,
                documento=evidencia.documento,clase=evidencia.clase,leyenda=evidencia.leyenda))
        t._event(exp,usuario,uuid.uuid4(),'COMERCIAL_PROPUESTA_PARCIAL',dict(origen=origen.pk,alcance=p.alcance),p.pk)
    if positiva:
        verificacion.update(alcance=p.alcance,presupuesto=p.snapshot['presupuesto_referencia'],diagnostico=p.diagnostico_id,
            propuesta=p.pk,version=p.version,informe_hash=p.informe.hash_sha256)
    data={k:datos[k] for k in basicos}
    data['decision']='APROBADO' if positiva else 'RECHAZADO'
    politica_snapshot=dict(pol or {'estado':'PENDIENTE'},verificacion_cliente=verificacion)
    obj = t._save(DecisionComercial(expediente=exp,autor=usuario,clave=clave,propuesta=p,politica=politica_snapshot,**data))
    _evidencia(obj,usuario,archivo,'DOCUMENTO','Respaldo de decisión aportado por GERENCIA; sin firma digital certificada.',uuid.uuid4())
    if archivo_facultad:
        _evidencia(obj,usuario,archivo_facultad,'DOCUMENTO','Facultades e identidad verificadas por GERENCIA.',uuid.uuid4())
    t._event(exp,usuario,clave,'COMERCIAL_DECISION',payload,obj.pk)
    return obj


def verificar_presupuesto(propuesta):
    """Drafts may be incomplete; acceptance must reference an intact budget."""
    if not propuesta.snapshot.get('presupuesto_referencia', '').strip():
        raise ValidationError('Falta referencia y versión del presupuesto. Prepare una nueva propuesta; no modifique el historial.')
    evidencias = list(propuesta.evidencias.select_related('documento'))
    if not evidencias:
        raise ValidationError('Adjunte el presupuesto de la propuesta antes de aceptar.')
    for evidencia in evidencias:
        t.documento_valido(evidencia.documento, propuesta.expediente.empresa)


@transaction.atomic
def recibir_repuesto(*, usuario, empresa, expediente_id, clave, datos, archivo=None, fotografia=None):
    exp = expediente(usuario,empresa,expediente_id,{'OFICINA','PLANTA','TALLER'},True)
    _datos(datos, ('pieza_id','procedencia','entregado_por','numero_parte','descripcion','cantidad','unidad','fecha','guia','discrepancia'))
    payload = dict(datos=datos,hash=_hash(archivo) if archivo else None,foto=_hash(fotografia) if fotografia else None)
    old = t._retry(exp,clave,'REPUESTO_RECIBIDO',payload)
    if old:
        return RecepcionRepuesto.objects.get(pk=old)
    _texto(datos, ('procedencia','entregado_por','descripcion','unidad'))
    data = dict(datos)
    data['cantidad'] = _cantidad(data.get('cantidad'))
    _fecha(data.get('fecha'))
    pieza = PiezaRequerida.objects.filter(pk=data.get('pieza_id'),diagnostico__expediente=exp).first() if data.get('pieza_id') else None
    if data.get('pieza_id') and (not pieza or not t._aprobado(pieza.diagnostico)):
        raise ValidationError('Pieza no pertenece al diagnóstico vigente aprobado de este expediente.')
    recibido = (RecepcionRepuesto.objects.filter(pieza=pieza).aggregate(n=Sum('cantidad'))['n'] or Decimal('0')) if pieza else Decimal('0')
    mismatch = not pieza or data.get('numero_parte','')!=pieza.numero_parte or data['unidad']!=pieza.unidad or data['cantidad']!=pieza.cantidad-recibido
    if mismatch and not data.get('discrepancia','').strip():
        raise ValidationError('Documente discrepancia, entrega parcial/excedente o recepción no vinculada a pieza requerida.')
    snap = dict(pieza=pieza.pk if pieza else None,diagnostico=pieza.diagnostico_id if pieza else None,
        cantidad_requerida=str(pieza.cantidad) if pieza else None,recibido_previamente=str(recibido),discrepancia=mismatch)
    obj = t._save(RecepcionRepuesto(expediente=exp,autor=usuario,clave=clave,snapshot=snap,**data))
    _evidencia(obj,usuario,archivo,'DOCUMENTO','Documento de ingreso de repuesto',uuid.uuid4())
    _evidencia(obj,usuario,fotografia,'FOTO','Fotografía de repuesto recibido',uuid.uuid4())
    t._event(exp,usuario,clave,'REPUESTO_RECIBIDO',payload,obj.pk)
    return obj


@transaction.atomic
def iniciar_actividad(*, usuario, empresa, expediente_id, clave, datos):
    exp = expediente(usuario,empresa,expediente_id,{'TECNICO','TALLER'},True)
    _datos(datos, ('trabajo','componentes','inicio','observaciones'))
    old = t._retry(exp,clave,'REPARACION_INICIADA',datos)
    if old:
        return ActividadReparacion.objects.get(pk=old)
    auth = exigir_autorizacion(exp)
    _texto(datos, ('trabajo','componentes'))
    _fecha(datos.get('inicio'))
    # Required parts must be documented as received with matching units and
    # identity. No inventory entry or implicit exception is generated.
    for pieza in auth.propuesta.diagnostico.piezas.all():
        qty = RecepcionRepuesto.objects.filter(expediente=exp,pieza=pieza,unidad=pieza.unidad,
            numero_parte=pieza.numero_parte).aggregate(n=Sum('cantidad'))['n'] or Decimal('0')
        if not pieza.unidad or qty < pieza.cantidad:
            raise ValidationError('Repuestos requeridos pendientes; no hay política aprobada de excepciones para iniciar.')
    obj = t._save(ActividadReparacion(expediente=exp,autor=usuario,clave=clave,autorizacion=auth,**datos))
    t._event(exp,usuario,clave,'REPARACION_INICIADA',datos,obj.pk)
    return obj


def _actividad(usuario, empresa, pk):
    act = ActividadReparacion.objects.get(pk=pk)
    exp = expediente(usuario,empresa,act.expediente_id,{'TECNICO','TALLER'},True)
    if act.autor_id!=usuario.pk or exigir_autorizacion(exp).pk!=act.autorizacion_id:
        raise PermissionDenied('Se requiere autor asignado y autorización vigente de la actividad.')
    return act,exp


def _abierta(act):
    result = act.resultados.order_by('-version').first()
    review = RevisionReparacion.objects.filter(resultado=result).first() if result else None
    if result and (not review or review.decision!='OBSERVADO'):
        raise ValidationError('Actividad enviada o aprobada; no admite cambios. Una observación abre una corrección versionada.')


@transaction.atomic
def usar_repuesto(*, usuario, empresa, actividad_id, recepcion_id, cantidad, observaciones, clave):
    act,exp = _actividad(usuario,empresa,actividad_id)
    payload = dict(actividad=act.pk,recepcion=recepcion_id,cantidad=str(cantidad),observaciones=observaciones)
    old = t._retry(exp,clave,'REPUESTO_UTILIZADO',payload)
    if old:
        return UsoRepuesto.objects.get(pk=old)
    _abierta(act)
    r = RecepcionRepuesto.objects.filter(pk=recepcion_id,expediente=exp).first()
    if not r or (r.pieza_id and r.pieza.diagnostico_id!=act.autorizacion.propuesta.diagnostico_id):
        raise ValidationError('Recepción ajena o correspondiente a otro diagnóstico.')
    n = _cantidad(cantidad)
    usado = UsoRepuesto.objects.filter(recepcion=r).aggregate(n=Sum('cantidad'))['n'] or Decimal('0')
    if usado+n > r.cantidad:
        raise ValidationError('Uso documentado supera cantidad recibida; no se generan ajustes de inventario.')
    obj = t._save(UsoRepuesto(expediente=exp,autor=usuario,clave=clave,actividad=act,recepcion=r,cantidad=n,observaciones=observaciones))
    t._event(exp,usuario,clave,'REPUESTO_UTILIZADO',payload,obj.pk)
    return obj


@transaction.atomic
def evidencia_actividad(*, usuario, empresa, actividad_id, clave, clase, leyenda, archivo):
    act,exp = _actividad(usuario,empresa,actividad_id)
    payload = dict(actividad=act.pk,clase=clase,leyenda=leyenda,hash=_hash(archivo) if archivo else None)
    old = t._retry(exp,clave,'REPARACION_EVIDENCIA',payload)
    if old:
        return EvidenciaOperativa.objects.get(pk=old)
    _abierta(act)
    if clase not in {'ANTES','DURANTE','DESPUES'} or not leyenda.strip() or not archivo:
        raise ValidationError('Indique fotografía, momento y leyenda.')
    obj = _evidencia(act,usuario,archivo,clase,leyenda,clave)
    t._event(exp,usuario,clave,'REPARACION_EVIDENCIA',payload,obj.pk)
    return obj


@transaction.atomic
def terminar_actividad(*, usuario, empresa, actividad_id, clave, datos):
    act,exp = _actividad(usuario,empresa,actividad_id)
    _datos(datos, ('fin','trabajo_realizado','resultado','observaciones'))
    payload = dict(actividad=act.pk,datos=datos)
    old = t._retry(exp,clave,'REPARACION_RESULTADO',payload)
    if old:
        return ResultadoReparacion.objects.get(pk=old)
    _abierta(act)
    _texto(datos, ('trabajo_realizado','resultado'))
    if _fecha(datos.get('fin')) < act.inicio:
        raise ValidationError('Fin anterior al inicio.')
    n = (act.resultados.aggregate(n=Max('version'))['n'] or 0)+1
    snap = dict(evidencias=[dict(documento=e.documento_id,hash=e.documento.hash_sha256,clase=e.clase,leyenda=e.leyenda)
        for e in act.evidencias.select_related('documento').order_by('pk')],usos=[dict(id=u.pk,recepcion=u.recepcion_id,cantidad=str(u.cantidad)) for u in act.usos.order_by('pk')])
    for e in act.evidencias.all():
        t.documento_valido(e.documento,empresa)
    obj = t._save(ResultadoReparacion(expediente=exp,autor=usuario,clave=clave,actividad=act,version=n,snapshot=snap,**datos))
    t._event(exp,usuario,clave,'REPARACION_RESULTADO',payload,obj.pk)
    return obj


@transaction.atomic
def revisar_actividad(*, usuario, empresa, resultado_id, clave, decision, motivo):
    result = ResultadoReparacion.objects.get(pk=resultado_id)
    exp = expediente(usuario,empresa,result.expediente_id,{'PLANTA'},True)
    payload = dict(resultado=result.pk,decision=decision,motivo=motivo)
    old = t._retry(exp,clave,'REPARACION_REVISION',payload)
    if old:
        return RevisionReparacion.objects.get(pk=old)
    if usuario.pk in {result.autor_id,result.actividad.autor_id}:
        raise PermissionDenied('No puede revisar su propio trabajo, incluso cambiando de rol.')
    if exigir_autorizacion(exp).pk!=result.actividad.autorizacion_id or result.actividad.resultados.order_by('-version').first().pk!=result.pk:
        raise ValidationError('Resultado o autorización sustituidos.')
    if decision not in {'APROBADO','OBSERVADO'} or not motivo.strip():
        raise ValidationError('Decisión y observaciones de revisión obligatorias.')
    obj = t._save(RevisionReparacion(expediente=exp,autor=usuario,clave=clave,resultado=result,decision=decision,motivo=motivo))
    t._event(exp,usuario,clave,'REPARACION_REVISION',payload,obj.pk)
    return obj


def documentos_visibles(exp, rol):
    # Commercial documents are restricted even within the same company.
    qs = Documento.objects.filter(Q(evidencias_operativas__expediente=exp)|Q(emision_operativa__expediente=exp))
    if rol not in {'GERENCIA','OFICINA'}:
        qs = qs.filter(Q(evidencias_operativas__recepcion__isnull=False)|Q(evidencias_operativas__actividad__isnull=False)|Q(emision_operativa__isnull=False))
    return qs.distinct()


@transaction.atomic
def respaldo_propuesta(*, usuario, empresa, propuesta_id, clave, leyenda, archivo):
    p=PropuestaComercial.objects.get(pk=propuesta_id)
    exp=expediente(usuario,empresa,p.expediente_id,{'OFICINA','GERENCIA'},True)
    payload=dict(propuesta=p.pk,leyenda=leyenda,hash=_hash(archivo) if archivo else None)
    old=t._retry(exp,clave,'COMERCIAL_RESPALDO',payload)
    if old:
        return EvidenciaOperativa.objects.get(pk=old)
    if not archivo or not leyenda.strip() or not _propuesta_vigente(p) or DecisionComercial.objects.filter(propuesta=p).exists():
        raise ValidationError('Adjunte respaldo con descripción a propuesta vigente sin decisión.')
    obj=_evidencia(p,usuario,archivo,'DOCUMENTO',leyenda,clave)
    t._event(exp,usuario,clave,'COMERCIAL_RESPALDO',payload,obj.pk)
    return obj


@transaction.atomic
def evidencia_repuesto(*, usuario, empresa, recepcion_id, clave, leyenda, archivo):
    r=RecepcionRepuesto.objects.get(pk=recepcion_id)
    exp=expediente(usuario,empresa,r.expediente_id,{'OFICINA','PLANTA','TALLER'},True)
    payload=dict(recepcion=r.pk,leyenda=leyenda,hash=_hash(archivo) if archivo else None)
    old=t._retry(exp,clave,'REPUESTO_EVIDENCIA',payload)
    if old:
        return EvidenciaOperativa.objects.get(pk=old)
    if not archivo or not leyenda.strip():
        raise ValidationError('Adjunte fotografía y descripción del repuesto recibido.')
    obj=_evidencia(r,usuario,archivo,'FOTO',leyenda,clave)
    t._event(exp,usuario,clave,'REPUESTO_EVIDENCIA',payload,obj.pk)
    return obj


@transaction.atomic
def reservar_documento(*, usuario, empresa, expediente_id, tipo, clave, nueva_version=False, motivo=''):
    exp = expediente(usuario,empresa,expediente_id,{'OFICINA','PLANTA'},True)
    if tipo not in {'REPUESTOS','REPARACION'}:
        raise ValidationError('Tipo documental inválido.')
    payload = dict(tipo=tipo,nueva_version=bool(nueva_version),motivo=motivo)
    old = t._retry(exp,clave,'OPERACION_EMISION',payload)
    if old:
        return EmisionOperativa.objects.get(pk=old)
    actual = EmisionOperativa.objects.filter(expediente=exp,tipo=tipo).order_by('-version').first()
    if actual and not nueva_version:
        if tipo=='REPARACION':
            auth=exigir_autorizacion(exp)
            current_ids=list(ActividadReparacion.objects.filter(autorizacion=auth).order_by('pk').values_list('pk',flat=True))
            if (estado(exp)!='REPARACION_FINALIZADA' or auth.pk!=actual.snapshot['autorizacion']
                    or current_ids!=[r['id'] for r in actual.snapshot['registros']]):
                raise ValidationError('El reporte anterior es histórico. Complete la fuente vigente y solicite nueva versión con motivo; no se reutiliza como reporte actual.')
        t._event(exp,usuario,clave,'OPERACION_EMISION',payload,actual.pk)
        return actual
    if nueva_version and not motivo.strip():
        raise ValidationError('Nueva versión requiere motivo.')
    snap = dict(plantilla='operacion-v1',tipo=tipo,numero=exp.numero,recepcion=exp.recepcion.snapshot,
        version=actual.version+1 if actual else 1,motivo=motivo,estado=estado(exp),evidencias=[])
    if tipo=='REPUESTOS':
        rows = list(RecepcionRepuesto.objects.filter(expediente=exp).order_by('pk'))
        if not rows:
            raise ValidationError('No existen recepciones de repuestos.')
        snap['registros']=[dict(id=r.pk,fecha=r.fecha.isoformat(),autor=r.autor.username,
            **{f:str(getattr(r,f)) for f in ('procedencia','entregado_por','numero_parte','descripcion','cantidad','unidad','guia','discrepancia')}) for r in rows]
        evs = EvidenciaOperativa.objects.filter(recepcion__in=rows)
    else:
        auth=exigir_autorizacion(exp)
        if estado(exp)!='REPARACION_FINALIZADA':
            raise ValidationError('Se requiere revisión independiente de todas las actividades vigentes.')
        rows = list(ActividadReparacion.objects.filter(autorizacion=auth).order_by('pk'))
        snap['autorizacion']=auth.pk
        snap['diagnostico']=auth.propuesta.diagnostico_id
        snap['registros']=[]
        for a in rows:
            r=a.resultados.order_by('-version').first()
            snap['registros'].append(dict(id=a.pk,tecnico=a.autor.username,inicio=a.inicio.isoformat(),fin=r.fin.isoformat(),
                trabajo=a.trabajo,componentes=a.componentes,trabajo_realizado=r.trabajo_realizado,resultado=r.resultado,
                observaciones=r.observaciones,version=r.version,revisor=r.revision.autor.username,revision=r.revision.motivo,
                usos=[dict(recepcion=u.recepcion_id,descripcion=u.recepcion.descripcion,cantidad=str(u.cantidad),unidad=u.recepcion.unidad) for u in a.usos.all()]))
        evs = EvidenciaOperativa.objects.filter(actividad__in=rows)
    for e in evs.select_related('documento').order_by('pk'):
        t.documento_valido(e.documento,empresa)
        snap['evidencias'].append(dict(documento=e.documento_id,hash=e.documento.hash_sha256,clase=e.clase,leyenda=e.leyenda))
    obj=t._save(EmisionOperativa(expediente=exp,autor=usuario,clave=clave,tipo=tipo,version=snap['version'],huella=t._digest(snap),snapshot=snap))
    t._event(exp,usuario,clave,'OPERACION_EMISION',payload,obj.pk)
    return obj


def generar_documento(emision_id):
    if not getattr(settings,'MASISCAM_OPERACION_LOCAL',False):
        raise PermissionDenied('Generación operativa local deshabilitada.')
    from .operacion_pdf import construir_documento
    em=EmisionOperativa.objects.get(pk=emision_id)
    if em.documento_id:
        verificar_copia_persistente(em.documento)
        return em.documento_id
    fotos=[]
    for item in em.snapshot['evidencias']:
        doc=Documento.objects.get(pk=item['documento'])
        t.documento_valido(doc,em.expediente.empresa)
        if doc.hash_sha256!=item['hash']:
            raise ValidationError('Evidencia distinta de snapshot.')
        if item['clase']!='DOCUMENTO':
            with doc.archivo.open('rb') as f:
                fotos.append((item,f.read()))
    data=construir_documento(em.snapshot,fotos)
    with transaction.atomic():
        exp=ExpedienteMantenimiento.objects.select_for_update().get(pk=em.expediente_id)
        em=EmisionOperativa.objects.select_for_update().get(pk=emision_id)
        if em.documento_id:
            verificar_copia_persistente(em.documento)
            return em.documento_id
        if em.tipo=='REPARACION' and (estado(exp)!='REPARACION_FINALIZADA' or exigir_autorizacion(exp).pk!=em.snapshot['autorizacion']):
            raise ValidationError('Fuente de reparación sustituida durante generación.')
        # An activity added during rendering invalidates this reservation.
        if em.tipo=='REPARACION' and list(ActividadReparacion.objects.filter(autorizacion_id=em.snapshot['autorizacion']).order_by('pk').values_list('pk',flat=True))!=[r['id'] for r in em.snapshot['registros']]:
            raise ValidationError('Actividades cambiaron durante generación.')
        archivo=ContentFile(data,name=f'{exp.numero}-{em.tipo.lower()}-v{em.version}.pdf')
        archivo.content_type='application/pdf'
        doc=t.guardar_archivo(usuario=em.autor,empresa=exp.empresa,proyecto=exp.registro.equipo.proyecto,
            equipo=exp.registro.equipo,archivo=archivo,categoria='INFORMES')
        em.documento,em.hash_sha256,em.estado,em.error=doc,doc.hash_sha256,'GENERADA',''
        em.save(update_fields=['documento','hash_sha256','estado','error'])
        evento(exp,em.autor,'OPERACION_DOCUMENTO_GENERADO',emision=em.pk,tipo=em.tipo,version=em.version,hash=em.hash_sha256)
        if settings.GOOGLE_DRIVE_ENABLED:
            transaction.on_commit(lambda:t._encolar_documento(doc.pk))
        return doc.pk

"""Local calibration. No bypass of commercial or industrial approvals."""
import copy
import re
import uuid
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max,Q,OuterRef,Subquery
from django.utils import timezone
from . import tecnica_services as t
from . import operacion_services as o
from .models import (PlantillaCalibracion,ValidacionPlantillaCalibracion,AplicabilidadCalibracion,
    Calibracion,MedicionCalibracion,EvidenciaCalibracion,DecisionCalibracion,EmisionCalibracion,
    ActividadReparacion,ResultadoReparacion,ExpedienteMantenimiento,Documento,Proyecto,Equipo)
from .recepcion_services import _hash,validar_fotografias,verificar_copia_persistente,evento


def autorizar(user,empresa,roles=None,exp=None):
    if not getattr(settings,'MASISCAM_CALIBRACION_LOCAL',False):
        raise PermissionDenied('Calibración habilitada únicamente en sandbox local autorizado.')
    return o.autorizar(user,empresa,roles,exp)


def expediente(user,empresa,pk,roles=None,lock=False):
    autorizar(user,empresa,roles)
    return o.expediente(user,empresa,pk,roles,lock)


def decimal_seguro(value,opcional=False):
    if value in (None,'') and opcional:
        return None
    try:
        n=Decimal(str(value))
    except (ValueError,InvalidOperation):
        raise ValidationError('Valor decimal inválido.') from None
    if not n.is_finite() or n.copy_abs()>=Decimal('1000000000000000000') or n.as_tuple().exponent < -12:
        raise ValidationError('Máximo 18 enteros y 12 decimales; no se redondea. Se admite cero y valores negativos.')
    return n


def validar_definicion(data,completa=False):
    if not isinstance(data,dict) or set(data)-{'mediciones','parametros','aplicacion'}:
        raise ValidationError('Definición: listas mediciones y parametros, sin fórmulas ejecutables.')
    aplicacion=data.get('aplicacion',{})
    if not isinstance(aplicacion,dict) or set(aplicacion)-{'marca','modelo','version_equipo','procedimiento_version'}:
        raise ValidationError('Aplicación industrial inválida.')
    for campo in ('marca','modelo','version_equipo','procedimiento_version'):
        value=aplicacion.get(campo,'')
        if not isinstance(value,str) or len(value)>200 or (completa and not value.strip()):
            raise ValidationError(f'Aplicación: {campo} documentado requerido antes de publicar.')
    for key in ('mediciones','parametros'):
        rows=data.get(key,[])
        if not isinstance(rows,list) or len(rows)>(16 if key=='mediciones' else 100):
            raise ValidationError('Lista de definiciones inválida.')
        if completa and key=='mediciones' and len(rows)!=16:
            raise ValidationError('Una plantilla validada debe definir exactamente las 16 posiciones, sin nombres predeterminados.')
        ids=set()
        for row in rows:
            allowed={'codigo','nombre','unidad','fuente','minimo','maximo','tolerancia','criterio','obligatorio'}
            if not isinstance(row,dict) or set(row)-allowed or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',str(row.get('codigo',''))) or row['codigo'] in ids:
                raise ValidationError('Código de posición/parámetro inválido o duplicado.')
            ids.add(row['codigo'])
            for field in ('nombre','unidad','fuente','tolerancia','criterio'):
                if field in row and (not isinstance(row[field],str) or len(row[field])>20000):
                    raise ValidationError('Los textos de referencia deben documentarse, no ejecutarse.')
            if not row.get('nombre','').strip():
                raise ValidationError('Nombre de punto/parámetro requerido.')
            if completa and any(not row.get(f,'').strip() for f in ('unidad','fuente')):
                raise ValidationError('La validación exige unidad y fuente técnica documentadas en cada definición.')
            if completa and (not row.get('criterio','').strip() or not row.get('tolerancia','').strip()):
                raise ValidationError('La publicación exige criterio y tolerancia documentados en cada punto/parámetro; no se presuponen.')
            if 'obligatorio' in row and not isinstance(row['obligatorio'],bool):
                raise ValidationError('Obligatoriedad debe ser explícitamente booleana.')
            if completa and key=='parametros' and 'obligatorio' not in row:
                raise ValidationError('Declare expresamente la obligatoriedad de cada parámetro antes de publicar.')
            if any(isinstance(row.get(f),(float,bool)) for f in ('minimo','maximo')):
                raise ValidationError('Escriba referencias decimales como texto exacto; no se admiten floats o booleanos.')
            lo=decimal_seguro(row.get('minimo'),True); hi=decimal_seguro(row.get('maximo'),True)
            if lo is not None and hi is not None and lo>hi:
                raise ValidationError('Mínimo mayor al máximo; no se corrigen referencias automáticamente.')
    return copy.deepcopy(data)


def huella_plantilla(p):
    return t._digest(dict(empresa=p.empresa_id,codigo=p.codigo,version=p.version,tipo=p.tipo_equipo,
        definicion=p.definicion,documento=p.documento_id,hash=p.documento.hash_sha256))


def pendientes_protocolo(p):
    """Review checklist only: never supplies industrial values or approvals."""
    pendientes=[]
    for campo in ('marca','modelo','version_equipo','procedimiento_version'):
        if not p.definicion.get('aplicacion',{}).get(campo,'').strip():
            pendientes.append(f'Aplicación: {campo} pendiente de documentación técnica.')
    if len(p.definicion.get('mediciones', [])) != 16:
        pendientes.append('Definir las 16 posiciones desde la fuente industrial.')
    for grupo in ('mediciones', 'parametros'):
        for row in p.definicion.get(grupo, []):
            nombre=f"{grupo}: {row.get('codigo', 'sin código')}"
            for campo in ('unidad', 'fuente', 'criterio'):
                if not row.get(campo, '').strip():
                    pendientes.append(f'{nombre}: {campo} pendiente de validación industrial.')
            if not row.get('tolerancia', '').strip() and row.get('minimo') in (None, '') and row.get('maximo') in (None, ''):
                pendientes.append(f'{nombre}: documentar tolerancias/límites o justificar su no aplicabilidad en el criterio.')
    if not aprobacion_plantilla(p):
        pendientes.append('Aprobación del propietario para la huella exacta pendiente.')
    if not p.aplicabilidades.exists():
        pendientes.append('Documentar aplicabilidad explícita por equipo.')
    return pendientes


def aprobacion_plantilla(p):
    for anchor in getattr(settings,'MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL',[]) or []:
        if (isinstance(anchor,dict) and anchor.get('huella')==huella_plantilla(p)
                and isinstance(anchor.get('aprobacion_propietario'),str) and anchor['aprobacion_propietario'].strip()):
            return dict(anchor)
    return None


def plantilla_validada(p,equipo=None):
    try:
        validar_definicion(p.definicion,True)
    except ValidationError:
        return False
    valid=ValidacionPlantillaCalibracion.objects.filter(plantilla=p).first()
    anchor=aprobacion_plantilla(p)
    return bool(valid and anchor and valid.aprobacion==anchor and (not equipo or (
        _equipo_compatible(p,equipo) and AplicabilidadCalibracion.objects.filter(plantilla=p,equipo=equipo).exists())))


def _equipo_compatible(p,equipo):
    app=p.definicion.get('aplicacion',{})
    return (p.tipo_equipo==equipo.tipo_producto and
        all(isinstance(app.get(c),str) and app[c].strip() and app[c].strip().casefold()==getattr(equipo,c,'').strip().casefold()
            for c in ('marca','modelo')))


@transaction.atomic
def crear_plantilla(*,usuario,empresa,clave,datos,archivo):
    autorizar(usuario,empresa,{'PLANTA'})
    t._liblock(usuario,empresa)
    o._datos(datos,('codigo','titulo','tipo_equipo','definicion','proyecto_id','sustituye_id'))
    payload=dict(datos=datos,hash=_hash(archivo) if archivo else None)
    old=t._libretry(empresa,clave,'CAL_PLANTILLA',payload)
    if old:
        return PlantillaCalibracion.objects.get(pk=old)
    if not archivo:
        raise ValidationError('Adjunte fuente técnica; no se inventa un protocolo.')
    data=dict(datos); proyecto=Proyecto.objects.filter(pk=data.pop('proyecto_id',None),empresa=empresa).first()
    if not proyecto:
        raise PermissionDenied('Proyecto de fuente no autorizado.')
    source=PlantillaCalibracion.objects.filter(pk=data.pop('sustituye_id',None),empresa=empresa).first()
    anterior=PlantillaCalibracion.objects.filter(empresa=empresa,codigo=data.get('codigo')).order_by('-version').first()
    if anterior and (not source or source.pk!=anterior.pk):
        raise ValidationError('Nueva versión exige la última plantilla fuente; nunca se sobrescribe.')
    if source and (source.codigo!=data.get('codigo') or source.tipo_equipo!=data.get('tipo_equipo')):
        raise ValidationError('Fuente y código/tipo incompatibles.')
    data['definicion']=validar_definicion(data.get('definicion'))
    doc=t.guardar_archivo(usuario=usuario,empresa=empresa,proyecto=proyecto,archivo=archivo,categoria='MANUALES')
    p=t._save(PlantillaCalibracion(empresa=empresa,autor=usuario,clave=clave,version=anterior.version+1 if anterior else 1,
        sustituye=source,documento=doc,**data))
    t._libevent(empresa,usuario,clave,'CAL_PLANTILLA',payload,p.pk)
    return p


@transaction.atomic
def validar_plantilla(*,usuario,empresa,plantilla_id,clave,fundamento):
    autorizar(usuario,empresa,{'PLANTA'}); t._liblock(usuario,empresa)
    p=PlantillaCalibracion.objects.get(pk=plantilla_id,empresa=empresa)
    payload=dict(plantilla=p.pk,fundamento=fundamento)
    old=t._libretry(empresa,clave,'CAL_PLANTILLA_VALIDADA',payload)
    if old:
        return ValidacionPlantillaCalibracion.objects.get(pk=old)
    if usuario.pk==p.autor_id:
        raise PermissionDenied('La validación de plantilla requiere responsable independiente.')
    validar_definicion(p.definicion,True)
    anchor=aprobacion_plantilla(p)
    if not anchor or not fundamento.strip():
        raise ValidationError('Plantilla/protocolo industrial pendiente de aprobación expresa; no se valida operativamente.')
    t.documento_valido(p.documento,empresa)
    obj=t._save(ValidacionPlantillaCalibracion(plantilla=p,responsable=usuario,fundamento=fundamento,aprobacion=anchor,clave=clave))
    t._libevent(empresa,usuario,clave,'CAL_PLANTILLA_VALIDADA',payload,obj.pk)
    if settings.GOOGLE_DRIVE_ENABLED:
        transaction.on_commit(lambda:t._encolar_documento(p.documento_id))
    return obj


@transaction.atomic
def aplicar_plantilla(*,usuario,empresa,plantilla_id,equipo_id,clave,fundamento,version_equipo=''):
    autorizar(usuario,empresa,{'PLANTA'}); t._liblock(usuario,empresa)
    payload=dict(plantilla=plantilla_id,equipo=equipo_id,fundamento=fundamento,version_equipo=version_equipo)
    old=t._libretry(empresa,clave,'CAL_APLICABILIDAD',payload)
    if old:
        return AplicabilidadCalibracion.objects.get(pk=old)
    p=PlantillaCalibracion.objects.get(pk=plantilla_id,empresa=empresa)
    eq=Equipo.objects.filter(pk=equipo_id,proyecto__empresa=empresa,tipo_producto=p.tipo_equipo).first()
    if not eq or not _equipo_compatible(p,eq) or not fundamento.strip():
        raise ValidationError('Equipo compatible y fundamento explícito requeridos; no se aplica por similitud de marca/modelo.')
    if not isinstance(version_equipo,str) or not version_equipo.strip() or version_equipo.strip()!=p.definicion.get('aplicacion',{}).get('version_equipo','').strip():
        raise ValidationError('Verifique y registre la versión real del equipo coincidente con la fuente; no se presume equivalencia.')
    obj=t._save(AplicabilidadCalibracion(plantilla=p,equipo=eq,autor=usuario,clave=clave,fundamento=fundamento))
    t._libevent(empresa,usuario,clave,'CAL_APLICABILIDAD',payload,obj.pk)
    return obj


def _reparacion(exp):
    auth=o.exigir_autorizacion(exp)
    if o.estado(exp)!='REPARACION_FINALIZADA':
        raise ValidationError('Calibración bloqueada: reparación vigente no finalizada/revisada independientemente.')
    acts=ActividadReparacion.objects.filter(autorizacion=auth).annotate(
        resultado_actual=Subquery(ResultadoReparacion.objects.filter(actividad_id=OuterRef('pk')).order_by('-version').values('pk')[:1]))
    return auth,[dict(actividad=pk,resultado=result) for pk,result in acts.order_by('pk').values_list('pk','resultado_actual')]


def vigente(c):
    return not Calibracion.objects.filter(expediente=c.expediente,revision__gt=c.revision).exists()


def _dependencias(c):
    auth,rep=_reparacion(c.expediente)
    if auth.pk!=c.autorizacion_id or rep!=c.reparacion_snapshot or not vigente(c):
        raise ValidationError('La fuente comercial, reparación o calibración fue sustituida; requiere revisión nueva.')
    if not plantilla_validada(c.plantilla,c.expediente.registro.equipo):
        raise ValidationError('Plantilla o aplicabilidad no validadas; operación bloqueada.')
    t.documento_valido(c.plantilla.documento,c.expediente.empresa)


@transaction.atomic
def iniciar(*,usuario,empresa,expediente_id,plantilla_id,clave,motivo=''):
    exp=expediente(usuario,empresa,expediente_id,{'TECNICO','TALLER','PLANTA'},True)
    payload=dict(plantilla=plantilla_id,motivo=motivo)
    old=t._retry(exp,clave,'CAL_INICIADA',payload)
    if old:
        return Calibracion.objects.get(pk=old)
    auth,rep=_reparacion(exp)
    p=PlantillaCalibracion.objects.filter(pk=plantilla_id,empresa=empresa).first()
    if not p or not plantilla_validada(p,exp.registro.equipo):
        raise ValidationError('Se requiere plantilla técnica exacta validada/aplicable al equipo.')
    t.documento_valido(p.documento,empresa)
    oldcal=Calibracion.objects.filter(expediente=exp).order_by('-revision').first()
    role=autorizar(usuario,empresa,exp=exp).rol
    if oldcal and (role!='PLANTA' or not motivo.strip()):
        raise PermissionDenied('PLANTA autoriza corrección con motivo; el técnico no sobrescribe revisiones previas.')
    if not oldcal and role not in t.OPERADORES:
        raise PermissionDenied('El técnico principal inicia la calibración.')
    assignment=exp.asignaciones_tecnicas.filter(fin__isnull=True).first()
    if not assignment:
        raise ValidationError('Asigne un técnico principal activo.')
    t.autorizar_tecnica(assignment.tecnico,empresa,{'TECNICO','TALLER'},exp)
    c=t._save(Calibracion(expediente=exp,diagnostico=auth.propuesta.diagnostico,autorizacion=auth,plantilla=p,
        tecnico=assignment.tecnico,reparacion_snapshot=rep,revision=oldcal.revision+1 if oldcal else 1,sustituye=oldcal,motivo_revision=motivo,clave=clave))
    # Correction starts empty: no implicit reuse of measurements or instrument
    # validity from a prior calibration; old rows remain accessible as history.
    t._event(exp,usuario,clave,'CAL_INICIADA',payload,c.pk)
    return c


def _editable(c,usuario,version):
    if c.tecnico_id!=usuario.pk or c.enviado_en:
        raise PermissionDenied('Solo el autor asignado edita borradores no enviados.')
    try:
        expected=int(version)
    except (TypeError,ValueError):
        raise ValidationError('Versión de edición inválida.') from None
    if expected!=c.version_edicion:
        raise ValidationError('Edición obsoleta; recargue antes de guardar.')
    _dependencias(c)


def _bump(c):
    c.version_edicion+=1
    c.estado='EN_PROCESO'
    c.save()


@transaction.atomic
def guardar(*,usuario,empresa,calibracion_id,version,clave,datos):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'TECNICO','TALLER'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,version=version,datos=datos)
    old=t._retry(exp,clave,'CAL_GUARDADA',payload)
    if old:
        return Calibracion.objects.get(pk=old)
    _editable(c,usuario,version)
    o._datos(datos,('fecha','instrumentos','observaciones','resultados','motivo_sin_evidencia','parametros'))
    for k,v in datos.items():
        if k=='fecha' and v is not None:
            o._fecha(v)
        elif k=='parametros':
            if not isinstance(v,dict):
                raise ValidationError('Parámetros inválidos.')
            allowed={r['codigo'] for r in c.plantilla.definicion.get('parametros',[])}
            if set(v)-allowed:
                raise ValidationError('Parámetros fuera de la plantilla aplicable.')
            v={key:(str(decimal_seguro(value,True)) if value not in (None,'') else None) for key,value in v.items()}
        elif k!='fecha' and (not isinstance(v,str) or len(v)>20000):
            raise ValidationError('Texto de calibración inválido o demasiado extenso.')
        setattr(c,k,v)
    _bump(c)
    t._event(exp,usuario,clave,'CAL_GUARDADA',payload,c.pk)
    return c


@transaction.atomic
def medir(*,usuario,empresa,calibracion_id,version,clave,punto,valor,observaciones=''):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'TECNICO','TALLER'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,version=version,punto=punto,valor=str(valor),observaciones=observaciones)
    old=t._retry(exp,clave,'CAL_MEDICION',payload)
    if old:
        return MedicionCalibracion.objects.get(pk=old)
    _editable(c,usuario,version)
    if punto not in {r['codigo'] for r in c.plantilla.definicion['mediciones']}:
        raise ValidationError('Punto inexistente en plantilla validada; no se crean posiciones arbitrarias.')
    n=decimal_seguro(valor,True)
    obj,_=MedicionCalibracion.objects.update_or_create(calibracion=c,punto=punto,defaults=dict(valor=n,observaciones=observaciones))
    # SQLite affinity/conversion can lose significant figures even when the
    # Django DecimalField scale is valid. Never report success for altered data.
    try:
        obj.refresh_from_db()
    except (InvalidOperation,OverflowError):
        raise ValidationError('Este motor no conserva la precisión indicada. No se guardó la medición; use PostgreSQL.') from None
    if obj.valor!=n:
        raise ValidationError('Este motor no conserva exactamente la precisión indicada. No se guardó la medición; use PostgreSQL para ese valor.')
    _bump(c)
    t._event(exp,usuario,clave,'CAL_MEDICION',payload,obj.pk)
    return obj


@transaction.atomic
def evidencia(*,usuario,empresa,calibracion_id,version,clave,clase,leyenda,archivo):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'TECNICO','TALLER'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,version=version,clase=clase,leyenda=leyenda,hash=_hash(archivo) if archivo else None)
    old=t._retry(exp,clave,'CAL_EVIDENCIA',payload)
    if old:
        return EvidenciaCalibracion.objects.get(pk=old)
    _editable(c,usuario,version)
    if clase not in {'FOTO','CERTIFICADO'} or not leyenda.strip() or not archivo:
        raise ValidationError('Archivo, clasificación y leyenda requeridos.')
    if clase=='FOTO':
        validar_fotografias([archivo])
    # Reuse the protected operational storage adapter, without introducing a
    # second binary repository. Only same-maintenance receipt bytes can dedup.
    doc=Documento.objects.filter(hash_sha256=_hash(archivo),evidencia_recepcion__recepcion__expediente=exp).first() if clase=='FOTO' else None
    if doc:
        t.documento_valido(doc,empresa)
    else:
        doc=t.guardar_archivo(usuario=usuario,empresa=empresa,proyecto=exp.registro.equipo.proyecto,equipo=exp.registro.equipo,
            archivo=archivo,categoria='FOTOGRAFIAS' if clase=='FOTO' else 'OTROS')
    obj=t._save(EvidenciaCalibracion(calibracion=c,documento=doc,clase=clase,leyenda=leyenda,autor=usuario,clave=clave))
    _bump(c)
    t._event(exp,usuario,clave,'CAL_EVIDENCIA',payload,obj.pk)
    if settings.GOOGLE_DRIVE_ENABLED:
        transaction.on_commit(lambda:t._encolar_documento(doc.pk))
    return obj


@transaction.atomic
def enviar(*,usuario,empresa,calibracion_id,version,clave):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'TECNICO','TALLER'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,version=version)
    old=t._retry(exp,clave,'CAL_ENVIADA',payload)
    if old:
        return Calibracion.objects.get(pk=old)
    _editable(c,usuario,version)
    if not c.fecha or not c.instrumentos.strip() or not c.resultados.strip():
        raise ValidationError('Complete fecha, identificación de instrumentos y resultados registrados.')
    definitions=c.plantilla.definicion
    measurements={m.punto:m for m in c.mediciones.all()}
    if len(measurements)!=16 or set(measurements)!={r['codigo'] for r in definitions['mediciones']} or any(m.valor is None for m in measurements.values()):
        raise ValidationError('Complete las 16 mediciones exactas de la plantilla, sin valores sustitutos.')
    for p in definitions.get('parametros',[]):
        if p.get('obligatorio',False) and c.parametros.get(p['codigo']) in (None,''):
            raise ValidationError(f'Parámetro pendiente: {p["nombre"]}.')
    if not c.evidencias.exists() and not c.motivo_sin_evidencia.strip():
        raise ValidationError('Adjunte evidencias o documente su ausencia; no se inventan fotografías.')
    for e in c.evidencias.all():
        t.documento_valido(e.documento,empresa)
    c.enviado_en=timezone.now();c.estado='PENDIENTE_REVISION';c.version_edicion+=1
    c.save()
    t._event(exp,usuario,clave,'CAL_ENVIADA',payload,c.pk)
    return c


def _autores(c):
    authors=set(); seen=set()
    while c:
        if c.pk in seen:
            raise ValidationError('Cadena de revisiones incoherente.')
        seen.add(c.pk)
        authors.add(c.tecnico_id)
        c=c.sustituye
    return authors


@transaction.atomic
def decidir(*,usuario,empresa,calibracion_id,clave,decision,motivo,evaluaciones):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'PLANTA'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,decision=decision,motivo=motivo,evaluaciones=evaluaciones)
    old=t._retry(exp,clave,'CAL_DECISION',payload)
    if old:
        return DecisionCalibracion.objects.get(pk=old)
    _dependencias(c)
    if usuario.pk in _autores(c):
        raise PermissionDenied('No puede aprobar ni revisar trabajo propio o derivado, incluso cambiando de rol.')
    if c.estado!='PENDIENTE_REVISION' or not c.enviado_en or decision not in {'APROBADA','OBSERVADA','RECHAZADA'} or not motivo.strip():
        raise ValidationError('Decisión y motivo sobre revisión enviada pendiente obligatorios.')
    refs={r['codigo']:r for r in c.plantilla.definicion['mediciones']}
    conformidad=isinstance(evaluaciones,dict) and any(v=='CONFORME' for v in evaluaciones.values())
    if decision=='APROBADA' or conformidad:
        validar_definicion(c.plantilla.definicion,True)
        for parametro in c.plantilla.definicion.get('parametros',[]):
            if parametro['obligatorio'] and c.parametros.get(parametro['codigo']) in (None,''):
                raise ValidationError('Conformidad bloqueada: parámetro obligatorio pendiente.')
    if not isinstance(evaluaciones,dict) or set(evaluaciones)-set(refs):
        raise ValidationError('Evaluaciones ajenas a la plantilla.')
    for key,value in evaluaciones.items():
        if value not in {'PENDIENTE','CONFORME','NO_CONFORME'} or (value!='PENDIENTE' and not refs[key].get('criterio','').strip()):
            raise ValidationError('Sin criterio aprobado solo se permite evaluación pendiente; no se calcula conformidad.')
    if decision=='APROBADA' and (len(evaluaciones)!=16 or any(v!='CONFORME' for v in evaluaciones.values())):
        raise ValidationError('Aprobación bloqueada: evaluación manual por PLANTA incompleta/no favorable. No se admiten excepciones implícitas.')
    obj=t._save(DecisionCalibracion(calibracion=c,decision=decision,motivo=motivo,evaluaciones=evaluaciones,responsable=usuario,clave=clave))
    c.estado=decision;c.save(update_fields=['estado'])
    t._event(exp,usuario,clave,'CAL_DECISION',payload,obj.pk)
    return obj


def snapshot(c):
    dec=DecisionCalibracion.objects.filter(calibracion=c).first()
    ms={m.punto:m for m in c.mediciones.all()}
    refs=c.plantilla.definicion
    def decimal_text(value):
        if value is None:
            return None
        raw=format(value,'f')
        return raw.rstrip('0').rstrip('.') if '.' in raw else raw
    return dict(plantilla='calibracion-v1',numero=c.expediente.numero,recepcion=c.expediente.recepcion.snapshot,
        calibracion=c.pk,revision=c.revision,diagnostico=c.diagnostico_id,autorizacion=c.autorizacion_id,
        reparacion=c.reparacion_snapshot,tipo_equipo=c.plantilla.tipo_equipo,protocolo=c.plantilla.codigo,
        protocolo_version=c.plantilla.version,protocolo_huella=huella_plantilla(c.plantilla),
        tecnico=c.tecnico.username,fecha=c.fecha.isoformat() if c.fecha else '',instrumentos=c.instrumentos,
        parametros=[dict(**p,valor=c.parametros.get(p['codigo'])) for p in refs.get('parametros',[])],
        observaciones=c.observaciones,resultados=c.resultados,estado=c.estado,
        decision=dict(id=dec.pk,responsable=dec.responsable.username,fecha=dec.creado_en.isoformat(),motivo=dec.motivo) if dec else None,
        mediciones=[dict(**r,valor=decimal_text(ms[r['codigo']].valor) if r['codigo'] in ms else None,
            observaciones=ms[r['codigo']].observaciones if r['codigo'] in ms else '',evaluacion=dec.evaluaciones.get(r['codigo'],'PENDIENTE') if dec else 'PENDIENTE') for r in refs['mediciones']],
        evidencias=[dict(documento=e.documento_id,hash=e.documento.hash_sha256,clase=e.clase,leyenda=e.leyenda) for e in c.evidencias.select_related('documento').order_by('pk')])


@transaction.atomic
def reservar_informe(*,usuario,empresa,calibracion_id,clave,nueva_version=False,motivo=''):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'OFICINA','PLANTA','TECNICO','TALLER'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,nueva_version=bool(nueva_version),motivo=motivo)
    old=t._retry(exp,clave,'CAL_INFORME',payload)
    if old:
        return EmisionCalibracion.objects.get(pk=old)
    _dependencias(c)
    if not c.enviado_en:
        raise ValidationError('Envíe la revisión; no se emite un borrador como calibración realizada.')
    snap=snapshot(c)
    prev=c.emisiones.order_by('-version').first()
    huella=t._digest(snap)
    if prev and not nueva_version:
        if prev.huella!=huella:
            raise ValidationError('Cambió el estado de revisión: solicite nueva versión con motivo.')
        t._event(exp,usuario,clave,'CAL_INFORME',payload,prev.pk)
        return prev
    if nueva_version and not motivo.strip():
        raise ValidationError('Nueva emisión requiere motivo.')
    obj=t._save(EmisionCalibracion(calibracion=c,version=prev.version+1 if prev else 1,snapshot=snap,huella=huella,autor=usuario,clave=clave))
    t._event(exp,usuario,clave,'CAL_INFORME',payload,obj.pk)
    return obj


def generar_informe(emision_id):
    if not getattr(settings,'MASISCAM_CALIBRACION_LOCAL',False):
        raise PermissionDenied('Generación de calibración local deshabilitada.')
    from .calibracion_pdf import construir_informe
    em=EmisionCalibracion.objects.get(pk=emision_id)
    if em.documento_id:
        verificar_copia_persistente(em.documento);return em.documento_id
    fotos=[]
    for item in em.snapshot['evidencias']:
        doc=Documento.objects.get(pk=item['documento'])
        t.documento_valido(doc,em.calibracion.expediente.empresa)
        if doc.hash_sha256!=item['hash']:
            raise ValidationError('Evidencia distinta del snapshot.')
        if item['clase']=='FOTO':
            with doc.archivo.open('rb') as f:
                fotos.append((item,f.read()))
    data=construir_informe(dict(em.snapshot,version=em.version),fotos)
    with transaction.atomic():
        exp=ExpedienteMantenimiento.objects.select_for_update().get(pk=em.calibracion.expediente_id)
        em=EmisionCalibracion.objects.select_for_update().get(pk=emision_id)
        if em.documento_id:
            verificar_copia_persistente(em.documento);return em.documento_id
        c=Calibracion.objects.get(pk=em.calibracion_id)
        _dependencias(c)
        if t._digest(snapshot(c))!=em.huella:
            raise ValidationError('La revisión cambió durante render; no se publica como vigente.')
        archivo=ContentFile(data,name=f'{exp.numero}-cal-r{c.revision}-v{em.version}.pdf');archivo.content_type='application/pdf'
        doc=t.guardar_archivo(usuario=em.autor,empresa=exp.empresa,proyecto=exp.registro.equipo.proyecto,equipo=exp.registro.equipo,archivo=archivo,categoria='INFORMES')
        em.documento,em.hash_sha256,em.estado,em.error=doc,doc.hash_sha256,'GENERADA',''
        em.save(update_fields=['documento','hash_sha256','estado','error'])
        evento(exp,em.autor,'CAL_PDF_GENERADO',emision=em.pk,revision=c.revision,version=em.version,hash=em.hash_sha256)
        if settings.GOOGLE_DRIVE_ENABLED:
            transaction.on_commit(lambda:t._encolar_documento(doc.pk))
        return doc.pk

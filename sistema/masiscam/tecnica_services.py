"""Local phase 3 services. Every write is authorized and serialized in server."""
import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from accounts.models import Empresa
from .access import acceso_cliente_usuario, rol_masiscam_usuario
from .models import (AsignacionTecnica, Auditoria, ConfirmacionRecepcion, Documento, Equipo,
    ExpedienteMantenimiento, EtapaExpediente, Inspeccion, Diagnostico, EvidenciaTecnica,
    DecisionTecnica, ReferenciaTecnica, ComponenteTecnico, ParteReferenciaTecnica,
    AplicabilidadReferenciaEquipo, PiezaRequerida, EmisionInformeTecnico)
from .recepcion_services import evento, verificar_copia_persistente, validar_fotografias, _hash

LECTORES = {'TECNICO', 'PLANTA', 'OFICINA', 'GERENCIA', 'ADMIN', 'CONSULTA'}
INS_FIELDS = ('fecha','estado_fisico','condiciones_generales','hallazgos','observaciones','motivo_sin_evidencia')
DIA_FIELDS = ('fecha','fallas','causas','componentes_afectados','recomendaciones','conclusiones','observaciones','motivo_sin_evidencia','repuestos')


def autorizar_tecnica(usuario, empresa, roles=None, expediente=None):
    if not getattr(settings, 'MASISCAM_TECNICA_LOCAL', False):
        raise PermissionDenied('Fase 3 solo está habilitada en el entorno local autorizado.')
    if not usuario.is_authenticated or not usuario.is_active or not empresa or not empresa.activa or acceso_cliente_usuario(usuario):
        raise PermissionDenied('Operación técnica no autorizada.')
    rol = rol_masiscam_usuario(usuario=usuario, empresa=empresa)
    if not rol or rol.rol not in (roles or LECTORES):
        raise PermissionDenied('El rol no permite esta operación técnica.')
    if expediente:
        if expediente.empresa_id != empresa.pk or expediente.registro.equipo.proyecto.empresa_id != empresa.pk:
            raise PermissionDenied('Expediente de otra empresa.')
        if rol.rol == 'TECNICO' and not AsignacionTecnica.objects.filter(expediente=expediente, tecnico=usuario, fin__isnull=True).exists():
            raise PermissionDenied('Se requiere asignación técnica vigente.')
    return rol


def expediente_autorizado(usuario, empresa, pk, roles=None, lock=False):
    autorizar_tecnica(usuario, empresa, roles)
    qs = ExpedienteMantenimiento.objects.all()
    if lock:
        qs = qs.select_for_update()
    exp = qs.filter(pk=pk, empresa=empresa).first()
    if not exp:
        raise PermissionDenied('Expediente no disponible.')
    autorizar_tecnica(usuario, empresa, roles, exp)
    if lock:
        from .cierre_services import abierto
        abierto(exp)
    return exp


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _retry(exp, clave, accion, payload):
    try:
        clave = str(uuid.UUID(str(clave)))
    except (ValueError, TypeError):
        raise ValidationError('Clave idempotente inválida.') from None
    old = Auditoria.objects.filter(expediente=exp, detalle__clave=clave).first()
    if old:
        if old.accion != accion or old.detalle.get('huella') != _digest(payload):
            raise ValidationError('Conflicto: clave utilizada con otra operación o contenido.')
        return old.detalle['resultado']


def _event(exp, user, key, action, payload, result):
    evento(exp, user, action, clave=str(key), huella=_digest(payload), resultado=result)
    return result


def _save(obj):
    obj.full_clean()
    obj.save()
    return obj


@transaction.atomic
def asignar_tecnico(*, usuario, empresa, expediente_id, tecnico_id, motivo, clave):
    exp = expediente_autorizado(usuario, empresa, expediente_id, {'PLANTA'}, True)
    payload = {'tecnico':int(tecnico_id), 'motivo':motivo.strip()}
    old = _retry(exp, clave, 'TECNICO_ASIGNADO', payload)
    if old:
        return AsignacionTecnica.objects.get(pk=old)
    if not motivo.strip():
        raise ValidationError('Indique motivo de asignación o reasignación.')
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=tecnico_id, is_active=True).first()
    rol = rol_masiscam_usuario(usuario=user, empresa=empresa) if user else None
    if not rol or rol.rol != 'TECNICO' or acceso_cliente_usuario(user):
        raise ValidationError('El responsable debe ser TECNICO activo de la empresa.')
    actual = exp.asignaciones_tecnicas.filter(fin__isnull=True).first()
    if actual and actual.tecnico_id == user.pk:
        result = actual
    else:
        if actual:
            actual.fin = timezone.now()
            actual.save(update_fields=['fin'])
        result = _save(AsignacionTecnica(expediente=exp, tecnico=user, asignado_por=usuario, motivo=motivo.strip(), clave=clave))
    _event(exp, usuario, clave, 'TECNICO_ASIGNADO', payload, result.pk)
    return result


def _etapa(exp, kind):
    return EtapaExpediente.objects.filter(expediente=exp, codigo=kind).first()


def vigente(obj):
    kind = 'INSPECCION' if isinstance(obj, Inspeccion) else 'DIAGNOSTICO'
    etapa = _etapa(obj.expediente, kind)
    return bool(etapa and getattr(etapa, kind.lower()+'_id') == obj.pk and
        (kind == 'INSPECCION' or vigente(obj.inspeccion)))


@transaction.atomic
def crear_revision(*, usuario, empresa, expediente_id, tipo, clave, motivo=''):
    if tipo not in {'INSPECCION', 'DIAGNOSTICO'}:
        raise ValidationError('Tipo técnico inválido.')
    exp = expediente_autorizado(usuario, empresa, expediente_id, {'TECNICO','PLANTA'}, True)
    payload = dict(tipo=tipo, motivo=motivo.strip())
    action = tipo+'_CREADA'
    old = _retry(exp, clave, action, payload)
    model = Inspeccion if tipo == 'INSPECCION' else Diagnostico
    if old:
        return model.objects.get(pk=old)
    recepcion = ConfirmacionRecepcion.objects.filter(recepcion__expediente=exp).select_related('guia__documento').first()
    if not recepcion:
        raise ValidationError('Confirme la recepción antes de iniciar inspección.')
    verificar_copia_persistente(recepcion.guia.documento)
    asignacion = exp.asignaciones_tecnicas.filter(fin__isnull=True).first()
    if not asignacion:
        raise ValidationError('PLANTA debe asignar un técnico principal.')
    etapa = _etapa(exp, tipo)
    anterior = getattr(etapa, tipo.lower()) if etapa else None
    role = rol_masiscam_usuario(usuario=usuario, empresa=empresa).rol
    if anterior:
        if role != 'PLANTA' or not motivo.strip():
            raise PermissionDenied('PLANTA debe autorizar una revisión nueva con motivo; el autor puede continuar su borrador.')
    elif role != 'TECNICO' or asignacion.tecnico_id != usuario.pk:
        raise PermissionDenied('El técnico asignado inicia la primera revisión.')
    data = dict(expediente=exp, tecnico=asignacion.tecnico, revision=(model.objects.filter(expediente=exp).aggregate(n=Max('revision'))['n'] or 0)+1,
                sustituye=anterior, motivo_revision=motivo.strip())
    if anterior:
        for field in (INS_FIELDS if tipo == 'INSPECCION' else DIA_FIELDS):
            data[field] = getattr(anterior, field)
    if tipo == 'DIAGNOSTICO':
        source = _etapa(exp, 'INSPECCION')
        if not source or not source.inspeccion.enviado_en:
            raise ValidationError('Envíe primero la inspección vigente.')
        data['inspeccion'] = source.inspeccion
    obj = _save(model(**data))
    if anterior:
        piezas_copiadas={}
        if tipo == 'DIAGNOSTICO':
            for pieza in anterior.piezas.all():
                values = {f.name:getattr(pieza,f.name) for f in PiezaRequerida._meta.fields if f.name not in {'id','diagnostico'}}
                piezas_copiadas[pieza.pk]=PiezaRequerida.objects.create(diagnostico=obj, **values)
        for e in anterior.evidencias.all():
            EvidenciaTecnica.objects.create(**{tipo.lower():obj}, pieza=piezas_copiadas.get(e.pieza_id), documento=e.documento, referencia=e.referencia, clase=e.clase, leyenda=e.leyenda, autor=e.autor)
    field = tipo.lower()
    EtapaExpediente.objects.update_or_create(expediente=exp, codigo=tipo,
        defaults={field:obj,'estado':'EN_PROCESO','version':(etapa.version+1 if etapa else 1)})
    _event(exp, usuario, clave, action, payload, obj.pk)
    return obj


def _editable(usuario, empresa, obj, version):
    autorizar_tecnica(usuario, empresa, {'TECNICO'}, obj.expediente)
    if obj.tecnico_id != usuario.pk or obj.enviado_en or not vigente(obj):
        raise PermissionDenied('Solo el autor asignado edita su borrador vigente.')
    try:
        version=int(version)
    except (ValueError,TypeError):
        raise ValidationError('Indique una versión de edición válida.') from None
    if obj.version_edicion != version:
        raise ValidationError('Conflicto de edición: recargue la revisión.')


@transaction.atomic
def guardar_revision(*, usuario, empresa, tipo, revision_id, version, clave, datos):
    model = Inspeccion if tipo == 'INSPECCION' else Diagnostico if tipo == 'DIAGNOSTICO' else None
    if not model:
        raise ValidationError('Tipo inválido.')
    obj = model.objects.get(pk=revision_id)
    exp = expediente_autorizado(usuario, empresa, obj.expediente_id, {'TECNICO'}, True)
    obj.refresh_from_db()
    allowed = INS_FIELDS if model is Inspeccion else DIA_FIELDS
    if set(datos) - set(allowed):
        raise ValidationError('Campos técnicos no permitidos.')
    payload = dict(tipo=tipo, id=obj.pk, version=version, datos=datos)
    old = _retry(exp, clave, 'REVISION_GUARDADA', payload)
    if old:
        return model.objects.get(pk=old)
    _editable(usuario, empresa, obj, version)
    for name, value in datos.items():
        if isinstance(value,str) and len(value)>20000:
            raise ValidationError('Texto demasiado largo.')
        setattr(obj, name, value.strip() if isinstance(value,str) else value)
    obj.version_edicion += 1
    _save(obj)
    _event(exp, usuario, clave, 'REVISION_GUARDADA', payload, obj.pk)
    return obj


def _evidencias(obj):
    qs = list(obj.evidencias.select_related('documento').order_by('pk'))
    if isinstance(obj, Diagnostico):
        qs = list(obj.inspeccion.evidencias.select_related('documento').order_by('pk')) + qs
    return qs


def documento_valido(doc, empresa):
    if doc.proyecto.empresa_id != empresa.pk or doc.publico or doc.estado_sincronizacion == 'ARCHIVADO':
        raise PermissionDenied('Documento privado fuera del ámbito o archivado.')
    verificar_copia_persistente(doc)


def guardar_archivo(*, usuario, empresa, proyecto, archivo, categoria, equipo=None):
    if proyecto.empresa_id != empresa.pk:
        raise PermissionDenied('Proyecto de otra empresa.')
    from .forms import DocumentoForm
    validator = DocumentoForm(files={'archivo':archivo})
    validator.cleaned_data={'archivo':archivo}
    validator.clean_archivo()
    digest = _hash(archivo)
    existing = Documento.objects.filter(proyecto=proyecto, hash_sha256=digest).exclude(estado_sincronizacion='ARCHIVADO').first()
    if existing:
        # Reuse only documents already under technical control. Legacy/public
        # documents require explicit admission through biblioteca, not silently.
        if not (existing.evidencias_tecnicas.exists() or existing.referencias_tecnicas.exists()
                or existing.evidencias_operativas.exists() or existing.plantillas_calibracion.exists()
                or existing.evidencias_calibracion.exists()):
            raise ValidationError('Documento existente: incorpórelo mediante selección autorizada, sin duplicarlo.')
        documento_valido(existing, empresa)
        return existing
    doc = Documento(proyecto=proyecto,equipo=equipo,categoria=categoria,titulo=archivo.name[:200],
        nombre_original=archivo.name[:255], tipo_mime=archivo.content_type,tamano=archivo.size,
        hash_sha256=digest,subido_por=usuario,publico=False)
    from pathlib import Path
    doc.archivo.save(f'tecnica-{uuid.uuid4().hex}{Path(archivo.name).suffix.lower()}',archivo,save=False)
    verificar_copia_persistente(doc)
    doc.save()
    return doc


@transaction.atomic
def agregar_evidencia(*,usuario,empresa,tipo,revision_id,version,clave,clase,leyenda,archivo=None,documento_id=None,referencia_id=None):
    model = Inspeccion if tipo=='INSPECCION' else Diagnostico
    obj=model.objects.get(pk=revision_id)
    exp=expediente_autorizado(usuario,empresa,obj.expediente_id,{'TECNICO'},True)
    obj.refresh_from_db()
    payload=dict(tipo=tipo,id=obj.pk,version=version,clase=clase,leyenda=leyenda,documento=documento_id,referencia=referencia_id,hash=_hash(archivo) if archivo else None)
    old=_retry(exp,clave,'EVIDENCIA_TECNICA_AGREGADA',payload)
    if old:
        return EvidenciaTecnica.objects.get(pk=old)
    _editable(usuario,empresa,obj,version)
    if not leyenda.strip() or clase not in dict(EvidenciaTecnica._meta.get_field('clase').choices) or sum(bool(v) for v in (archivo,documento_id,referencia_id))!=1:
        raise ValidationError('Seleccione una fotografía o fuente, clasificación y leyenda.')
    ref=None
    if referencia_id:
        ref=referencias_visibles(usuario,empresa).filter(pk=referencia_id,estado='PUBLICADA',aplicabilidades__equipo=exp.registro.equipo,aplicabilidades__activa=True).first()
        if not ref or clase!='REFERENCIA':
            raise PermissionDenied('Seleccione una referencia publicada/aplicable con clasificación REFERENCIA.')
        doc=ref.documento
        documento_valido(doc,empresa)
    elif clase=='REFERENCIA':
        raise ValidationError('La clasificación REFERENCIA exige versión explícita de biblioteca.')
    elif archivo:
        validar_fotografias([archivo])
        doc=guardar_archivo(usuario=usuario,empresa=empresa,proyecto=exp.registro.equipo.proyecto,equipo=exp.registro.equipo,archivo=archivo,categoria='FOTOGRAFIAS')
    else:
        # Only evidence already belonging to this same maintenance, not a
        # project-wide arbitrary document ID or another maintenance's photo.
        doc=Documento.objects.filter(pk=documento_id).filter(
            Q(evidencia_recepcion__recepcion__expediente=exp)|Q(evidencias_tecnicas__inspeccion__expediente=exp)|Q(evidencias_tecnicas__diagnostico__expediente=exp)).distinct().first()
        if not doc:
            raise PermissionDenied('Evidencia de otro expediente.')
        documento_valido(doc,empresa)
        from django.core.files.uploadedfile import SimpleUploadedFile
        with doc.archivo.open('rb') as f:
            validar_fotografias([SimpleUploadedFile(doc.nombre_original,f.read(),content_type=doc.tipo_mime)])
    result=_save(EvidenciaTecnica(**{tipo.lower():obj},documento=doc,referencia=ref,clase=clase,leyenda=leyenda.strip(),autor=usuario))
    obj.version_edicion+=1
    obj.save(update_fields=['version_edicion'])
    _event(exp,usuario,clave,'EVIDENCIA_TECNICA_AGREGADA',payload,result.pk)
    return result


@transaction.atomic
def quitar_hijo(*,usuario,empresa,tipo,revision_id,version,clave,hijo_id,pieza=False):
    model=Inspeccion if tipo=='INSPECCION' else Diagnostico
    obj=model.objects.get(pk=revision_id)
    exp=expediente_autorizado(usuario,empresa,obj.expediente_id,{'TECNICO'},True)
    obj.refresh_from_db()
    payload=dict(tipo=tipo,id=obj.pk,version=version,hijo=hijo_id,pieza=pieza)
    old=_retry(exp,clave,'DETALLE_TECNICO_RETIRADO',payload)
    if old:
        return old
    _editable(usuario,empresa,obj,version)
    manager=obj.piezas if pieza and isinstance(obj,Diagnostico) else obj.evidencias
    if pieza and isinstance(obj,Diagnostico):
        manager.get(pk=hijo_id).fotografias.update(pieza=None)
    manager.get(pk=hijo_id).delete()  # Only draft relation, never deletes Documento.
    obj.version_edicion+=1
    obj.save(update_fields=['version_edicion'])
    return _event(exp,usuario,clave,'DETALLE_TECNICO_RETIRADO',payload,int(hijo_id))


@transaction.atomic
def enviar_revision(*,usuario,empresa,tipo,revision_id,version,clave):
    model=Inspeccion if tipo=='INSPECCION' else Diagnostico
    obj=model.objects.get(pk=revision_id)
    exp=expediente_autorizado(usuario,empresa,obj.expediente_id,{'TECNICO'},True)
    obj.refresh_from_db()
    payload=dict(tipo=tipo,id=obj.pk,version=version)
    old=_retry(exp,clave,'REVISION_ENVIADA',payload)
    if old:
        return model.objects.get(pk=old)
    _editable(usuario,empresa,obj,version)
    fields=('estado_fisico','condiciones_generales','hallazgos') if model is Inspeccion else ('fallas','causas','componentes_afectados','recomendaciones','conclusiones')
    errors={field:'Campo obligatorio o declaración explícita de información pendiente.' for field in fields if not getattr(obj,field).strip()}
    if not obj.fecha:
        errors['fecha']='Indique fecha efectiva.'
    evs=_evidencias(obj)
    if not evs and not obj.motivo_sin_evidencia.strip():
        errors['motivo_sin_evidencia']='Adjunte evidencia o documente su ausencia.'
    for e in evs:
        documento_valido(e.documento,empresa)
        if e.referencia_id and (e.referencia.estado!='PUBLICADA' or not AplicabilidadReferenciaEquipo.objects.filter(referencia=e.referencia,equipo=exp.registro.equipo,activa=True).exists()):
            raise ValidationError('Una referencia citada dejó de estar publicada o aplicable; revise el borrador.')
    if model is Diagnostico:
        if obj.repuestos not in {'REQUERIDOS','SIN_REPUESTOS'} or (obj.repuestos=='REQUERIDOS') != obj.piezas.exists():
            errors['repuestos']='Registre piezas requeridas o declare que no se requieren; la lista debe coincidir.'
        for p in obj.piezas.all():
            _validar_pieza(p)
            if p.parte_id and not _parte_aplicable(p.parte,exp):
                raise ValidationError('La referencia de una pieza ya no está publicada o aplicable.')
    if errors:
        raise ValidationError(errors)
    obj.enviado_en=timezone.now()
    obj.estado='FINALIZADO' if model is Inspeccion else 'PENDIENTE_APROBACION'
    obj.version_edicion+=1
    obj.save(update_fields=['enviado_en','estado','version_edicion'])
    etapa=_etapa(exp,tipo)
    etapa.estado=obj.estado
    etapa.version+=1
    etapa.save()
    _event(exp,usuario,clave,'REVISION_ENVIADA',payload,obj.pk)
    return obj


def _autores(obj):
    result=set()
    visited=set()
    while obj:
        key=(obj._meta.label,obj.pk)
        if key in visited:
            raise ValidationError('Procedencia cíclica de revisiones.')
        visited.add(key)
        result.add(obj.tecnico_id)
        if isinstance(obj,Diagnostico):
            result.update(_autores(obj.inspeccion))
        obj=obj.sustituye
    return result


@transaction.atomic
def decidir_diagnostico(*,usuario,empresa,diagnostico_id,decision,motivo,clave):
    obj=Diagnostico.objects.get(pk=diagnostico_id)
    exp=expediente_autorizado(usuario,empresa,obj.expediente_id,{'PLANTA'},True)
    obj.refresh_from_db()
    payload=dict(id=obj.pk,decision=decision,motivo=motivo.strip())
    old=_retry(exp,clave,'DIAGNOSTICO_DECIDIDO',payload)
    if old:
        return DecisionTecnica.objects.get(pk=old)
    if not vigente(obj) or obj.estado!='PENDIENTE_APROBACION' or not obj.enviado_en:
        raise ValidationError('Diagnóstico no vigente o fuera de revisión.')
    if usuario.pk in _autores(obj):
        raise PermissionDenied('No puede aprobar, observar ni rechazar su propio trabajo o sus fuentes.')
    if decision not in {'APROBADO','OBSERVADO','RECHAZADO'} or (decision!='APROBADO' and not motivo.strip()):
        raise ValidationError('Decisión inválida; observar o rechazar exige motivo.')
    result=_save(DecisionTecnica(diagnostico=obj,solicitud=clave,decision=decision,motivo=motivo.strip(),responsable=usuario))
    obj.estado=decision
    obj.save(update_fields=['estado'])
    etapa=_etapa(exp,'DIAGNOSTICO')
    etapa.estado=decision
    etapa.version+=1
    etapa.save()
    _event(exp,usuario,clave,'DIAGNOSTICO_DECIDIDO',payload,result.pk)
    return result


def referencias_visibles(usuario,empresa):
    rol=autorizar_tecnica(usuario,empresa)
    qs=ReferenciaTecnica.objects.filter(empresa=empresa)
    if rol.rol!='PLANTA':
        qs=qs.exclude(estado='BORRADOR')
    if rol.rol=='TECNICO':
        equipos=AsignacionTecnica.objects.filter(tecnico=usuario,fin__isnull=True,expediente__empresa=empresa).values('expediente__registro__equipo_id')
        # Retired historical sources remain available only if cited in an
        # assigned maintenance, not simply because an old applicability exists.
        qs=qs.filter(Q(estado='PUBLICADA',aplicabilidades__activa=True,aplicabilidades__equipo_id__in=equipos)|
            Q(partes__piezarequerida__diagnostico__expediente__asignaciones_tecnicas__tecnico=usuario,
              partes__piezarequerida__diagnostico__expediente__asignaciones_tecnicas__fin__isnull=True)|
            Q(citas_tecnicas__diagnostico__expediente__asignaciones_tecnicas__tecnico=usuario,citas_tecnicas__diagnostico__expediente__asignaciones_tecnicas__fin__isnull=True)|
            Q(citas_tecnicas__inspeccion__expediente__asignaciones_tecnicas__tecnico=usuario,citas_tecnicas__inspeccion__expediente__asignaciones_tecnicas__fin__isnull=True))
    return qs.distinct()


def _parte_aplicable(parte,exp):
    return parte.referencia.empresa_id==exp.empresa_id and parte.referencia.estado=='PUBLICADA' and parte.referencia.tipo=='PLANO' and AplicabilidadReferenciaEquipo.objects.filter(referencia=parte.referencia,equipo=exp.registro.equipo,activa=True).exists()


def _validar_pieza(p):
    try:
        value=Decimal(str(p.cantidad))
    except (InvalidOperation,ValueError):
        raise ValidationError('Cantidad decimal inválida.') from None
    if not value.is_finite() or value<=0 or value.as_tuple().exponent < -6 or value>=Decimal('100000000000000'):
        raise ValidationError('Cantidad positiva, máximo 14 enteros y seis decimales; no se redondea.')
    for name in ('descripcion','condicion','accion'):
        if not getattr(p,name).strip():
            raise ValidationError(f'Indique {name} de la pieza.')
    if not p.unidad.strip() and not p.motivo_unidad_pendiente.strip():
        raise ValidationError('Indique unidad real o motivo de unidad pendiente.')
    if p.origen=='MANUAL' and not p.motivo_manual.strip():
        raise ValidationError('Documente el contexto de registro manual.')


@transaction.atomic
def agregar_pieza(*,usuario,empresa,diagnostico_id,version,clave,datos,pieza_id=None):
    obj=Diagnostico.objects.get(pk=diagnostico_id)
    exp=expediente_autorizado(usuario,empresa,obj.expediente_id,{'TECNICO'},True)
    obj.refresh_from_db()
    payload=dict(id=obj.pk,version=version,datos=datos)
    action='PIEZA_REQUERIDA_EDITADA' if pieza_id else 'PIEZA_REQUERIDA_AGREGADA'
    if pieza_id:
        payload['pieza']=pieza_id
    old=_retry(exp,clave,action,payload)
    if old:
        return PiezaRequerida.objects.get(pk=old)
    _editable(usuario,empresa,obj,version)
    allowed={'origen','parte_id','descripcion','numero_parte','cantidad','unidad','motivo_unidad_pendiente','condicion','accion','motivo_manual','posicion_plano_id','observaciones'}
    if set(datos)-allowed:
        raise ValidationError('Campos de repuesto no permitidos.')
    if pieza_id:
        p=obj.piezas.filter(pk=pieza_id).first()
        if not p:
            raise PermissionDenied('Pieza de otro diagnóstico.')
        if datos.get('origen', p.origen)!=p.origen or datos.get('parte_id', p.parte_id)!=p.parte_id:
            raise ValidationError('Para cambiar de componente retire la pieza del borrador y seleccione una nueva.')
        for field,value in datos.items():
            setattr(p,field,value)
    else:
        p=PiezaRequerida(diagnostico=obj,**datos)
    if p.posicion_plano_id:
        from .models import PosicionPlano
        punto=PosicionPlano.objects.select_related('mapa').filter(pk=p.posicion_plano_id,parte_id=p.parte_id).first()
        if not punto or punto.mapa.referencia_id!=p.parte.referencia_id or punto.mapa.hash_documento!=p.parte.referencia.documento.hash_sha256:
            raise ValidationError('La posición no corresponde a la parte y versión documental seleccionadas.')
    if p.origen=='BIBLIOTECA':
        if not p.parte_id or not _parte_aplicable(p.parte,exp):
            raise PermissionDenied('Parte no publicada, no aplicable o de otra empresa.')
        p.descripcion,p.numero_parte=p.parte.descripcion,p.parte.numero_parte
        p.unidad=p.parte.unidad or p.unidad
        p.snapshot=dict(referencia=p.parte.referencia.codigo,revision=p.parte.referencia.revision,
            posicion=p.parte.posicion,documento=p.parte.referencia.documento_id,
            hash=p.parte.referencia.documento.hash_sha256,version_fabricante=p.parte.referencia.version_fabricante)
        documento_valido(p.parte.referencia.documento,empresa)
        if p.posicion_plano_id:
            p.snapshot['plano']=dict(posicion_id=punto.pk,mapa_id=punto.mapa_id,version=punto.mapa.version,
                pagina=punto.pagina,x=str(punto.x),y=str(punto.y),numero=punto.numero)
    else:
        p.snapshot=dict(origen='MANUAL',contexto=p.motivo_manual,
            identificacion_pendiente=not bool(p.numero_parte),unidad_pendiente=not bool(p.unidad))
    _validar_pieza(p)
    _save(p)
    obj.version_edicion+=1
    obj.save(update_fields=['version_edicion'])
    _event(exp,usuario,clave,action,payload,p.pk)
    return p


def _liblock(usuario,empresa):
    autorizar_tecnica(usuario,empresa,{'PLANTA'})
    Empresa.objects.select_for_update().get(pk=empresa.pk,activa=True)


def _libretry(empresa,clave,accion,payload):
    try:
        key=str(uuid.UUID(str(clave)))
    except (ValueError,TypeError):
        raise ValidationError('Clave inválida.') from None
    old=Auditoria.objects.filter(empresa=empresa,objeto_tipo='BibliotecaTecnica',detalle__clave=key).first()
    if old:
        if old.accion!=accion or old.detalle['huella']!=_digest(payload):
            raise ValidationError('Conflicto idempotente de biblioteca.')
        return old.detalle['resultado']


def _libevent(empresa,usuario,clave,accion,payload,result):
    Auditoria.objects.create(empresa=empresa,usuario=usuario,accion=accion,objeto_tipo='BibliotecaTecnica',
        objeto_id=str(result),detalle=dict(clave=str(clave),huella=_digest(payload),resultado=result))


def _encolar_documento(doc_id):
    from .services import encolar_drive
    from .tasks import sincronizar_documento
    encolar_drive(sincronizar_documento,doc_id)


@transaction.atomic
def guardar_referencia(*,usuario,empresa,clave,datos,archivo=None,referencia_id=None):
    _liblock(usuario,empresa)
    payload=dict(id=referencia_id,datos=datos,hash=_hash(archivo) if archivo else None)
    old=_libretry(empresa,clave,'BIBLIOTECA_REFERENCIA',payload)
    if old:
        return ReferenciaTecnica.objects.get(pk=old)
    data=dict(datos)
    proyecto_id=data.pop('proyecto_id',None)
    doc_id=data.pop('documento_id',None)
    source_id=data.pop('sustituye_id',None)
    allowed={'codigo','tipo','titulo','marca','modelo','ratio','version_fabricante','procedencia','autorizacion_uso'}
    if set(data)-allowed or not data.get('autorizacion_uso','').strip():
        raise ValidationError('Registre los datos permitidos y la autorización de uso/congelación del documento privado.')
    obj=ReferenciaTecnica.objects.filter(pk=referencia_id,empresa=empresa).first() if referencia_id else None
    if referencia_id and (not obj or obj.estado!='BORRADOR'):
        raise PermissionDenied('Solo se edita una referencia borrador propia.')
    if archivo:
        from .models import Proyecto
        proyecto=Proyecto.objects.filter(pk=proyecto_id,empresa=empresa).first()
        if not proyecto or doc_id:
            raise ValidationError('Seleccione proyecto real para el archivo nuevo, o documento existente.')
        doc=guardar_archivo(usuario=usuario,empresa=empresa,proyecto=proyecto,archivo=archivo,
            categoria='PLANOS' if data.get('tipo')=='PLANO' else 'MANUALES')
    else:
        doc=Documento.objects.filter(pk=doc_id,proyecto__empresa=empresa).first()
        if not doc:
            raise PermissionDenied('Documento no autorizado.')
        documento_valido(doc,empresa)
    if doc.categoria != ('PLANOS' if data.get('tipo')=='PLANO' else 'MANUALES'):
        raise ValidationError('Categoría documental incompatible con plano/manual.')
    # Private explicit admission is required; never changes legacy visibility.
    source=ReferenciaTecnica.objects.filter(pk=source_id,empresa=empresa).first() if source_id else None
    if source_id and (not source or source.codigo!=data.get('codigo')):
        raise ValidationError('Versión fuente de otra referencia/empresa.')
    if obj:
        if obj.codigo!=data.get('codigo'):
            raise ValidationError('El código interno no cambia al editar.')
        for name,value in data.items():
            setattr(obj,name,value.strip())
        obj.documento=doc
    else:
        n=ReferenciaTecnica.objects.filter(empresa=empresa,codigo=data.get('codigo')).aggregate(n=Max('revision'))['n'] or 0
        if n and not source:
            raise ValidationError('Seleccione la versión fuente para crear otra revisión del código.')
        obj=ReferenciaTecnica(empresa=empresa,documento=doc,autor=usuario,revision=n+1,sustituye=source,**data)
    _save(obj)
    if source and not referencia_id:
        for part in source.partes.all():
            values={f.name:getattr(part,f.name) for f in ParteReferenciaTecnica._meta.fields if f.name not in {'id','referencia'}}
            ParteReferenciaTecnica.objects.create(referencia=obj,**values)
    _libevent(empresa,usuario,clave,'BIBLIOTECA_REFERENCIA',payload,obj.pk)
    return obj


@transaction.atomic
def estado_referencia(*,usuario,empresa,referencia_id,estado,clave):
    _liblock(usuario,empresa)
    payload=dict(id=referencia_id,estado=estado)
    old=_libretry(empresa,clave,'BIBLIOTECA_ESTADO',payload)
    if old:
        return ReferenciaTecnica.objects.get(pk=old)
    obj=ReferenciaTecnica.objects.get(pk=referencia_id,empresa=empresa)
    if (obj.estado,estado) not in {('BORRADOR','PUBLICADA'),('PUBLICADA','RETIRADA')}:
        raise ValidationError('Transición de biblioteca no permitida.')
    documento_valido(obj.documento,empresa)
    obj.estado=estado
    obj.save(update_fields=['estado'])
    _libevent(empresa,usuario,clave,'BIBLIOTECA_ESTADO',payload,obj.pk)
    if estado=='PUBLICADA' and settings.GOOGLE_DRIVE_ENABLED:
        transaction.on_commit(lambda:_encolar_documento(obj.documento_id))
    return obj


@transaction.atomic
def reintentar_referencia(*,usuario,empresa,referencia_id,clave):
    _liblock(usuario,empresa)
    ref=ReferenciaTecnica.objects.get(pk=referencia_id,empresa=empresa)
    documento_valido(ref.documento,empresa)
    payload=dict(referencia=ref.pk)
    if not _libretry(empresa,clave,'BIBLIOTECA_REINTENTO',payload):
        _libevent(empresa,usuario,clave,'BIBLIOTECA_REINTENTO',payload,ref.pk)
    transaction.on_commit(lambda:_encolar_documento(ref.documento_id))
    return ref


@transaction.atomic
def guardar_componente(*,usuario,empresa,datos,clave,componente_id=None):
    _liblock(usuario,empresa)
    payload=dict(id=componente_id,datos=datos)
    old=_libretry(empresa,clave,'BIBLIOTECA_COMPONENTE',payload)
    if old:
        return ComponenteTecnico.objects.get(pk=old)
    if set(datos)-{'codigo','fabricante','numero_parte','descripcion','unidad'}:
        raise ValidationError('Campos de componente inválidos.')
    if componente_id:
        obj=ComponenteTecnico.objects.get(pk=componente_id,empresa=empresa)
        for name,value in datos.items():
            setattr(obj,name,value)
    else:
        obj=ComponenteTecnico(empresa=empresa,autor=usuario,**datos)
    _save(obj)
    _libevent(empresa,usuario,clave,'BIBLIOTECA_COMPONENTE',payload,obj.pk)
    return obj


@transaction.atomic
def guardar_parte(*,usuario,empresa,referencia_id,datos,clave,parte_id=None,eliminar=False):
    _liblock(usuario,empresa)
    ref=ReferenciaTecnica.objects.get(pk=referencia_id,empresa=empresa)
    payload=dict(id=parte_id,referencia=ref.pk,datos=datos,eliminar=eliminar)
    old=_libretry(empresa,clave,'BIBLIOTECA_PARTE',payload)
    if old:
        return old
    if ref.estado!='BORRADOR' or ref.tipo!='PLANO':
        raise PermissionDenied('Partes solo en un plano borrador.')
    allowed={'componente_id','posicion','numero_parte','descripcion','unidad','cantidad_referencia','pagina'}
    if set(datos)-allowed:
        raise ValidationError('Campos de parte inválidos.')
    if datos.get('componente_id') and not ComponenteTecnico.objects.filter(pk=datos['componente_id'],empresa=empresa).exists():
        raise PermissionDenied('Componente de otra empresa.')
    obj=ParteReferenciaTecnica.objects.get(pk=parte_id,referencia=ref) if parte_id else ParteReferenciaTecnica(referencia=ref)
    if eliminar:
        result=obj.pk
        obj.delete()
    else:
        for name,value in datos.items():
            setattr(obj,name,value)
        _save(obj)
        result=obj.pk
    _libevent(empresa,usuario,clave,'BIBLIOTECA_PARTE',payload,result)
    return result


@transaction.atomic
def aplicar_referencia(*,usuario,empresa,referencia_id,equipo_id,fundamento,clave,activa=True):
    _liblock(usuario,empresa)
    payload=dict(referencia=referencia_id,equipo=equipo_id,fundamento=fundamento,activa=activa)
    old=_libretry(empresa,clave,'BIBLIOTECA_APLICABILIDAD',payload)
    if old:
        return AplicabilidadReferenciaEquipo.objects.get(pk=old)
    ref=ReferenciaTecnica.objects.get(pk=referencia_id,empresa=empresa)
    equipo=Equipo.objects.filter(pk=equipo_id,proyecto__empresa=empresa).first()
    if not equipo or not fundamento.strip() or (activa and ref.estado!='PUBLICADA'):
        raise ValidationError('Referencia publicada, equipo de empresa y fundamento son obligatorios.')
    obj,_=AplicabilidadReferenciaEquipo.objects.update_or_create(referencia=ref,equipo=equipo,
        defaults={'fundamento':fundamento.strip(),'activa':activa,'autor':usuario})
    _libevent(empresa,usuario,clave,'BIBLIOTECA_APLICABILIDAD',payload,obj.pk)
    return obj


def _snapshot_informe(obj):
    exp=obj.expediente
    snapshot=dict(exp.recepcion.snapshot)
    snapshot.update(plantilla='inicial-v1',diagnostico_id=obj.pk,revision_diagnostico=obj.revision,
        revision_inspeccion=obj.inspeccion.revision,tecnico=obj.tecnico.get_full_name() or obj.tecnico.username,
        revisor=obj.decision.responsable.get_full_name() or obj.decision.responsable.username,
        decision_fecha=obj.decision.creado_en.isoformat(),
        inspeccion={name:str(getattr(obj.inspeccion,name) or '') for name in INS_FIELDS},
        diagnostico={name:str(getattr(obj,name) or '') for name in DIA_FIELDS},
        evidencias=[dict(documento_id=e.documento_id,hash=e.documento.hash_sha256,clase=e.clase,leyenda=e.leyenda,
            referencia=(dict(codigo=e.referencia.codigo,revision=e.referencia.revision,titulo=e.referencia.titulo,
                tipo=e.referencia.tipo,version_fabricante=e.referencia.version_fabricante) if e.referencia_id else None)) for e in _evidencias(obj)],
        piezas=[dict(descripcion=p.descripcion,numero_parte=p.numero_parte,cantidad=str(p.cantidad),unidad=p.unidad,
            motivo_unidad_pendiente=p.motivo_unidad_pendiente,condicion=p.condicion,accion=p.accion,origen=p.origen,referencia=p.snapshot,
            observaciones=p.observaciones,fotografias=[dict(documento=e.documento_id,hash=e.documento.hash_sha256,leyenda=e.leyenda) for e in p.fotografias.select_related('documento').order_by('pk')]) for p in obj.piezas.all().order_by('pk')])
    return snapshot


def _aprobado(obj):
    return vigente(obj) and obj.estado in {'APROBADO','FINALIZADO'} and DecisionTecnica.objects.filter(diagnostico=obj,decision='APROBADO').exists()


@transaction.atomic
def reservar_informe(*,usuario,empresa,diagnostico_id,clave,nueva_version=False,motivo=''):
    obj=Diagnostico.objects.get(pk=diagnostico_id)
    exp=expediente_autorizado(usuario,empresa,obj.expediente_id,{'OFICINA','PLANTA','TECNICO'},True)
    obj.refresh_from_db()
    if not _aprobado(obj):
        raise ValidationError('Solo se emite un diagnóstico vigente aprobado independientemente por PLANTA.')
    try:
        clave=uuid.UUID(str(clave))
    except (ValueError,TypeError):
        raise ValidationError('Clave inválida.') from None
    huella=_digest(dict(diagnostico=obj.pk,nueva_version=bool(nueva_version),motivo=motivo.strip()))
    old=EmisionInformeTecnico.objects.filter(clave=clave).first()
    if old:
        if old.expediente_id!=exp.pk or old.huella!=huella:
            raise ValidationError('Clave de emisión en conflicto.')
        return old
    actual=exp.informes_tecnicos.filter(diagnostico=obj).order_by('-version').first()
    if actual and (not nueva_version or not actual.documento_id):
        return actual
    if nueva_version and not motivo.strip():
        raise ValidationError('Una nueva emisión requiere motivo.')
    snap=_snapshot_informe(obj)
    for evidence in _evidencias(obj):
        documento_valido(evidence.documento,empresa)
    version=(exp.informes_tecnicos.aggregate(n=Max('version'))['n'] or 0)+1
    snap.update(version=version,motivo_emision=motivo.strip())
    result=_save(EmisionInformeTecnico(expediente=exp,diagnostico=obj,version=version,clave=clave,huella=huella,snapshot=snap,autor=usuario))
    evento(exp,usuario,'INFORME_INICIAL_RESERVADO',version=version,diagnostico=obj.pk,motivo=motivo)
    return result


def generar_informe(emision_id):
    if not getattr(settings,'MASISCAM_TECNICA_LOCAL',False):
        raise PermissionDenied('Generación técnica local deshabilitada.')
    from .tecnica_pdf import construir_informe
    em=EmisionInformeTecnico.objects.select_related('expediente','diagnostico','documento').get(pk=emision_id)
    if em.documento_id:
        verificar_copia_persistente(em.documento)
        return em.documento_id
    fotos=[]
    for item in em.snapshot['evidencias']:
        doc=Documento.objects.get(pk=item['documento_id'])
        documento_valido(doc,em.expediente.empresa)
        if doc.hash_sha256!=item['hash']:
            raise ValidationError('Evidencia distinta de la congelada.')
        if item['clase']=='REFERENCIA':
            continue
        with doc.archivo.open('rb') as stream:
            fotos.append((item,stream.read()))
    pdf=construir_informe(em.snapshot,fotos)
    with transaction.atomic():
        exp=ExpedienteMantenimiento.objects.select_for_update().get(pk=em.expediente_id)
        em=EmisionInformeTecnico.objects.select_for_update().get(pk=emision_id)
        if em.documento_id:
            verificar_copia_persistente(em.documento)
            return em.documento_id
        # A stale reservation is kept for audit, never published as current.
        if not _aprobado(em.diagnostico):
            raise ValidationError('La revisión fue sustituida durante la generación; no se publica como vigente.')
        file=ContentFile(pdf,name=f'{exp.numero}-inicial-v{em.version}.pdf')
        file.content_type='application/pdf'
        doc=guardar_archivo(usuario=em.autor,empresa=exp.empresa,proyecto=exp.registro.equipo.proyecto,equipo=exp.registro.equipo,archivo=file,categoria='INFORMES')
        em.documento,em.hash_sha256,em.estado,em.error=doc,doc.hash_sha256,'GENERADA',''
        em.save(update_fields=['documento','hash_sha256','estado','error'])
        diag=em.diagnostico
        diag.estado='FINALIZADO'
        diag.save(update_fields=['estado'])
        etapa=_etapa(exp,'DIAGNOSTICO')
        etapa.estado='FINALIZADO'
        etapa.version+=1
        etapa.save()
        evento(exp,em.autor,'INFORME_INICIAL_GENERADO',version=em.version,hash=doc.hash_sha256,diagnostico=diag.pk)
        return doc.pk

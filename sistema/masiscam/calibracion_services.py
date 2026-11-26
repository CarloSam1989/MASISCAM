"""Local calibration. No bypass of commercial or industrial approvals."""
import copy
import hashlib
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError,transaction
from django.db.models import Max,Q,OuterRef,Subquery
from django.utils import timezone
from . import tecnica_services as t
from . import operacion_services as o
from .models import (PlantillaCalibracion,ValidacionPlantillaCalibracion,AplicabilidadCalibracion,
    Calibracion,MedicionCalibracion,EvidenciaCalibracion,DecisionCalibracion,EmisionCalibracion,
    ActividadReparacion,ResultadoReparacion,ExpedienteMantenimiento,Documento,Proyecto,Equipo,Auditoria)
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


def protocolo_estado(exp):
    """(protocolo validado aplicable | None, requisitos que faltan, redactados exactamente). No crea ni sustituye protocolos."""
    equipo=exp.registro.equipo
    aplicados=list(PlantillaCalibracion.objects.filter(empresa=exp.empresa,aplicabilidades__equipo=equipo).distinct().order_by('-version','-pk'))
    for p in aplicados:
        if plantilla_validada(p,equipo): return p,[]
    faltan=[]
    if not aplicados:
        sueltos=PlantillaCalibracion.objects.filter(empresa=exp.empresa,tipo_equipo=equipo.tipo_producto).order_by('-pk')[:3]
        if sueltos:
            faltan.append('Ningún protocolo está aplicado a este equipo ('+', '.join(f'{p.codigo} v{p.version}' for p in sueltos)+'): ADMINISTRADOR debe aplicarlo explícitamente.')
        else:
            faltan.append('No existe un protocolo de calibración para este tipo de equipo: ADMINISTRADOR debe registrarlo con su fuente técnica.')
        return None,faltan
    p=aplicados[0];nombre=f'Protocolo {p.codigo} v{p.version}'
    try: validar_definicion(p.definicion,True)
    except ValidationError as error: faltan.append(f'{nombre}: definición incompleta ({"; ".join(error.messages)})')
    valid=ValidacionPlantillaCalibracion.objects.filter(plantilla=p).first()
    anchor=aprobacion_plantilla(p)
    if not valid: faltan.append(f'{nombre}: falta la validación técnica independiente.')
    if not anchor: faltan.append(f'{nombre}: falta la aprobación del propietario para su huella exacta.')
    elif valid and valid.aprobacion!=anchor: faltan.append(f'{nombre}: la validación no corresponde a la aprobación vigente del propietario.')
    if not _equipo_compatible(p,equipo): faltan.append(f'{nombre}: marca, modelo o tipo no coinciden con el equipo.')
    return None,faltan or [f'{nombre}: no cumple las comprobaciones de protocolo aplicable.']


def _equipo_compatible(p,equipo):
    app=p.definicion.get('aplicacion',{})
    return (p.tipo_equipo==equipo.tipo_producto and
        all(isinstance(app.get(c),str) and app[c].strip() and app[c].strip().casefold()==getattr(equipo,c,'').strip().casefold()
            for c in ('marca','modelo')))


@transaction.atomic
def crear_plantilla(*,usuario,empresa,clave,datos,archivo):
    autorizar(usuario,empresa,{'ADMIN'})
    t._liblock(usuario,empresa)
    o._datos(datos,('codigo','titulo','tipo_equipo','definicion','proyecto_id','sustituye_id'))
    if str(datos.get('codigo','')).strip().upper().startswith(PREFIJO_FICHA):
        raise ValidationError('El prefijo FICHA- está reservado a las fichas de calibración de TALLER.')
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
    autorizar(usuario,empresa,{'ADMIN'}); t._liblock(usuario,empresa)
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
    autorizar(usuario,empresa,{'ADMIN'}); t._liblock(usuario,empresa)
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
        raise ValidationError('Calibración bloqueada: TALLER debe finalizar el mantenimiento autorizado.')
    acts=ActividadReparacion.objects.filter(autorizacion=auth).annotate(
        resultado_actual=Subquery(ResultadoReparacion.objects.filter(actividad_id=OuterRef('pk')).order_by('-version').values('pk')[:1]))
    return auth,[dict(actividad=pk,resultado=result) for pk,result in acts.order_by('pk').values_list('pk','resultado_actual')]


def vigente(c):
    return not Calibracion.objects.filter(expediente=c.expediente,revision__gt=c.revision).exists()


def _dependencias(c):
    auth,rep=_reparacion(c.expediente)
    if auth.pk!=c.autorizacion_id or rep!=c.reparacion_snapshot or not vigente(c):
        raise ValidationError('La fuente comercial, reparación o calibración fue sustituida; requiere revisión nueva.')
    if not es_ficha(c.plantilla) and not plantilla_validada(c.plantilla,c.expediente.registro.equipo):
        raise ValidationError('Plantilla o aplicabilidad no validadas; operación bloqueada.')
    t.documento_valido(c.plantilla.documento,c.expediente.empresa)


def preliminares(exp):
    """Datos preliminares guardados antes de contar con protocolo (más recientes primero). Registro de auditoría: solo se agregan."""
    return list(Auditoria.objects.filter(expediente=exp,accion='CAL_PRELIMINAR').select_related('usuario').order_by('-pk'))


PREFIJO_FICHA='FICHA-'
FUENTE_HOJA='Hoja física de calibración (formato MASISCAM)'
UNIDADES_HOJA={'longitud':('mm','µm','in','mil'),'presion':('psi','bar','kPa','MPa')}
GRUPOS_HOJA={'longitud':'Unidad de longitud (shims, backlash, referencias y mediciones)','presion':'Unidad de presión'}
# (código, nombre en la hoja física, grupo de unidad, no admite negativos)
PARAMETROS_HOJA=(
    ('SHIM-VS','Shim vertical superior','longitud',True),
    ('SHIM-VI','Shim vertical inferior','longitud',True),
    ('SHIM-H','Shim horizontal','longitud',True),
    ('BACKLASH','Backlash','longitud',True),
    ('TOLERANCIA','Tolerancia','longitud',True),
    ('CAL-TOTAL','Calibración total','longitud',True),
    ('MINIMO','Mínimo','longitud',False),
    ('MAXIMO','Máximo','longitud',False),
    ('PRES-V','Presión eje vertical','presion',True),
    ('PRES-H','Presión eje horizontal','presion',True),
)
CAMPOS_REFERENCIA=('TOLERANCIA','MINIMO','MAXIMO')


def es_ficha(p):
    """La hoja de calibración de TALLER usa una ficha técnica por configuración (código FICHA-…), no un protocolo de ADMINISTRADOR."""
    return bool(p) and str(p.codigo).startswith(PREFIJO_FICHA)


def configuracion_equipo(equipo):
    return {k:' '.join(str(getattr(equipo,k,'') or '').split()) for k in ('marca','modelo','ratio','rotacion')}


def codigo_ficha(empresa,equipo):
    conf=configuracion_equipo(equipo)
    huella=hashlib.sha256(json.dumps([empresa.pk,equipo.tipo_producto]+[conf[k].casefold() for k in ('marca','modelo','ratio','rotacion')],
        ensure_ascii=False).encode()).hexdigest()
    return PREFIJO_FICHA+huella[:16].upper()


def definicion_ficha(conf):
    return dict(formato='hoja-calibracion-v1',configuracion=conf,
        parametros=[dict(codigo=c,nombre=n,grupo=g,unidad='',fuente=FUENTE_HOJA,obligatorio=True) for c,n,g,_ in PARAMETROS_HOJA],
        mediciones=[dict(codigo=f'MED-{n:02}',nombre=f'Medición {n}',grupo='longitud',unidad='',fuente=FUENTE_HOJA) for n in range(1,17)])


def datos_equipo(equipo):
    return {k:str(getattr(equipo,k,'') or '') for k in ('nombre','marca','modelo','numero_serie','ratio','potencia_hp','rpm','rotacion','service_factor')}


def ficha_de(exp,usuario):
    """Ficha técnica de calibración de la configuración del reductor (marca, modelo, ratio y rotación): una por empresa y configuración."""
    equipo=exp.registro.equipo
    codigo=codigo_ficha(exp.empresa,equipo)
    p=PlantillaCalibracion.objects.filter(empresa=exp.empresa,codigo=codigo).order_by('-version').first()
    if p:
        t.documento_valido(p.documento,exp.empresa)
        return p
    from .calibracion_pdf import construir_ficha
    conf=configuracion_equipo(equipo);definicion=definicion_ficha(conf)
    titulo='Ficha de calibración · '+' · '.join(f'{k} {conf[k] or "sin dato"}' for k in ('marca','modelo','ratio','rotacion'))
    archivo=ContentFile(construir_ficha(conf,definicion,titulo),name=f'{codigo}.pdf');archivo.content_type='application/pdf'
    doc=t.guardar_archivo(usuario=usuario,empresa=exp.empresa,proyecto=equipo.proyecto,equipo=equipo,archivo=archivo,categoria='MANUALES')
    try:
        with transaction.atomic():
            p=t._save(PlantillaCalibracion(empresa=exp.empresa,codigo=codigo,version=1,tipo_equipo=equipo.tipo_producto,titulo=titulo[:200],
                definicion=definicion,documento=doc,autor=usuario))
    except (IntegrityError,ValidationError):
        p=PlantillaCalibracion.objects.filter(empresa=exp.empresa,codigo=codigo).order_by('-version').first()
        if not p: raise
    if settings.GOOGLE_DRIVE_ENABLED:
        transaction.on_commit(lambda:t._encolar_documento(doc.pk))
    return p


def valores_previos(c):
    """Unidades y referencias (tolerancia, mínimo, máximo) de la última hoja finalizada de esta configuración: solo para precargar el formulario."""
    previa=Calibracion.objects.filter(plantilla=c.plantilla,estado='FINALIZADA').exclude(pk=c.pk).order_by('-enviado_en').first()
    if not previa: return {}
    p=previa.parametros or {}
    return dict(unidades=dict(p.get('_unidades',{})),origen=previa.pk,**{k:p.get(k) for k in CAMPOS_REFERENCIA})


def tolerancias_aprobadas(exp):
    """Protocolo con tolerancias aprobadas y aplicable al equipo, si existe (ADMINISTRADOR lo aprueba por separado; no condiciona la hoja)."""
    return protocolo_estado(exp)[0]


def _tolerancias_hoja(c):
    p=tolerancias_aprobadas(c.expediente)
    if not p: return None
    return dict(protocolo=p.codigo,version=p.version,huella=huella_plantilla(p),
        aprobacion=(aprobacion_plantilla(p) or {}).get('aprobacion_propietario',''),
        filas=[dict(minimo=r.get('minimo'),maximo=r.get('maximo'),tolerancia=r.get('tolerancia',''),unidad=r.get('unidad','')) for r in p.definicion.get('mediciones',[])])


def _evaluar(valor,fila,unidad):
    """Verificación aritmética contra un rango aprobado. Sin criterio válido no se evalúa ni se afirma conformidad."""
    if fila is None: return 'Sin tolerancia aprobada: no se evalúa'
    if not unidad or str(fila.get('unidad','')).strip().casefold()!=unidad.strip().casefold(): return 'Unidad distinta a la aprobada: no se evalúa'
    lo,hi=fila.get('minimo'),fila.get('maximo')
    if lo in (None,'') or hi in (None,''): return 'Sin límites numéricos aprobados: no se evalúa'
    try: n=Decimal(str(valor));return 'Dentro del rango aprobado' if Decimal(str(lo))<=n<=Decimal(str(hi)) else 'Fuera del rango aprobado'
    except (InvalidOperation,ValueError): return 'Sin límites numéricos aprobados: no se evalúa'


def _decimal_texto(value):
    if value is None: return None
    raw=format(value,'f')
    return raw.rstrip('0').rstrip('.') if '.' in raw else raw


def _validar_valores_hoja(parametros):
    """Rangos de la hoja física: magnitudes sin signo negativo (shims, backlash, tolerancia, calibración total, presiones) y mínimo ≤ máximo."""
    valores={}
    for codigo,nombre,grupo,no_negativo in PARAMETROS_HOJA:
        v=parametros.get(codigo)
        if v in (None,''): continue
        n=decimal_seguro(v)
        if no_negativo and n<0: raise ValidationError(f'{nombre}: no puede ser negativo.')
        valores[codigo]=n
    if 'MINIMO' in valores and 'MAXIMO' in valores and valores['MINIMO']>valores['MAXIMO']:
        raise ValidationError('El mínimo no puede ser mayor que el máximo.')


def _parametros_hoja(v):
    permitidos={c for c,*_ in PARAMETROS_HOJA}
    if set(v)-permitidos-{'_unidades'}: raise ValidationError('Parámetros fuera de la hoja de calibración.')
    salida={k:(str(decimal_seguro(x,True)) if x not in (None,'') else None) for k,x in v.items() if k!='_unidades'}
    unidades=v.get('_unidades') or {}
    if not isinstance(unidades,dict) or set(unidades)-set(UNIDADES_HOJA) or any(u not in (None,'') and u not in UNIDADES_HOJA[g] for g,u in unidades.items()):
        raise ValidationError('Unidad no admitida: elige una de la lista.')
    salida['_unidades']={g:u for g,u in unidades.items() if u}
    _validar_valores_hoja(salida)
    return salida


def pdf_generado(c):
    """El PDF de la calibración (tercer documento del expediente) ya existe."""
    return c.emisiones.filter(estado='GENERADA').exists()


def faltantes_hoja(c):
    """Campos obligatorios de la hoja de calibración que aún no están completos (texto para el técnico)."""
    p=c.parametros or {};unidades=p.get('_unidades',{});faltan=[]
    if not c.fecha: faltan.append('Fecha')
    faltan += [nombre for codigo,nombre,_,_ in PARAMETROS_HOJA if p.get(codigo) in (None,'')]
    faltan += [etiqueta for grupo,etiqueta in GRUPOS_HOJA.items() if not unidades.get(grupo)]
    medidas={m.punto:m.valor for m in c.mediciones.all()}
    sin=[str(n) for n in range(1,17) if medidas.get(f'MED-{n:02}') is None]
    if sin: faltan.append('Mediciones '+', '.join(sin))
    return faltan


def _finalizar_hoja(c,exp,usuario,clave,payload):
    """TALLER genera la hoja: queda FINALIZADA sin pasar por ADMINISTRADOR. Se congelan los datos del equipo y las tolerancias aprobadas usadas."""
    if c.sustituye_id and not c.motivo_revision.strip(): raise ValidationError('La revisión de la hoja necesita su motivo.')
    faltan=faltantes_hoja(c)
    if faltan: raise ValidationError('Para generar la hoja faltan: '+'; '.join(faltan)+'.')
    _validar_valores_hoja(c.parametros)
    for e in c.evidencias.all():
        t.documento_valido(e.documento,exp.empresa)
    parametros=dict(c.parametros)
    parametros['_equipo']=datos_equipo(exp.registro.equipo)
    parametros['_tolerancias']=_tolerancias_hoja(c)
    c.parametros=parametros;c.enviado_en=timezone.now();c.estado='FINALIZADA';c.version_edicion+=1
    c.save()
    t._event(exp,usuario,clave,'CAL_FINALIZADA',payload,c.pk)
    return c


@transaction.atomic
def iniciar_hoja(*,usuario,empresa,expediente_id,clave,motivo=''):
    """TALLER abre la hoja de calibración sobre la ficha técnica de la configuración del equipo. No requiere protocolo ni aprobación de ADMINISTRADOR."""
    exp=expediente(usuario,empresa,expediente_id,{'TALLER'},True)
    payload=dict(hoja=True,motivo=motivo)
    old=t._retry(exp,clave,'CAL_INICIADA',payload)
    if old:
        return Calibracion.objects.get(pk=old)
    auth,rep=_reparacion(exp)
    asignacion=exp.asignaciones_tecnicas.filter(fin__isnull=True).first()
    if not asignacion:
        raise ValidationError('Asigne un técnico principal activo.')
    t.autorizar_tecnica(asignacion.tecnico,empresa,{'TALLER'},exp)
    if asignacion.tecnico_id!=usuario.pk:
        raise PermissionDenied('Solo el técnico principal asignado registra la calibración.')
    anterior=Calibracion.objects.filter(expediente=exp).order_by('-revision').first()
    if anterior:
        if not anterior.enviado_en:
            if es_ficha(anterior.plantilla): return anterior   # hoja en curso: se continúa, no se duplica
            raise ValidationError('Hay una calibración con protocolo en curso: se continúa en su propia hoja.')
        if anterior.estado not in {'FINALIZADA','OBSERVADA','RECHAZADA'}:
            raise PermissionDenied('La calibración ya tiene su propia revisión; no se reabre desde TALLER.')
        if not motivo.strip():
            raise ValidationError('Indica el motivo de la nueva revisión de la hoja.')
        ultimo=exp.informes_finales.order_by('-version').first()
        if ultimo and ultimo.estado!='OBSERVADO':
            raise ValidationError('El informe final ya fue generado: ADMINISTRADOR debe observarlo para corregir la hoja.')
    ficha=ficha_de(exp,usuario)
    c=t._save(Calibracion(expediente=exp,diagnostico=auth.propuesta.diagnostico,autorizacion=auth,plantilla=ficha,tecnico=asignacion.tecnico,
        reparacion_snapshot=rep,revision=anterior.revision+1 if anterior else 1,sustituye=anterior,motivo_revision=motivo,clave=clave))
    t._event(exp,usuario,clave,'CAL_INICIADA',payload,c.pk)
    return c


def _snapshot_hoja(c):
    d=c.plantilla.definicion
    ms={m.punto:m for m in c.mediciones.all()}
    p=c.parametros or {}
    unidades=dict(p.get('_unidades',{}))
    fija=bool(c.enviado_en)
    equipo=p.get('_equipo') if fija and p.get('_equipo') else datos_equipo(c.expediente.registro.equipo)
    tol=p.get('_tolerancias') if fija else _tolerancias_hoja(c)
    filas=(tol or {}).get('filas',[])
    longitud=unidades.get('longitud','')
    mediciones=[]
    for n,r in enumerate(d['mediciones']):
        m=ms.get(r['codigo']);valor=_decimal_texto(m.valor) if m else None
        fila=filas[n] if n<len(filas) else None
        mediciones.append(dict(codigo=r['codigo'],nombre=r['nombre'],unidad=longitud,valor=valor,
            minimo=fila.get('minimo') if fila else None,maximo=fila.get('maximo') if fila else None,tolerancia=fila.get('tolerancia','') if fila else '',
            fuente=FUENTE_HOJA,criterio='',observaciones=m.observaciones if m else '',
            evaluacion=_evaluar(valor,fila,longitud) if valor is not None else 'Pendiente'))
    parametros=[dict(codigo=r['codigo'],nombre=r['nombre'],grupo=r['grupo'],unidad=unidades.get(r['grupo'],''),fuente=FUENTE_HOJA,
        valor=_decimal_texto(Decimal(p[r['codigo']])) if p.get(r['codigo']) not in (None,'') else None) for r in d['parametros']]
    valores={r['codigo']:r['valor'] for r in parametros}
    advertencias=[]
    if all(valores.get(k) is not None for k in ('CAL-TOTAL','MINIMO','MAXIMO')):
        total,minimo,maximo=(Decimal(valores[k]) for k in ('CAL-TOTAL','MINIMO','MAXIMO'))
        if not minimo<=total<=maximo:
            advertencias.append('La calibración total registrada está fuera del rango mínimo–máximo que registró TALLER.')
    if tol:
        verificacion=dict(estado='TOLERANCIA_APROBADA',protocolo=tol['protocolo'],version=tol['version'],huella=tol['huella'],aprobacion=tol['aprobacion'],
            texto=f"Las mediciones se compararon con el rango aprobado del protocolo {tol['protocolo']} v{tol['version']}. La comparación es aritmética y no sustituye la valoración técnica.")
    else:
        verificacion=dict(estado='SIN_TOLERANCIA_APROBADA',
            texto='Sin tolerancia aprobada: esta hoja registra mediciones. No se afirma conformidad técnica.')
    return dict(plantilla='hoja-calibracion-v1',numero=c.expediente.numero,recepcion=c.expediente.recepcion.snapshot,
        calibracion=c.pk,revision=c.revision,diagnostico=c.diagnostico_id,autorizacion=c.autorizacion_id,reparacion=c.reparacion_snapshot,
        tipo_equipo=c.plantilla.tipo_equipo,protocolo=c.plantilla.codigo,protocolo_version=c.plantilla.version,protocolo_huella=huella_plantilla(c.plantilla),
        tecnico=c.tecnico.username,tecnico_nombre=c.tecnico.get_full_name() or c.tecnico.username,fecha=c.fecha.isoformat() if c.fecha else '',
        instrumentos='',resultados='',observaciones=c.observaciones,estado=c.estado,decision=None,motivo_revision=c.motivo_revision,
        equipo=equipo,configuracion=d.get('configuracion',{}),unidades=unidades,parametros=parametros,mediciones=mediciones,
        verificacion=verificacion,advertencias=advertencias,
        evidencias=[dict(documento=e.documento_id,hash=e.documento.hash_sha256,clase=e.clase,leyenda=e.leyenda) for e in c.evidencias.select_related('documento').order_by('pk')])


@transaction.atomic
def iniciar(*,usuario,empresa,expediente_id,plantilla_id,clave,motivo=''):
    exp=expediente(usuario,empresa,expediente_id,{'TALLER', 'ADMIN'},True)
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
    if oldcal and (role!='ADMIN' or not motivo.strip()):
        raise PermissionDenied('ADMINISTRADOR autoriza corrección con motivo; el técnico no sobrescribe revisiones previas.')
    if not oldcal and role not in t.OPERADORES:
        raise PermissionDenied('El técnico principal inicia la calibración.')
    assignment=exp.asignaciones_tecnicas.filter(fin__isnull=True).first()
    if not assignment:
        raise ValidationError('Asigne un técnico principal activo.')
    t.autorizar_tecnica(assignment.tecnico,empresa,{'TALLER'},exp)
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
    exp=expediente(usuario,empresa,c.expediente_id,{'TALLER'},True);c.refresh_from_db()
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
            if es_ficha(c.plantilla):
                v=_parametros_hoja(v)
            else:
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


def guardar_formato(*,usuario,empresa,calibracion_id,version,clave,datos,mediciones,adjunto=None):
    """Guarda de una vez la hoja de calibración: datos y parámetros, las mediciones modificadas y, si se adjunta, una evidencia.

    Reutiliza guardar/medir/evidencia (permisos, versión de edición, validaciones y auditoría intactos). Las claves de cada
    paso derivan de la clave de la hoja y todo se confirma o se revierte junto; repetir el envío no duplica registros."""
    base=uuid.UUID(str(clave))
    with transaction.atomic():
        c=Calibracion.objects.get(pk=calibracion_id)
        exp=expediente(usuario,empresa,c.expediente_id,{'TALLER'},True)
        repetida=Auditoria.objects.filter(expediente=exp,detalle__clave=str(uuid.uuid5(base,'datos'))).exists()
        c=guardar(usuario=usuario,empresa=empresa,calibracion_id=c.pk,version=version,clave=uuid.uuid5(base,'datos'),datos=datos)
        if repetida:
            return c
        paso=int(version)+1
        actuales={m.punto:m.valor for m in c.mediciones.all()}
        for punto,valor in mediciones.items():
            if valor is None and punto not in actuales or valor is not None and punto in actuales and actuales[punto]==valor:
                continue
            medir(usuario=usuario,empresa=empresa,calibracion_id=c.pk,version=paso,clave=uuid.uuid5(base,f'medicion:{punto}'),punto=punto,valor=valor)
            paso+=1
        if adjunto and adjunto.get('archivo'):
            evidencia(usuario=usuario,empresa=empresa,calibracion_id=c.pk,version=paso,clave=uuid.uuid5(base,'evidencia'),**adjunto)
        return Calibracion.objects.get(pk=c.pk)


@transaction.atomic
def medir(*,usuario,empresa,calibracion_id,version,clave,punto,valor,observaciones=''):
    c=Calibracion.objects.get(pk=calibracion_id)
    exp=expediente(usuario,empresa,c.expediente_id,{'TALLER'},True);c.refresh_from_db()
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
    exp=expediente(usuario,empresa,c.expediente_id,{'TALLER'},True);c.refresh_from_db()
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
    exp=expediente(usuario,empresa,c.expediente_id,{'TALLER'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,version=version)
    ficha=es_ficha(c.plantilla)
    old=t._retry(exp,clave,'CAL_FINALIZADA' if ficha else 'CAL_ENVIADA',payload)
    if old:
        return Calibracion.objects.get(pk=old)
    _editable(c,usuario,version)
    if ficha:
        return _finalizar_hoja(c,exp,usuario,clave,payload)
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
    exp=expediente(usuario,empresa,c.expediente_id,{'ADMIN'},True);c.refresh_from_db()
    payload=dict(calibracion=c.pk,decision=decision,motivo=motivo,evaluaciones=evaluaciones)
    old=t._retry(exp,clave,'CAL_DECISION',payload)
    if old:
        return DecisionCalibracion.objects.get(pk=old)
    if es_ficha(c.plantilla):
        raise ValidationError('La hoja de calibración la genera TALLER y no requiere decisión de ADMINISTRADOR.')
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
        raise ValidationError('Aprobación bloqueada: evaluación manual por ADMINISTRADOR incompleta/no favorable. No se admiten excepciones implícitas.')
    obj=t._save(DecisionCalibracion(calibracion=c,decision=decision,motivo=motivo,evaluaciones=evaluaciones,responsable=usuario,clave=clave))
    c.estado=decision;c.save(update_fields=['estado'])
    t._event(exp,usuario,clave,'CAL_DECISION',payload,obj.pk)
    if decision == 'APROBADA':
        from .flujo_services import generar_final_automatico
        transaction.on_commit(lambda: generar_final_automatico(c.pk))
    return obj


def snapshot(c):
    if es_ficha(c.plantilla):
        return _snapshot_hoja(c)
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
    exp=expediente(usuario,empresa,c.expediente_id,{'OFICINA', 'ADMIN', 'TALLER'},True);c.refresh_from_db()
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
        if es_ficha(c.plantilla):
            # Tercer documento del expediente: al quedar generado se incorpora solo al informe final.
            from .flujo_services import generar_final_automatico
            transaction.on_commit(lambda:generar_final_automatico(c.pk))
        return doc.pk

"""Manual, versioned drawing maps. Existing technical services remain authoritative."""
import csv
import io
import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max

from . import tecnica_services as tecnica
from .models import Diagnostico, MapaPlano, PosicionPlano, ReferenciaTecnica
from .recepcion_services import _hash, validar_fotografias


def revision_plano(ref):
    from .models import Auditoria
    return Auditoria.objects.filter(empresa_id=ref.empresa_id, objeto_tipo='ReferenciaTecnica',
        objeto_id=str(ref.pk), accion='PLANO_REVISADO').select_related('usuario').order_by('-pk').first()


def validar_pdf_trabajo(archivo):
    from pypdf import PdfReader
    try:
        archivo.seek(0)
        reader = PdfReader(archivo)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
            raise ValueError()
    except Exception as exc:
        raise ValidationError('Seleccione un PDF legible, sin contraseña y de hasta 200 páginas.') from exc
    finally:
        archivo.seek(0)


@transaction.atomic
def corregir_plano_trabajo(*, usuario, empresa, referencia_id, clave, archivo):
    ref = ReferenciaTecnica.objects.get(pk=referencia_id, empresa=empresa)
    if not ref.expediente_trabajo_id:
        raise PermissionDenied('La corrección requiere un plano de trabajo propio.')
    from accounts.models import Empresa
    Empresa.objects.select_for_update().get(pk=empresa.pk)
    exp = tecnica.expediente_autorizado(usuario, empresa, ref.expediente_trabajo_id, {'TALLER'}, True)
    ref = ReferenciaTecnica.objects.select_for_update().get(pk=ref.pk)
    payload = dict(referencia=ref.pk, hash=_hash(archivo))
    old = tecnica._retry(exp, clave, 'PLANO_TRABAJO_CORREGIDO', payload)
    if old:
        return ReferenciaTecnica.objects.get(pk=old)
    decision = revision_plano(ref)
    if ref.estado != 'BORRADOR' or not decision or decision.detalle['decision'] != 'RECHAZADO':
        raise ValidationError('Solo se corrige un plano devuelto a TALLER.')
    if ReferenciaTecnica.objects.filter(empresa=empresa, codigo=ref.codigo, revision__gt=ref.revision).exists():
        raise ValidationError('Ya existe una nueva versión. Continúe desde ella.')
    validar_pdf_trabajo(archivo)
    if payload['hash'] == ref.documento.hash_sha256:
        tecnica.documento_valido(ref.documento, empresa)
        doc = ref.documento
    else:
        doc = tecnica.guardar_archivo(usuario=usuario, empresa=empresa, proyecto=exp.registro.equipo.proyecto,
            equipo=exp.registro.equipo, archivo=archivo, categoria='PLANOS')
    nueva = tecnica._save(ReferenciaTecnica(empresa=empresa, documento=doc, expediente_trabajo=exp,
        autor=usuario, sustituye=ref, revision=ref.revision+1,
        **{f:getattr(ref, f) for f in ('codigo','tipo','titulo','marca','modelo','ratio',
                                     'version_fabricante','procedencia','autorizacion_uso')}))
    tecnica.copiar_partes_referencia(ref, nueva)
    tecnica._event(exp, usuario, clave, 'PLANO_TRABAJO_CORREGIDO', payload, nueva.pk)
    return nueva


@transaction.atomic
def decidir_plano_trabajo(*, usuario, empresa, referencia_id, clave, revision, decision, motivo=''):
    tecnica._liblock(usuario, empresa)
    ref = ReferenciaTecnica.objects.get(pk=referencia_id, empresa=empresa)
    if not ref.expediente_trabajo_id or ref.tipo != 'PLANO':
        raise PermissionDenied('La revisión requiere el plano de un expediente.')
    exp = tecnica.expediente_autorizado(usuario, empresa, ref.expediente_trabajo_id, {'ADMIN'}, True)
    ref = ReferenciaTecnica.objects.select_for_update().get(pk=ref.pk)
    payload = dict(referencia=ref.pk, revision=str(revision), decision=decision, motivo=motivo.strip())
    if tecnica._libretry(empresa, clave, 'PLANO_DECIDIDO', payload):
        return ref
    if str(revision) != str(ref.revision) or ref.estado != 'BORRADOR':
        raise ValidationError('La versión ya fue publicada o cambió. Recargue el plano.')
    if ReferenciaTecnica.objects.filter(empresa=empresa, codigo=ref.codigo, revision__gt=ref.revision).exists():
        raise ValidationError('Revise la nueva versión; esta se conserva como histórica.')
    if decision not in {'APROBADO', 'RECHAZADO'} or len(motivo.strip()) > 2000:
        raise ValidationError('Decisión o motivo inválidos.')
    if decision == 'RECHAZADO' and not motivo.strip():
        raise ValidationError('Indique el motivo del rechazo.')
    tecnica.validar_revisor_plano(usuario, ref)
    tecnica.documento_valido(ref.documento, empresa)
    mapa = ref.mapas.order_by('-version').first()
    if decision == 'APROBADO':
        key = uuid.UUID(str(clave))
        base = dict(usuario=usuario, empresa=empresa, referencia_id=ref.pk)
        tecnica.estado_referencia(**base, estado='PUBLICADA', clave=uuid.uuid5(key, 'publicar'))
        tecnica.aplicar_referencia(**base, equipo_id=exp.registro.equipo_id,
            fundamento=f'Plano r{ref.revision} aprobado para el equipo del expediente {exp.numero}.',
            clave=uuid.uuid5(key, 'aplicabilidad'))
        # Publicar una copia revisada del mapa parcial, sin alterar el original.
        if mapa and mapa.provisional:
            mapa = guardar_mapa(**base, base_version=mapa.version, clave=uuid.uuid5(key, 'mapa'),
                posiciones=[dict(parte=p.parte_id, pagina=p.pagina, x=str(p.x), y=str(p.y))
                            for p in mapa.posiciones.all()])
        ref.refresh_from_db()
    from .models import Auditoria
    Auditoria.objects.create(empresa=empresa, usuario=usuario, expediente=exp,
        accion='PLANO_REVISADO', objeto_tipo='ReferenciaTecnica', objeto_id=str(ref.pk),
        detalle=dict(decision=decision, motivo=motivo.strip(), revision=ref.revision,
            documento=ref.documento_id, hash_documento=ref.documento.hash_sha256,
            mapa_version=mapa.version if mapa else None, equipo=exp.registro.equipo_id,
            partes=list(ref.partes.order_by('pk').values('id', 'posicion', 'numero_parte', 'descripcion'))))
    tecnica._libevent(empresa, usuario, clave, 'PLANO_DECIDIDO', payload, ref.pk)
    return ref


@transaction.atomic
def cargar_plano_trabajo(*, usuario, empresa, expediente_id, clave, archivo):
    exp = tecnica.expediente_autorizado(usuario, empresa, expediente_id, {'TALLER'}, True)
    digest = _hash(archivo)
    payload = dict(hash=digest)
    old = tecnica._retry(exp, clave, 'PLANO_TRABAJO_CARGADO', payload)
    if old:
        return ReferenciaTecnica.objects.get(pk=old)
    etapa = tecnica._etapa(exp, 'DIAGNOSTICO')
    if etapa and etapa.diagnostico_id:
        diag = etapa.diagnostico
    else:
        ins = tecnica._etapa(exp, 'INSPECCION')
        if not ins:
            _, diag = tecnica.iniciar_inspeccion(usuario=usuario, empresa=empresa,
                expediente_id=exp.pk, clave=uuid.uuid5(uuid.UUID(str(clave)), 'inspeccion'))
        elif ins.inspeccion.enviado_en:
            diag = tecnica.crear_revision(usuario=usuario, empresa=empresa, expediente_id=exp.pk,
                tipo='DIAGNOSTICO', clave=uuid.uuid5(uuid.UUID(str(clave)), 'diagnostico'))
        else:
            diag = tecnica.preparar_diagnostico(usuario=usuario, empresa=empresa, expediente_id=exp.pk,
                clave=uuid.uuid5(uuid.UUID(str(clave)), 'diagnostico'))
    tecnica._editable(usuario, empresa, diag, diag.version_edicion)
    tecnica.seleccion_editable(diag)
    validar_pdf_trabajo(archivo)
    ref = exp.planos_trabajo.filter(documento__hash_sha256=digest).first()
    if not ref:
        doc = tecnica.guardar_archivo(usuario=usuario, empresa=empresa,
            proyecto=exp.registro.equipo.proyecto, equipo=exp.registro.equipo,
            archivo=archivo, categoria='PLANOS')
        if doc.tipo_mime != 'application/pdf' or doc.categoria != 'PLANOS':
            raise ValidationError('El documento debe ser un plano PDF.')
        ref = tecnica._save(ReferenciaTecnica(empresa=empresa, documento=doc,
            expediente_trabajo=exp, codigo=f'TRABAJO-{exp.pk}-{digest[:16]}', revision=1,
            tipo='PLANO', titulo=archivo.name[:200], autor=usuario,
            procedencia=f'Cargado por TALLER desde {exp.numero}',
            autorizacion_uso='Borrador de trabajo privado; pendiente de revisión y aplicabilidad por ADMINISTRADOR.'))
    tecnica.documento_valido(ref.documento, empresa)
    tecnica._event(exp, usuario, clave, 'PLANO_TRABAJO_CARGADO', payload, ref.pk)
    return ref


@transaction.atomic
def registrar_componente_trabajo(*, usuario, empresa, referencia_id, diagnostico_id, clave, datos):
    from .models import ParteReferenciaTecnica
    diag = Diagnostico.objects.get(pk=diagnostico_id)
    exp = tecnica.expediente_autorizado(usuario, empresa, diag.expediente_id, {'TALLER'}, True)
    diag.refresh_from_db()
    ref = ReferenciaTecnica.objects.get(pk=referencia_id)
    payload = dict(referencia=ref.pk, diagnostico=diag.pk, datos=datos)
    old = tecnica._retry(exp, clave, 'COMPONENTE_TRABAJO_REGISTRADO', payload)
    if old:
        return ParteReferenciaTecnica.objects.get(pk=old)
    tecnica._editable(usuario, empresa, diag, diag.version_edicion)
    tecnica.seleccion_editable(diag)
    if not tecnica.plano_de_trabajo(ref, exp):
        raise PermissionDenied('Solo se registran componentes del plano borrador de este expediente.')
    if set(datos) - {'posicion', 'numero_parte', 'descripcion', 'unidad', 'pagina'}:
        raise ValidationError('Campos de componente no permitidos.')
    if not datos.get('posicion', '').strip() or not datos.get('descripcion', '').strip():
        raise ValidationError('Indique el número y la descripción leídos en el plano.')
    parte = tecnica._save(ParteReferenciaTecnica(referencia=ref, **datos))
    tecnica._event(exp, usuario, clave, 'COMPONENTE_TRABAJO_REGISTRADO', payload, parte.pk)
    return parte


def documento_info(ref, empresa):
    tecnica.documento_valido(ref.documento, empresa)
    try:
        with ref.documento.archivo.open('rb') as stream:
            if ref.documento.tipo_mime == 'application/pdf':
                from pypdf import PdfReader
                reader = PdfReader(stream)
                if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
                    raise ValueError()
                return dict(tipo='pdf', paginas=len(reader.pages))
            if ref.documento.tipo_mime in {'image/png', 'image/jpeg', 'image/webp'}:
                from PIL import Image
                with Image.open(stream) as image:
                    return dict(tipo='imagen', paginas=1, ancho=image.width, alto=image.height)
    except Exception as exc:
        raise ValidationError('No se pudo verificar el plano. Revise el original PDF o imagen.') from exc
    raise ValidationError('El visor admite PDF, PNG, JPEG o WebP. El original sigue disponible.')


@transaction.atomic
def guardar_mapa(*, usuario, empresa, referencia_id, base_version, posiciones, clave):
    rol = tecnica.autorizar_tecnica(usuario, empresa, {'ADMIN', 'TALLER', 'OFICINA'})
    ref = ReferenciaTecnica.objects.get(pk=referencia_id, empresa=empresa)
    if rol.rol == 'TALLER':
        if not ref.expediente_trabajo_id or ref.estado != 'BORRADOR':
            raise PermissionDenied('TALLER solo marca posiciones en el plano provisional de su expediente.')
        exp = tecnica.expediente_autorizado(usuario, empresa, ref.expediente_trabajo_id, {'TALLER'}, True)
        etapa = tecnica._etapa(exp, 'DIAGNOSTICO')
        if not etapa or not etapa.diagnostico_id:
            raise PermissionDenied('Se requiere un diagnóstico borrador vigente.')
        tecnica._editable(usuario, empresa, etapa.diagnostico, etapa.diagnostico.version_edicion)
        tecnica.seleccion_editable(etapa.diagnostico)
    else:
        tecnica._liblock(usuario, empresa, {'ADMIN', 'OFICINA'})
        if rol.rol == 'OFICINA' and (ref.estado != 'BORRADOR' or ref.expediente_trabajo_id):
            raise PermissionDenied('OFICINA solo configura planos de biblioteca en borrador.')
    ref = ReferenciaTecnica.objects.select_for_update().get(pk=ref.pk, empresa=empresa)
    payload = dict(referencia=ref.pk, base=base_version, posiciones=posiciones)
    old = tecnica._libretry(empresa, clave, 'PLANO_MAPA_CREADO', payload)
    if old:
        return MapaPlano.objects.get(pk=old)
    provisional = ref.estado == 'BORRADOR'
    if rol.rol == 'TALLER' and not provisional:
        raise PermissionDenied('El plano dejó de ser provisional; recargue antes de marcar.')
    if ref.tipo != 'PLANO' or not (ref.estado == 'PUBLICADA' or provisional):
        raise PermissionDenied('Publique primero la revisión documental verificada para configurar su mapa.')
    info = documento_info(ref, empresa)
    actual = ref.mapas.aggregate(n=Max('version'))['n'] or 0
    if str(base_version) != str(actual):
        raise ValidationError('Otro usuario guardó el mapa. Recargue antes de modificarlo.')
    if not isinstance(posiciones, list) or len(posiciones) > 500:
        raise ValidationError('El mapa admite hasta 500 posiciones manuales.')
    partes = {p.pk: p for p in ref.partes.all()}
    puntos = []
    for item in posiciones:
        try:
            if set(item) != {'parte', 'pagina', 'x', 'y'}:
                raise ValueError()
            parte = partes[int(item['parte'])]
            pagina = int(item['pagina'])
            x, y = Decimal(str(item['x'])), Decimal(str(item['y']))
            if str(pagina) != str(item['pagina']) or not 1 <= pagina <= info['paginas']:
                raise ValueError()
            if not x.is_finite() or not y.is_finite() or not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError()
            puntos.append(PosicionPlano(parte=parte, numero=parte.posicion, pagina=pagina,
                x=x.quantize(Decimal('.00000001')), y=y.quantize(Decimal('.00000001'))))
        except (ValueError, KeyError, TypeError, InvalidOperation):
            raise ValidationError('Parte, página o coordenadas inválidas; use valores normalizados entre 0 y 1.') from None
    mapa = tecnica._save(MapaPlano(referencia=ref, version=actual + 1,
        hash_documento=ref.documento.hash_sha256, autor=usuario, clave=clave, provisional=provisional))
    for punto in puntos:
        punto.mapa = mapa
        tecnica._save(punto)
    tecnica._libevent(empresa, usuario, clave, 'PLANO_MAPA_CREADO', payload, mapa.pk)
    return mapa


@transaction.atomic
def guardar_posicion_trabajo(*, usuario, empresa, referencia_id, diagnostico_id, clave,
                            version, mapa_version, operacion, punto_id=None, parte_id=None,
                            pagina=None, x=None, y=None, posicion='', descripcion='',
                            cantidad=None, condicion='', unidad='', fotografias=()):
    """Un gesto manual guarda componente, mapa versionado y selección en conjunto."""
    from accounts.models import Empresa
    tecnica.autorizar_tecnica(usuario, empresa, {'TALLER'})
    Empresa.objects.select_for_update().get(pk=empresa.pk)
    diag = Diagnostico.objects.get(pk=diagnostico_id)
    exp = tecnica.expediente_autorizado(usuario, empresa, diag.expediente_id, {'TALLER'}, True)
    diag.refresh_from_db()
    ref = ReferenciaTecnica.objects.select_for_update().get(pk=referencia_id, empresa=empresa, tipo='PLANO')
    if not (tecnica.plano_de_trabajo(ref, exp) or
            ref.estado == 'PUBLICADA' and ref.aplicabilidades.filter(equipo=exp.registro.equipo, activa=True).exists()):
        raise PermissionDenied('Plano ajeno o sin aplicabilidad a este equipo.')
    payload = dict(referencia=ref.pk, diagnostico=diag.pk, version=version, mapa=mapa_version,
        operacion=operacion, punto=punto_id, parte=parte_id, pagina=pagina, x=x, y=y,
        posicion=posicion, descripcion=descripcion, cantidad=cantidad, condicion=condicion, unidad=unidad)
    if fotografias:
        if operacion != 'guardar':
            raise ValidationError('Las fotografías se adjuntan al guardar una pieza.')
        payload['fotos'] = [_hash(f) for f in fotografias]
    old = tecnica._retry(exp, clave, 'PLANO_POSICION_TRABAJO', payload)
    if old:
        return ReferenciaTecnica.objects.get(pk=old)
    tecnica._editable(usuario, empresa, diag, version)
    tecnica.seleccion_editable(diag)
    mapa = ref.mapas.order_by('-version').first()
    if int(mapa_version) != (mapa.version if mapa else 0):
        raise ValidationError('El mapa cambió. Recargue antes de modificarlo.')
    punto = mapa.posiciones.filter(pk=punto_id).first() if mapa and punto_id else None
    if punto_id and not punto:
        raise ValidationError('La posición no pertenece a la versión vigente del mapa.')
    if operacion not in {'guardar','seleccionar','retirar'}:
        raise ValidationError('Operación de posición inválida.')
    if operacion in {'seleccionar','retirar'} and not punto:
        raise ValidationError('Seleccione una posición registrada.')
    key = uuid.UUID(str(clave))
    if operacion == 'retirar' and (not mapa.provisional or ref.estado != 'BORRADOR'):
        raise PermissionDenied('Solo se retiran posiciones provisionales.')
    if operacion == 'guardar' and ref.estado == 'PUBLICADA':
        # No modificar el documento ni los componentes aprobados: continuar en
        # un borrador sucesor del expediente con el mismo PDF y fuentes.
        siguiente = ReferenciaTecnica.objects.filter(empresa=empresa, codigo=ref.codigo).aggregate(n=Max('revision'))['n']
        if siguiente != ref.revision:
            raise ValidationError('Existe una versión posterior. Abra ese plano desde el expediente.')
        source = ref
        ref = tecnica._save(ReferenciaTecnica(empresa=empresa, documento=source.documento,
            expediente_trabajo=exp, autor=usuario, revision=siguiente+1, sustituye=source,
            **{f:getattr(source,f) for f in ('codigo','tipo','titulo','marca','modelo','ratio',
                'version_fabricante','procedencia','autorizacion_uso')}))
        tecnica.copiar_partes_referencia(source, ref)
        parts = {p.posicion:p for p in ref.partes.all()}
        if mapa:
            originales = list(mapa.posiciones.order_by('pk'))
            copia = guardar_mapa(usuario=usuario, empresa=empresa, referencia_id=ref.pk,
                base_version=0, clave=uuid.uuid5(key,'copiar-mapa'),
                posiciones=[dict(parte=parts[p.parte.posicion].pk,pagina=p.pagina,x=str(p.x),y=str(p.y)) for p in originales])
            if punto:
                punto = list(copia.posiciones.order_by('pk'))[next(i for i,p in enumerate(originales) if p.pk==punto.pk)]
            mapa = copia
        if parte_id:
            original = source.partes.filter(pk=parte_id).first()
            if not original: raise ValidationError('Componente de otro plano.')
            parte_id = parts[original.posicion].pk
    if operacion == 'seleccionar':
        parte = punto.parte
    elif operacion == 'guardar':
        if not tecnica.plano_de_trabajo(ref, exp):
            raise PermissionDenied('El marcado exige un borrador de trabajo propio.')
        if parte_id or punto:
            parte = ref.partes.filter(pk=parte_id or punto.parte_id).first()
            if not parte: raise ValidationError('Componente de otro plano.')
            if posicion and posicion != parte.posicion or descripcion and descripcion != parte.descripcion:
                raise ValidationError('El componente fuente se conserva. Seleccione otro componente para cambiar la asociación.')
        else:
            parte = ref.partes.filter(posicion=posicion.strip()).first()
            if parte and parte.descripcion != descripcion.strip():
                raise ValidationError('Este número ya tiene otra descripción. Seleccione el componente existente.')
            if not parte:
                parte = registrar_componente_trabajo(usuario=usuario, empresa=empresa, referencia_id=ref.pk,
                    diagnostico_id=diag.pk, clave=uuid.uuid5(key,'componente'),
                    datos=dict(posicion=posicion.strip(), descripcion=descripcion.strip(), unidad=unidad.strip(),
                               pagina=str(pagina)))
    if operacion != 'seleccionar':
        posiciones = [dict(parte=p.parte_id,pagina=p.pagina,x=str(p.x),y=str(p.y))
                      for p in mapa.posiciones.order_by('pk') if not punto or p.pk != punto.pk] if mapa else []
        if operacion == 'guardar':
            posiciones.append(dict(parte=parte.pk,pagina=pagina,x=str(x),y=str(y)))
        mapa = guardar_mapa(usuario=usuario, empresa=empresa, referencia_id=ref.pk,
            base_version=mapa.version if mapa else 0, posiciones=posiciones, clave=uuid.uuid5(key,'mapa'))
        if operacion == 'guardar': punto = mapa.posiciones.order_by('-pk').first()
    if operacion != 'retirar':
        pieza = diag.piezas.filter(parte=parte).order_by('pk').first()
        if operacion != 'seleccionar' or not pieza:
            qty = cantidad if cantidad is not None else parte.cantidad_referencia
            if qty is None:
                from .models import Auditoria
                anterior = Auditoria.objects.filter(expediente=exp, accion='PLANO_POSICION_TRABAJO',
                    detalle__diagnostico=diag.pk, detalle__parte=parte.pk,
                    detalle__operacion='guardar').order_by('-pk').first()
                if anterior: qty = anterior.detalle.get('cantidad')
            if qty is None:
                raise ValidationError('Indique la cantidad antes de seleccionar esta pieza.')
            guardar_seleccion(usuario=usuario, empresa=empresa, diagnostico_id=diag.pk,
                version=diag.version_edicion, clave=uuid.uuid5(key,'seleccion'), pieza_id=pieza.pk if pieza else None,
                fotografias=fotografias,
                datos=dict(origen='BIBLIOTECA', parte_id=parte.pk, posicion_plano_id=punto.pk,
                    cantidad=qty, unidad=parte.unidad or unidad or (pieza.unidad if pieza else ''),
                    motivo_unidad_pendiente='Unidad pendiente de identificación' if not (parte.unidad or unidad) else '',
                    condicion=condicion.strip() or (pieza.condicion if pieza else 'Pendiente de inspección'),
                    accion=pieza.accion if pieza else 'EVALUAR', observaciones=pieza.observaciones if pieza else ''))
    tecnica.evento(exp, usuario, 'PLANO_POSICION_TRABAJO', clave=str(clave), huella=tecnica._digest(payload),
        resultado=ref.pk, diagnostico=diag.pk, operacion=operacion,
        parte=parte.pk if operacion!='retirar' else punto.parte_id,
        punto=punto.pk if punto else None, mapa_version=mapa.version,
        cantidad=str(cantidad) if cantidad is not None else None,
        pagina=pagina, x=str(x) if x is not None else None, y=str(y) if y is not None else None)
    return ref


@transaction.atomic
def guardar_seleccion(*, usuario, empresa, diagnostico_id, version, clave, datos=None,
                      pieza_id=None, fotografias=(), operacion='guardar', evidencia_id=None):
    obj = Diagnostico.objects.get(pk=diagnostico_id)
    exp = tecnica.expediente_autorizado(usuario, empresa, obj.expediente_id, {'TALLER'}, True)
    obj.refresh_from_db()
    payload = dict(diagnostico=obj.pk, version=version, pieza=pieza_id, datos=datos,
        operacion=operacion, evidencia=evidencia_id, fotos=[_hash(f) for f in fotografias])
    old = tecnica._retry(exp, clave, 'PIEZA_PLANO_GUARDADA', payload)
    if old:
        return old
    tecnica._editable(usuario, empresa, obj, version)
    tecnica.seleccion_editable(obj)
    key = uuid.UUID(str(clave))
    if operacion == 'retirar_foto':
        foto = obj.evidencias.filter(pk=evidencia_id, pieza_id=pieza_id, pieza__isnull=False).first()
        if not foto:
            raise PermissionDenied('La fotografía no pertenece a la pieza de este diagnóstico.')
        tecnica.quitar_hijo(usuario=usuario, empresa=empresa, tipo='DIAGNOSTICO', revision_id=obj.pk,
            version=obj.version_edicion, clave=uuid.uuid5(key, 'retirar'), hijo_id=foto.pk)
        result = pieza_id
    elif operacion == 'retirar':
        if not obj.piezas.filter(pk=pieza_id).exists():
            raise PermissionDenied('Pieza de otro diagnóstico.')
        tecnica.quitar_hijo(usuario=usuario, empresa=empresa, tipo='DIAGNOSTICO', revision_id=obj.pk,
            version=obj.version_edicion, clave=uuid.uuid5(key, 'retirar'), hijo_id=pieza_id, pieza=True)
        result = pieza_id
    elif operacion == 'guardar':
        if not datos or datos.get('accion') not in {'REEMPLAZAR', 'REPARAR', 'REUTILIZAR', 'EVALUAR'}:
            raise ValidationError('Seleccione expresamente reemplazar, reparar, reutilizar o evaluar.')
        if fotografias:
            if len(fotografias) > 6:
                raise ValidationError('Adjunte como máximo seis fotografías por guardado.')
            validar_fotografias(fotografias)
        pieza = tecnica.agregar_pieza(usuario=usuario, empresa=empresa, diagnostico_id=obj.pk,
            version=obj.version_edicion, clave=uuid.uuid5(key, 'pieza'), datos=datos, pieza_id=pieza_id)
        for index, archivo in enumerate(fotografias):
            if pieza.fotografias.filter(documento__hash_sha256=_hash(archivo)).exists():
                continue
            obj.refresh_from_db()
            evidencia = tecnica.agregar_evidencia(usuario=usuario, empresa=empresa, tipo='DIAGNOSTICO',
                revision_id=obj.pk, version=obj.version_edicion, clave=uuid.uuid5(key, 'foto-' + str(index)),
                clase='DETALLE', leyenda='Pieza: ' + pieza.descripcion[:180], archivo=archivo)
            evidencia.pieza = pieza
            evidencia.save(update_fields=['pieza'])
        result = pieza.pk
    else:
        raise ValidationError('Operación de selección inválida.')
    tecnica._event(exp, usuario, clave, 'PIEZA_PLANO_GUARDADA', payload, result)
    return result



def leer_excel_materiales(archivo):
    """Read the supplied five-column XLSX into the existing reviewable CSV format."""
    import re
    import zipfile
    from xml.etree import ElementTree as ET

    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    headers = {'PARTE #', 'UNITS', 'CODIGO', 'DESCRIPCION', 'CHECK'}
    if archivo.size > 2_000_000:
        raise ValidationError('El Excel excede 2 MB.')
    try:
        archivo.seek(0)
        with zipfile.ZipFile(archivo) as book:
            infos = book.infolist()
            if len(infos) > 200 or sum(i.file_size for i in infos) > 20_000_000:
                raise ValueError('El archivo es demasiado grande.')
            def xml(name):
                data = book.read(name)
                if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                    raise ValueError('XML no permitido.')
                return ET.fromstring(data)
            shared = []
            if 'xl/sharedStrings.xml' in book.namelist():
                shared = [''.join(x.text or '' for x in si.findall('.//m:t', ns))
                          for si in xml('xl/sharedStrings.xml').findall('m:si', ns)]
            sheets = [i.filename for i in infos if re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', i.filename)]
            candidates = []
            for sheet in sheets:
                matrix = []
                for row in xml(sheet).findall('.//m:sheetData/m:row', ns):
                    cells = {}
                    for cell in row.findall('m:c', ns):
                        match = re.fullmatch(r'([A-Z]+)[0-9]+', cell.get('r', ''))
                        if not match:
                            raise ValueError('Referencia de celda inválida.')
                        if cell.find('m:f', ns) is not None:
                            raise ValueError('Las fórmulas requieren revisión y no se importan.')
                        kind = cell.get('t', 'n')
                        value = cell.findtext('m:v', default='', namespaces=ns)
                        if kind == 's':
                            value = shared[int(value)]
                        elif kind == 'inlineStr':
                            value = ''.join(x.text or '' for x in cell.findall('.//m:is//m:t', ns))
                        elif kind not in ('n', 'str'):
                            if value:
                                raise ValueError('Tipo de celda no admitido.')
                        cells[match.group(1)] = value.strip()
                    matrix.append(cells)
                for index, row in enumerate(matrix):
                    mapping = {v.upper(): k for k, v in row.items() if v.upper() in headers}
                    if set(mapping) == headers:
                        if not any(existing_sheet == sheet for existing_sheet, _, _ in candidates):
                            candidates.append((sheet, matrix[index + 1:], mapping))
                        elif candidates[-1][2] != mapping:
                            raise ValueError('La tabla cambia de columnas en la misma hoja.')
            if len(candidates) != 1:
                raise ValueError('Se requiere una sola tabla con las cinco columnas indicadas.')
            _, matrix, columns = candidates[0]
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=['posicion', 'numero_parte', 'descripcion', 'unidad', 'cantidad_referencia', 'pagina'], lineterminator='\n')
            writer.writeheader()
            for row in matrix:
                part, units, code, description = (row.get(columns[k], '') for k in ('PARTE #', 'UNITS', 'CODIGO', 'DESCRIPCION'))
                if (part.upper(), units.upper(), code.upper(), description.upper()) == ('PARTE #', 'UNITS', 'CODIGO', 'DESCRIPCION'):
                    continue
                # Rows containing only a pre-numbered empty template are not components.
                if not units and not code and not description:
                    continue
                writer.writerow(dict(posicion=part, numero_parte=code, descripcion=description,
                                     unidad='', cantidad_referencia=units, pagina=''))
            text = output.getvalue()
            if not leer_materiales(text, permitir_incompletas=True):
                raise ValueError('La tabla no contiene piezas.')
            return text
    except (zipfile.BadZipFile, KeyError, IndexError, ET.ParseError, ValueError, OverflowError) as exc:
        raise ValidationError('No se pudo leer el Excel: ' + str(exc)) from exc
    finally:
        archivo.seek(0)

def leer_materiales(texto, permitir_incompletas=False):
    if len(texto.encode('utf-8')) > 250000:
        raise ValidationError('La lista de materiales es demasiado extensa.')
    reader = csv.DictReader(io.StringIO(texto.lstrip('\ufeff')))
    campos = {'posicion', 'numero_parte', 'descripcion', 'unidad', 'cantidad_referencia', 'pagina'}
    if set(reader.fieldnames or []) != campos or len(reader.fieldnames) != len(campos):
        raise ValidationError('Use las seis columnas de la plantilla CSV, separadas por comas.')
    rows, posiciones = [], set()
    for line, row in enumerate(reader, 2):
        if len(rows) >= 500 or None in row or any(value is None for value in row.values()):
            raise ValidationError(f'Fila {line}: columnas incompletas o demasiadas filas.')
        row = {key: value.strip() for key, value in row.items()}
        if not row['posicion'] or (not permitir_incompletas and not row['descripcion']) or row['posicion'] in posiciones:
            raise ValidationError(f'Fila {line}: verifique posición única y descripción original.')
        for field, limit in [('posicion', 80), ('numero_parte', 120), ('unidad', 80), ('pagina', 40), ('descripcion', 20000)]:
            if len(row[field]) > limit:
                raise ValidationError(f'Fila {line}: {field} excede la longitud permitida.')
        try:
            quantity = Decimal(row['cantidad_referencia']) if row['cantidad_referencia'] else None
            if quantity is not None and (not quantity.is_finite() or quantity <= 0 or quantity.as_tuple().exponent < -6 or quantity >= Decimal('100000000000000')):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValidationError(f'Fila {line}: deje la cantidad vacía si no consta; si consta debe ser positiva.') from None
        row['cantidad_referencia'] = quantity
        posiciones.add(row['posicion'])
        rows.append(row)
    return rows


@transaction.atomic
def importar_m20(*, usuario, empresa, proyecto_id, archivo, materiales, procedencia, autorizacion,
                 confirmado, clave, sustituye_id=None, version_fabricante='', marca='', modelo='M20A', operacion='', rotacion='', ratio='3:1'):
    tecnica._liblock(usuario, empresa, {'ADMIN', 'OFICINA'})
    if modelo.strip().upper() != 'M20A' or ''.join(ratio.split()) != '3:1':
        raise ValidationError('Este importador requiere modelo M20A y relacion 3:1.')
    if confirmado is not True:
        raise ValidationError('La revisión humana del original y de todas las filas es obligatoria.')
    rows = leer_materiales(materiales, permitir_incompletas=True)
    if sustituye_id and rows:
        raise ValidationError('Una revisión con partes heredadas se revisa en el editor de borrador; use solo la cabecera CSV.')
    payload = dict(hash=_hash(archivo), materiales=materiales, proyecto=proyecto_id,
        procedencia=procedencia, autorizacion=autorizacion, sustituye=sustituye_id, fabricante=version_fabricante,
        revision_humana=True, marca=marca, modelo=modelo, operacion=operacion, rotacion=rotacion, ratio=ratio)
    old = tecnica._libretry(empresa, clave, 'PLANO_M20_IMPORTADO', payload)
    if old:
        return ReferenciaTecnica.objects.get(pk=old)
    from pypdf import PdfReader
    try:
        archivo.seek(0)
        reader = PdfReader(archivo)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
            raise ValueError()
        text = ' '.join((page.extract_text() or '') for page in reader.pages).upper()
        compact = ''.join(text.split())
        if 'M20A-HNRC' not in text or '3:1' not in compact or not ('FIG.1' in compact or 'FIGURE#1' in compact):
            raise ValueError()
    except Exception as exc:
        raise ValidationError('No se verificó la identidad M20A-HNRC, 3:1, figura 1 en el PDF. No se importó.') from exc
    finally:
        archivo.seek(0)
    ref = tecnica.guardar_referencia(usuario=usuario, empresa=empresa,
        clave=uuid.uuid5(uuid.UUID(str(clave)), 'referencia'), archivo=archivo,
        datos=dict(codigo='M20A-HNRC-FIG1', tipo='PLANO', titulo='M20A-HNRC · 3:1 · Figura 1 · Bill of Materials',
            marca=marca, modelo=modelo, operacion=operacion, rotacion=rotacion, ratio=ratio, version_fabricante=version_fabricante,
            procedencia=procedencia, autorizacion_uso=autorizacion, proyecto_id=proyecto_id,
            sustituye_id=sustituye_id))
    # A new revision may have inherited parts; importing into it must not silently
    # merge or overwrite the source BOM. Review it via the normal draft editor.
    for index, row in enumerate(rows):
        tecnica.guardar_parte(usuario=usuario, empresa=empresa, referencia_id=ref.pk, datos=row,
            clave=uuid.uuid5(uuid.UUID(str(clave)), 'parte-' + str(index)))
    tecnica._libevent(empresa, usuario, clave, 'PLANO_M20_IMPORTADO', payload, ref.pk)
    return ref

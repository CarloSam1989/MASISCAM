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
    tecnica._liblock(usuario, empresa)  # PLANTA only, no ADMIN/superuser equivalence.
    ref = ReferenciaTecnica.objects.get(pk=referencia_id, empresa=empresa)
    payload = dict(referencia=ref.pk, base=base_version, posiciones=posiciones)
    old = tecnica._libretry(empresa, clave, 'PLANO_MAPA_CREADO', payload)
    if old:
        return MapaPlano.objects.get(pk=old)
    if ref.tipo != 'PLANO' or ref.estado != 'PUBLICADA':
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
        hash_documento=ref.documento.hash_sha256, autor=usuario, clave=clave))
    for punto in puntos:
        punto.mapa = mapa
        tecnica._save(punto)
    tecnica._libevent(empresa, usuario, clave, 'PLANO_MAPA_CREADO', payload, mapa.pk)
    return mapa


@transaction.atomic
def guardar_seleccion(*, usuario, empresa, diagnostico_id, version, clave, datos=None,
                      pieza_id=None, fotografias=(), operacion='guardar', evidencia_id=None):
    obj = Diagnostico.objects.get(pk=diagnostico_id)
    exp = tecnica.expediente_autorizado(usuario, empresa, obj.expediente_id, {'TECNICO','TALLER'}, True)
    obj.refresh_from_db()
    payload = dict(diagnostico=obj.pk, version=version, pieza=pieza_id, datos=datos,
        operacion=operacion, evidencia=evidencia_id, fotos=[_hash(f) for f in fotografias])
    old = tecnica._retry(exp, clave, 'PIEZA_PLANO_GUARDADA', payload)
    if old:
        return old
    tecnica._editable(usuario, empresa, obj, version)
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


def leer_materiales(texto):
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
        if not row['posicion'] or not row['descripcion'] or row['posicion'] in posiciones:
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
                 confirmado, clave, sustituye_id=None, version_fabricante=''):
    tecnica._liblock(usuario, empresa)
    if confirmado is not True:
        raise ValidationError('La revisión humana del original y de todas las filas es obligatoria.')
    rows = leer_materiales(materiales)
    if sustituye_id and rows:
        raise ValidationError('Una revisión con partes heredadas se revisa en el editor de borrador; use solo la cabecera CSV.')
    payload = dict(hash=_hash(archivo), materiales=materiales, proyecto=proyecto_id,
        procedencia=procedencia, autorizacion=autorizacion, sustituye=sustituye_id, fabricante=version_fabricante,
        revision_humana=True)
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
            modelo='M20A', ratio='3:1', version_fabricante=version_fabricante,
            procedencia=procedencia, autorizacion_uso=autorizacion, proyecto_id=proyecto_id,
            sustituye_id=sustituye_id))
    # A new revision may have inherited parts; importing into it must not silently
    # merge or overwrite the source BOM. Review it via the normal draft editor.
    for index, row in enumerate(rows):
        tecnica.guardar_parte(usuario=usuario, empresa=empresa, referencia_id=ref.pk, datos=row,
            clave=uuid.uuid5(uuid.UUID(str(clave)), 'parte-' + str(index)))
    tecnica._libevent(empresa, usuario, clave, 'PLANO_M20_IMPORTADO', payload, ref.pk)
    return ref

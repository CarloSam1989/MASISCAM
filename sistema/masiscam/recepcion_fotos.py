"""Scoped read-only access to original photographs. No Drive writes or copies."""
import hashlib
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import ArchivoRegistro, Documento, EvidenciaRecepcion

MIMES = ('image/jpeg', 'image/png', 'image/webp')


def fuentes(recepcion):
    equipo = recepcion.expediente.registro.equipo
    documentos = Documento.objects.filter(equipo=equipo, proyecto=equipo.proyecto,
        tipo_mime__in=MIMES).exclude(estado_sincronizacion='ARCHIVADO')
    archivos = ArchivoRegistro.objects.filter(registro__equipo=equipo,
        estado='DISPONIBLE', tipo_mime__in=MIMES).exclude(drive_file_id__isnull=True).exclude(drive_file_id='')
    return documentos, archivos


def candidatos(recepcion):
    result, hashes = [], set()
    for e in recepcion.evidencias.filter(tipo='FOTO').select_related('documento').order_by('pk'):
        result.append((str(e.pk), e.documento.nombre_original))
        hashes.add(e.documento.hash_sha256)
    for prefix, qs in zip(('d', 'a'), fuentes(recepcion)):
        for source in qs.order_by('pk'):
            if source.hash_sha256 in hashes:
                continue
            hashes.add(source.hash_sha256)
            result.append((f'{prefix}:{source.pk}', source.nombre_original if prefix == 'd' else source.nombre))
    return result


def origen(recepcion, token, bloquear=False):
    token = str(token)
    if ':' not in token:
        try:
            pk = int(token)
            qs = recepcion.evidencias.filter(pk=pk, tipo='FOTO').select_related('documento')
            if bloquear:
                qs = qs.select_for_update()
            return qs.get().documento
        except (ValueError, EvidenciaRecepcion.DoesNotExist):
            raise ValidationError('Fotografía fuera del equipo autorizado o no disponible.') from None
    try:
        prefix, pk = token.split(':')
        qs = fuentes(recepcion)[{'d': 0, 'a': 1}[prefix]]
        if bloquear:
            qs = qs.select_for_update()
        return qs.get(pk=int(pk))
    except (ValueError, KeyError, Documento.DoesNotExist, ArchivoRegistro.DoesNotExist):
        raise ValidationError('Fotografía fuera del equipo autorizado o no disponible.') from None


def leer(source):
    from .recepcion_services import validar_fotografias
    if isinstance(source, Documento):
        nombre, mime = source.nombre_original, source.tipo_mime
        if source.archivo:
            stream = source.archivo.open('rb')
        else:
            from .drive_documents import EquipoDriveDocuments
            stream, _, _ = EquipoDriveDocuments(source.equipo).download(source.drive_file_id)
    else:
        from .drive_documents import EquipoDriveDocuments
        nombre, mime = source.nombre, source.tipo_mime
        stream, _, _ = EquipoDriveDocuments(source.registro.equipo).download(source.drive_file_id)
    with stream:
        data = stream.read(30 * 1024 * 1024 + 1)
    if len(data) != source.tamano or hashlib.sha256(data).hexdigest() != source.hash_sha256:
        raise ValidationError('Integridad de fotografía no verificable.')
    validar_fotografias([SimpleUploadedFile(nombre, data, content_type=mime)])
    return data

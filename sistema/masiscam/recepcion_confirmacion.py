"""Confirmación local de recepción; no cierra el mantenimiento ni aprueba trabajos."""
import hashlib
import json
import logging
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (ConfirmacionRecepcion, EmisionGuiaIngreso, EvidenciaRecepcion,
                     ExpedienteMantenimiento, FotoRecepcionClasificada)
from .recepcion_services import (autorizar, evento, generar_guia, validar_fotografias,
                                verificar_copia_persistente, _guardar_documento, _hash)

POLITICA = "recepcion-2026-11-06"
logger = logging.getLogger(__name__)


def habilitada():
    return bool(getattr(settings, "MASISCAM_RECEPCION_LOCAL", False))


def _sincronizar(guia_id):
    # After commit only. Queue failures must not disguise a committed confirmation.
    try:
        from .recepcion_tasks import generar_guia_ingreso
        generar_guia_ingreso(guia_id)
    except Exception:
        logger.exception("Recepción confirmada localmente; sincronización pendiente para guía %s", guia_id)


def confirmar_recepcion(*, usuario, empresa, expediente_id, clave, placa,
                        motivo_sin_placa="", motivo_sin_guia="", general_id=None,
                        placa_id=None, foto_general=None, foto_placa=None):
    if not habilitada():
        raise PermissionDenied("La confirmación está habilitada únicamente en el entorno local autorizado.")
    autorizar(usuario, empresa, escribir=True)
    try:
        clave = uuid.UUID(str(clave))
        general_id = str(general_id) if general_id and ':' in str(general_id) else (int(general_id) if general_id else None)
        placa_id = str(placa_id) if placa_id and ':' in str(placa_id) else (int(placa_id) if placa_id else None)
    except (ValueError, TypeError):
        raise ValidationError("Clave o evidencia inválida.") from None
    motivo_sin_placa, motivo_sin_guia = motivo_sin_placa.strip(), motivo_sin_guia.strip()
    if placa not in {"CON_PLACA", "SIN_PLACA"}:
        raise ValidationError({"placa": "Declare si el equipo tiene placa."})
    if len(motivo_sin_placa) > 10000 or len(motivo_sin_guia) > 10000:
        raise ValidationError("Justificación demasiado larga.")
    for archivo in (foto_general, foto_placa):
        if archivo:
            validar_fotografias([archivo])
    payload = dict(placa=placa, motivo_sin_placa=motivo_sin_placa, motivo_sin_guia=motivo_sin_guia,
                   general_id=general_id, placa_id=placa_id,
                   general_hash=_hash(foto_general) if foto_general else None,
                   placa_hash=_hash(foto_placa) if foto_placa else None)
    huella = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        exp = ExpedienteMantenimiento.objects.select_for_update().filter(pk=expediente_id, empresa=empresa).first()
        if not exp or exp.registro.equipo.proyecto.empresa_id != empresa.pk:
            raise PermissionDenied("Expediente fuera del ámbito autorizado.")
        from .recepcion_services import autorizar_expediente
        autorizar_expediente(usuario, empresa, exp)
        autorizar(usuario, empresa, escribir=True)
        anterior = ConfirmacionRecepcion.objects.filter(recepcion=exp.recepcion).select_related("guia__documento").first()
        if anterior:
            if anterior.huella != huella:
                raise ValidationError("Recepción ya confirmada con datos diferentes; no se modifica.")
            verificar_copia_persistente(anterior.guia.documento)
            transaction.on_commit(lambda: _sincronizar(anterior.guia_id))
            return anterior
        if ConfirmacionRecepcion.objects.filter(clave=clave).exists() or EmisionGuiaIngreso.objects.filter(clave=clave).exists():
            raise ValidationError("Clave utilizada en otra solicitud.")
        if placa == "SIN_PLACA" and not motivo_sin_placa:
            raise ValidationError({"motivo_sin_placa": "Justifique la ausencia de placa."})
        if placa == "CON_PLACA" and motivo_sin_placa:
            raise ValidationError({"motivo_sin_placa": "No corresponde una excepción en un equipo con placa."})
        if placa == "SIN_PLACA" and (placa_id or foto_placa):
            raise ValidationError({"placa": "No adjunte placa si declara SIN_PLACA."})
        if not exp.recepcion.guia_remision and not motivo_sin_guia:
            raise ValidationError({"motivo_sin_guia": "Documente por qué no existe guía de remisión."})
        if exp.recepcion.guia_remision and motivo_sin_guia:
            raise ValidationError({"motivo_sin_guia": "Existe una guía registrada; no corresponde motivo de ausencia."})
        elegidas = []
        referencias = []
        hashes_elegidos = set()
        for clase, evidencia_id, archivo in (("GENERAL", general_id, foto_general), ("PLACA", placa_id, foto_placa)):
            if clase == "PLACA" and placa == "SIN_PLACA":
                continue
            if bool(evidencia_id) == bool(archivo):
                raise ValidationError({"general_id" if clase == "GENERAL" else "placa_id":
                    f"Seleccione una fotografía {clase} o cargue una nueva, sin combinar ambas opciones."})
            if evidencia_id and ':' in str(evidencia_id):
                from .recepcion_fotos import origen, leer
                from .models import Documento, FotoRecepcionExistente
                source = origen(exp.recepcion, evidencia_id, bloquear=True)
                leer(source)
                if source.hash_sha256 in hashes_elegidos:
                    raise ValidationError('GENERAL y PLACA deben ser fotografías distintas.')
                hashes_elegidos.add(source.hash_sha256)
                ref = FotoRecepcionExistente.objects.create(recepcion=exp.recepcion, clase=clase,
                    **({'documento': source} if isinstance(source, Documento) else {'archivo_registro': source}))
                referencias.append(dict(referencia_id=ref.pk, token=str(evidencia_id),
                    hash_sha256=source.hash_sha256, tipo='FOTO', clasificacion=clase,
                    nombre_original=source.nombre_original if isinstance(source, Documento) else source.nombre))
                evento(exp, usuario, 'FOTO_EXISTENTE_CLASIFICADA', origen=str(evidencia_id), clase=clase, hash=source.hash_sha256)
                continue
            if archivo:
                doc = _guardar_documento(exp, usuario, archivo, "FOTOGRAFIAS")
                evidencia = EvidenciaRecepcion.objects.create(recepcion=exp.recepcion, documento=doc, tipo="FOTO")
            else:
                evidencia = exp.recepcion.evidencias.filter(pk=evidencia_id, tipo="FOTO").select_related("documento").first()
                if not evidencia:
                    raise ValidationError("La fotografía no pertenece a esta recepción.")
                doc = evidencia.documento
            verificar_copia_persistente(doc)
            with doc.archivo.open("rb") as stream:
                from django.core.files.uploadedfile import SimpleUploadedFile
                validar_fotografias([SimpleUploadedFile(doc.nombre_original, stream.read(), content_type=doc.tipo_mime)])
            if doc.hash_sha256 in hashes_elegidos:
                raise ValidationError("GENERAL y PLACA deben ser evidencias distintas.")
            hashes_elegidos.add(doc.hash_sha256)
            clasificacion, _ = FotoRecepcionClasificada.objects.get_or_create(evidencia=evidencia, defaults={"clase": clase})
            if clasificacion.clase != clase:
                raise ValidationError("La fotografía ya tiene otra clasificación.")
            elegidas.append((evidencia, clase))
        # Verify every frozen attachment, including the remisión PDF.
        evidencias = list(exp.recepcion.evidencias.select_related("documento").order_by("pk"))
        for evidencia in evidencias:
            verificar_copia_persistente(evidencia.documento)
        ahora = timezone.now()
        actual = exp.guias.order_by("-version").first()
        snapshot = {**exp.recepcion.snapshot, "version": (actual.version if actual else 0) + 1,
            "plantilla": "recepcion-v3", "pendiente_validacion": False, "politica": POLITICA,
            "placa": placa, "motivo_sin_placa": motivo_sin_placa, "motivo_sin_guia": motivo_sin_guia,
            "confirmado_en": ahora.isoformat(), "confirmado_por": usuario.get_full_name() or usuario.username,
            "evidencias": [{"documento_id": e.documento_id, "documento__hash_sha256": e.documento.hash_sha256,
                "tipo": e.tipo, "clasificacion": next((c for elegido, c in elegidas if elegido.pk == e.pk), "")}
                for e in evidencias], 'fotos_existentes': referencias}
        guia = EmisionGuiaIngreso.objects.create(expediente=exp, clave=clave, version=snapshot["version"],
            snapshot=snapshot, creado_por=usuario)
        evento(exp, usuario, "GUIA_RESERVADA", version=guia.version)
        generar_guia(guia.pk)
        guia.refresh_from_db()
        verificar_copia_persistente(guia.documento)
        confirmacion = ConfirmacionRecepcion.objects.create(recepcion=exp.recepcion, guia=guia, clave=clave,
            huella=huella, placa=placa, motivo_sin_placa=motivo_sin_placa, motivo_sin_guia=motivo_sin_guia,
            politica=POLITICA, responsable=usuario, confirmado_en=ahora)
        evento(exp, usuario, "RECEPCION_CONFIRMADA", version=guia.version, hash=guia.hash_sha256, politica=POLITICA)
        transaction.on_commit(lambda: _sincronizar(guia.pk))
        return confirmacion

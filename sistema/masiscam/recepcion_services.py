"""Recepción inmutable. No transiciones técnicas ni autorizaciones comerciales."""
import hashlib
import json
import uuid
from pathlib import Path

from PIL import Image
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F

from accounts.models import Empresa
from .access import acceso_cliente_usuario, rol_masiscam_usuario
from .models import (Auditoria, ContadorExpediente, Documento, EmisionGuiaIngreso,
                     Equipo, EvidenciaRecepcion, ExpedienteMantenimiento, RecepcionEquipo,
                     RegistroEquipo)


def autorizar(usuario, empresa, escribir=False):
    if not usuario.is_authenticated or not usuario.is_active or not empresa or not empresa.activa or acceso_cliente_usuario(usuario):
        raise PermissionDenied("Recepción no autorizada.")
    rol = rol_masiscam_usuario(usuario=usuario, empresa=empresa)
    permitidos = {"OFICINA", "TALLER"} if escribir else {"OFICINA", "TALLER", "PLANTA", "GERENCIA", "ADMIN", "CONSULTA"}
    if not rol or rol.rol not in permitidos:
        raise PermissionDenied("El rol no permite esta operación de recepción.")
    return rol


def expedientes_autorizados(usuario, empresa):
    rol = autorizar(usuario, empresa)
    qs = ExpedienteMantenimiento.objects.filter(empresa=empresa)
    from .access import AUDITORES_INACTIVOS
    if rol.rol not in AUDITORES_INACTIVOS:
        qs=qs.exclude(registro__equipo__estado='INACTIVO')
    if rol.rol == "TALLER":
        from .access import equipos_taller
        qs = qs.filter(registro__equipo__in=equipos_taller(usuario=usuario, empresa=empresa),
            cliente_contractual_id=F('registro__equipo__cliente_id'))
    return qs


def autorizar_expediente(usuario, empresa, exp):
    from .access import validar_consulta_equipo
    validar_consulta_equipo(exp.registro.equipo,autorizar(usuario,empresa).rol)
    if not expedientes_autorizados(usuario, empresa).filter(pk=exp.pk).exists():
        raise PermissionDenied("Expediente fuera del ámbito autorizado.")


def _hash(archivo):
    archivo.seek(0)
    digest = hashlib.sha256()
    for chunk in archivo.chunks():
        digest.update(chunk)
    archivo.seek(0)
    return digest.hexdigest()


def validar_fotografias(archivos):
    from .forms import ArchivosRegistroField
    archivos = ArchivosRegistroField().clean(archivos)
    for archivo in archivos:
        if not archivo.content_type.startswith("image/"):
            raise ValidationError("Adjunte solo fotografías JPEG, PNG o WebP.")
        try:
            with Image.open(archivo) as img:
                if img.width * img.height > 25_000_000:
                    raise ValidationError("Fotografía mayor a 25 megapíxeles.")
                img.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ValidationError("Fotografía inválida.") from None
        finally:
            archivo.seek(0)
    return archivos


def evento(expediente, usuario, accion, **detalle):
    return Auditoria.objects.create(empresa_id=expediente.empresa_id,
        proyecto_id=expediente.registro.equipo.proyecto_id, expediente=expediente,
        usuario=usuario, accion=accion, objeto_tipo="ExpedienteMantenimiento",
        objeto_id=str(expediente.pk), detalle=detalle)


def verificar_copia_persistente(documento):
    """Read-back verification: storage.save succeeding is not proof of content."""
    digest, tamano = hashlib.sha256(), 0
    try:
        with documento.archivo.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
                tamano += len(block)
    except OSError:
        raise ValidationError("No se pudo verificar la copia persistente del documento.") from None
    if tamano != documento.tamano or digest.hexdigest() != documento.hash_sha256:
        raise ValidationError("La copia persistente no coincide con su tamaño o SHA-256.")


def _guardar_documento(expediente, usuario, archivo, categoria):
    """Copy-on-write; no overwrite or deletion of existing storage paths."""
    digest = _hash(archivo)
    equipo = expediente.registro.equipo
    doc = Documento.objects.filter(proyecto=equipo.proyecto, hash_sha256=digest,
        estado_sincronizacion__in=["PENDIENTE", "SUBIENDO", "SINCRONIZADO"]).first()
    if doc:
        # Do not bind a shared legacy document (could already be public).
        raise ValidationError("El archivo ya existe en el proyecto; no se duplica ni se cambia su visibilidad.")
    doc = Documento(proyecto=equipo.proyecto, equipo=equipo, categoria=categoria,
        titulo=archivo.name[:200], nombre_original=archivo.name[:255],
        tipo_mime=archivo.content_type, tamano=archivo.size, hash_sha256=digest,
        subido_por=usuario, publico=False)
    doc.archivo.save(f"recepcion-{uuid.uuid4().hex}-{digest[:12]}{Path(archivo.name).suffix.lower()}", archivo, save=False)
    verificar_copia_persistente(doc)
    doc.save()
    return doc


def abrir_recepcion(*, usuario, empresa, equipo_id, cliente_id, clave, tipo, fecha,
                    condicion_ingreso, observaciones="", guia_remision="", fotografias=(), guia_archivo=None):
    autorizar(usuario, empresa, escribir=True)
    clave = uuid.UUID(str(clave))
    if tipo not in {"MANTENIMIENTO", "REPARACION"} or not condicion_ingreso.strip() or not fecha:
        raise ValidationError("Indique tipo, fecha y condición de ingreso.")
    if len(condicion_ingreso) > 10000 or len(observaciones) > 10000 or len(guia_remision) > 150:
        raise ValidationError("Datos de recepción demasiado largos.")
    fotos = validar_fotografias(list(fotografias))
    if guia_archivo:
        from .forms import DocumentoForm
        if not guia_remision.strip() or guia_archivo.content_type != "application/pdf":
            raise ValidationError("La guía adjunta requiere número y contenido PDF.")
        validator = DocumentoForm(files={"archivo": guia_archivo})
        validator.cleaned_data = {"archivo": guia_archivo}
        validator.clean_archivo()
    hashes = [_hash(a) for a in fotos] + ([_hash(guia_archivo)] if guia_archivo else [])
    if len(hashes) != len(set(hashes)):
        raise ValidationError("No repita archivos en la recepción.")
    payload = dict(equipo=int(equipo_id), cliente=int(cliente_id), tipo=tipo, fecha=fecha.isoformat(),
        condicion=condicion_ingreso.strip(), observaciones=observaciones.strip(),
        guia=guia_remision.strip(), archivos=sorted(hashes))
    huella = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        # The existing Drive services lock the first Empresa. Take the same lock
        # first, then company/equipment/record, preventing inverse lock ordering.
        Empresa.objects.select_for_update().order_by("pk").first()
        Empresa.objects.select_for_update().get(pk=empresa.pk, activa=True)
        autorizar(usuario, empresa, escribir=True)
        equipo = Equipo.objects.select_for_update().select_related("cliente", "proyecto").filter(
            pk=equipo_id, proyecto__empresa=empresa, cliente_id=cliente_id,
            cliente__empresa=empresa).exclude(estado="INACTIVO").exclude(proyecto__estado="ARCHIVADO").first()
        if not equipo:
            raise PermissionDenied("Equipo o cliente fuera del ámbito autorizado.")
        if autorizar(usuario, empresa, escribir=True).rol == "TALLER":
            from .access import equipos_taller
            if not equipos_taller(usuario=usuario, empresa=empresa).filter(pk=equipo.pk).exists():
                raise PermissionDenied("Equipo no vinculado a su trabajo.")
        registro = RegistroEquipo.objects.filter(equipo=equipo, clave_creacion=clave).first()
        if registro:
            exp = ExpedienteMantenimiento.objects.filter(registro=registro).first()
            if not exp or exp.huella_solicitud != huella:
                raise ValidationError("Clave de envío utilizada con datos diferentes.")
            return exp
        contador, _ = ContadorExpediente.objects.get_or_create(empresa=empresa)
        contador = ContadorExpediente.objects.select_for_update().get(pk=contador.pk)
        contador.ultimo_numero += 1
        contador.save(update_fields=["ultimo_numero"])
        registro = RegistroEquipo(equipo=equipo, tipo=tipo, fecha=fecha.date(), clave_creacion=clave)
        registro._sin_drive_recepcion = True
        registro.save()  # Do not renumber old records or create/move their folders.
        exp = ExpedienteMantenimiento.objects.create(registro=registro, empresa=empresa,
            cliente_contractual=equipo.cliente, correlativo=contador.ultimo_numero,
            numero=f"EXP-E{empresa.pk}-{contador.ultimo_numero:08d}", huella_solicitud=huella, creado_por=usuario)
        if rol_masiscam_usuario(usuario=usuario, empresa=empresa).rol == 'TALLER':
            from .models import AsignacionTecnica
            AsignacionTecnica.objects.create(expediente=exp, tecnico=usuario, asignado_por=usuario,
                motivo='Responsable del ingreso de su equipo en TALLER', clave=uuid.uuid4())
            evento(exp, usuario, 'TALLER_ASIGNADO_INGRESO', responsable=usuario.pk)
        snapshot = dict(numero=exp.numero, empresa=empresa.nombre, ruc=equipo.cliente.ruc,
            cliente=equipo.cliente.razon_social, equipo=equipo.nombre, tipo_producto=equipo.tipo_producto,
            marca=equipo.marca, modelo=equipo.modelo, serie=equipo.numero_serie,
            ratio=equipo.ratio, potencia=equipo.potencia_hp, fecha=fecha.isoformat(),
            responsable=usuario.get_full_name() or usuario.username, condicion=condicion_ingreso.strip(),
            observaciones=observaciones.strip(), guia=guia_remision.strip(), tipo=tipo)
        # Capture only existing information. Empty values remain pending, not
        # invented measurements or silently completed master data.
        for campo in ("fabricante", "rpm", "service_factor", "rotacion", "tipo_aceite", "ubicacion", "sector"):
            snapshot[campo] = getattr(equipo, campo)
        snapshot["datos_tecnicos_pendientes"] = [campo for campo in
            ("marca", "modelo", "serie", "ratio", "potencia", "rpm", "service_factor", "rotacion", "tipo_aceite")
            if not str(snapshot.get(campo) or "").strip()]
        recepcion = RecepcionEquipo.objects.create(expediente=exp, fecha=fecha, responsable=usuario,
            condicion_ingreso=condicion_ingreso.strip(), observaciones=observaciones.strip(),
            guia_remision=guia_remision.strip(), snapshot=snapshot)
        for archivo, clase in [(a, "FOTO") for a in fotos] + ([(guia_archivo, "GUIA")] if guia_archivo else []):
            doc = _guardar_documento(exp, usuario, archivo, "FOTOGRAFIAS" if clase == "FOTO" else "OTROS")
            EvidenciaRecepcion.objects.create(recepcion=recepcion, documento=doc, tipo=clase)
        evento(exp, usuario, "RECEPCION_REGISTRADA", huella=huella)
    return exp


def reservar_guia(*, usuario, empresa, expediente_id, clave, nueva_version=False):
    autorizar(usuario, empresa, escribir=True)
    with transaction.atomic():
        exp = ExpedienteMantenimiento.objects.select_for_update().get(pk=expediente_id, empresa=empresa)
        autorizar_expediente(usuario, empresa, exp)
        from .models import ConfirmacionRecepcion
        confirmada = ConfirmacionRecepcion.objects.filter(recepcion=exp.recepcion).select_related("guia").first()
        if confirmada:
            return confirmada.guia
        existente = EmisionGuiaIngreso.objects.filter(clave=clave).first()
        if existente:
            if existente.expediente_id != exp.pk:
                raise ValidationError("Clave de emisión utilizada en otro expediente.")
            return existente
        actual = exp.guias.order_by("-version").first()
        # A refreshed page/new nonce is still a request for the current guide.
        # New versions require explicit intent, and pending jobs are reused.
        if actual and (not nueva_version or not actual.documento_id):
            return actual
        version = (actual.version if actual else 0) + 1
        snapshot = dict(exp.recepcion.snapshot)
        snapshot["evidencias"] = list(exp.recepcion.evidencias.values("documento_id", "documento__hash_sha256", "tipo"))
        snapshot["version"] = version
        snapshot["plantilla"] = "recepcion-v2"
        snapshot["pendiente_validacion"] = True
        guia = EmisionGuiaIngreso.objects.create(expediente=exp, version=version, clave=clave,
            snapshot=snapshot, creado_por=usuario)
        evento(exp, usuario, "GUIA_RESERVADA", version=version)
        return guia


def generar_guia(guia_id):
    """Retryable local generation. No Drive calls while holding DB locks."""
    from .recepcion_pdf import construir_guia
    guia = EmisionGuiaIngreso.objects.select_related("expediente__registro__equipo", "creado_por").get(pk=guia_id)
    if guia.documento_id:
        verificar_copia_persistente(guia.documento)
        return guia.documento_id
    fotos = []
    for item in guia.snapshot.get("evidencias", []):
        if item["tipo"] != "FOTO":
            continue
        doc = Documento.objects.get(pk=item["documento_id"], evidencia_recepcion__recepcion__expediente=guia.expediente)
        with doc.archivo.open("rb") as stream:
            data = stream.read()
        if hashlib.sha256(data).hexdigest() != item["documento__hash_sha256"]:
            raise ValidationError("Integridad de fotografía no verificable.")
        fotos.append(data)
    contenido = construir_guia(guia.snapshot, fotos)
    with transaction.atomic():
        guia = EmisionGuiaIngreso.objects.select_for_update().get(pk=guia_id)
        if guia.documento_id:
            verificar_copia_persistente(guia.documento)
            return guia.documento_id
        archivo = ContentFile(contenido, name=f"{guia.expediente.numero}-v{guia.version}.pdf")
        archivo.content_type = "application/pdf"
        doc = _guardar_documento(guia.expediente, guia.creado_por, archivo, "INFORMES")
        guia.documento, guia.hash_sha256 = doc, doc.hash_sha256
        guia.estado, guia.error = "GENERADA", ""
        guia.save(update_fields=["documento", "hash_sha256", "estado", "error"])
        evento(guia.expediente, guia.creado_por, "GUIA_GENERADA", version=guia.version, hash=doc.hash_sha256)
        return doc.pk

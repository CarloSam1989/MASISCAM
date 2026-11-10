from django.urls import reverse
import hashlib
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.utils.http import content_disposition_header
import logging

from .access import masiscam_access_required
from .models import Documento, EmisionGuiaIngreso, ExpedienteMantenimiento
from .recepcion_forms import RecepcionForm
from .recepcion_services import autorizar, abrir_recepcion, reservar_guia, expedientes_autorizados

logger = logging.getLogger(__name__)


@require_GET
@masiscam_access_required
def expedientes(request):
    autorizar(request.user, request.empresa_activa)
    from django.core.paginator import Paginator
    qs = expedientes_autorizados(request.user, request.empresa_activa).select_related("cliente_contractual", "registro__equipo")
    return render(request, "masiscam/recepcion_listado.html", {"pagina": Paginator(qs, 25).get_page(request.GET.get("page")),
        "puede_recepcion": request.rol_masiscam and request.rol_masiscam.rol in {"OFICINA", "TALLER"}})


@require_http_methods(["GET", "POST"])
@masiscam_access_required
def recepcion_crear(request):
    autorizar(request.user, request.empresa_activa, escribir=True)
    form = RecepcionForm(request.POST or None, request.FILES or None, empresa=request.empresa_activa, usuario=request.user)
    if request.method == "GET" and request.GET.get("equipo"):
        equipo_id = request.GET["equipo"]
        if not equipo_id.isascii() or not equipo_id.isdecimal() or len(equipo_id) > 18:
            raise Http404
        equipo = get_object_or_404(form.fields["equipo"].queryset, pk=equipo_id)
        form.initial.update(equipo=equipo.pk, cliente=equipo.cliente_id)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data["equipo_id"] = data.pop("equipo").pk
        data["cliente_id"] = data.pop("cliente").pk
        try:
            exp = abrir_recepcion(usuario=request.user, empresa=request.empresa_activa, **data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if request.rol_masiscam.rol == 'TALLER':
                return redirect(reverse('masiscam:taller_equipo', args=[exp.registro.equipo_id]) + f'?expediente={exp.pk}')
            return redirect("masiscam:expediente_detalle", pk=exp.pk)
    equipo = form.fields['equipo'].queryset.filter(pk=form.initial.get('equipo')).first() if form.initial.get('equipo') else None
    return render(request, "masiscam/recepcion_form.html", {"form": form,"equipo":equipo}, status=400 if request.method == "POST" else 200)


@require_GET
@masiscam_access_required
def expediente_detalle(request, pk):
    autorizar(request.user, request.empresa_activa)
    exp = get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    return _detalle(request, exp)


def _detalle(request, exp, form=None, status=200):
    from django.conf import settings
    from .recepcion_confirmacion import habilitada
    from .recepcion_forms import ConfirmacionRecepcionForm
    from .recepcion_services import verificar_copia_persistente
    confirmacion = getattr(exp.recepcion, 'confirmacion', None)
    taller = request.rol_masiscam.rol == 'TALLER'
    asignado = exp.asignaciones_tecnicas.filter(tecnico=request.user,fin__isnull=True).exists()
    pendientes=[]
    if not confirmacion:
        if not exp.recepcion.evidencias.filter(tipo='FOTO',clasificacion__clase='GENERAL').exists():
            pendientes.append('Para confirmar la recepción, adjunta una fotografía general del equipo')
        pendientes.append('Adjunta o selecciona la fotografía de placa; si no existe, justifica su ausencia.')
        if not exp.recepcion.guia_remision:
            pendientes.append('Indica el motivo de ausencia de guía de remisión.')
        if not habilitada(): pendientes.append('El responsable del sistema debe habilitar la confirmación de recepción.')
    elif taller and not asignado:
        pendientes.append('PLANTA debe asignar el responsable para continuar la inspección y selección de piezas.')
    elif taller and not settings.MASISCAM_TECNICA_LOCAL:
        pendientes.append('El responsable del sistema debe habilitar el módulo técnico para continuar.')
    guias=list(exp.guias.select_related('documento').order_by('-version'))
    for guia in guias:
        guia.disponible=False
        if guia.documento_id:
            try: verificar_copia_persistente(guia.documento)
            except (ValidationError,OSError): pass
            else: guia.disponible=True
    pasos=[dict(nombre=n,estado='Listo' if i==0 else 'Pendiente') for i,n in enumerate(
        ['Registrar equipo','Seleccionar piezas','Oficina cotiza y envía','Taller repara','Generar informe'])]
    paso=2
    if settings.MASISCAM_TECNICA_LOCAL and request.rol_masiscam.rol in {'TALLER','OFICINA','PLANTA','GERENCIA'}:
        from .flujo_services import resumen
        try: estado=resumen(exp,request.user)
        except (PermissionDenied,ValidationError): pass
        else: pasos,paso=estado['pasos'],estado['paso']
    continuar = bool(taller and asignado and settings.MASISCAM_TECNICA_LOCAL)
    return render(request, "masiscam/recepcion_detalle.html", {"expediente": exp,
        "equipo":exp.registro.equipo,"es_taller":taller,"es_oficina":request.rol_masiscam.rol=='OFICINA',"pendientes":pendientes,"navegacion_integrada":True,
        "guias":guias,"pasos":pasos,"paso":paso,"continuar":continuar,
        "guia_principal":next((g for g in guias if g.disponible),None),
        "snapshot": exp.recepcion.snapshot, "clave_emision": uuid.uuid4(),
        "confirmacion": confirmacion, "cierre_local": habilitada(),
        "form_confirmacion": form if form is not None else ConfirmacionRecepcionForm(recepcion=exp.recepcion),
        "puede_recepcion": request.rol_masiscam and request.rol_masiscam.rol in {"OFICINA", "TALLER"}}, status=status)


@require_POST
@masiscam_access_required
def recepcion_finalizar(request, pk):
    from .recepcion_confirmacion import confirmar_recepcion, habilitada
    from .recepcion_forms import ConfirmacionRecepcionForm
    if not habilitada():
        raise Http404
    autorizar(request.user, request.empresa_activa, escribir=True)
    exp = get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    form = ConfirmacionRecepcionForm(request.POST, request.FILES, recepcion=exp.recepcion)
    if form.is_valid():
        data = form.cleaned_data.copy()
        try:
            confirmar_recepcion(usuario=request.user, empresa=request.empresa_activa, expediente_id=pk, **data)
        except ValidationError as exc:
            if hasattr(exc, "error_dict"):
                for campo, errores in exc.error_dict.items():
                    form.add_error(campo if campo in form.fields else None, errores)
            else:
                form.add_error(None, exc)
        except OSError:
            form.add_error(None, "No se pudo almacenar la guía o sus evidencias. Recepción sin finalizar; reintente.")
        else:
            messages.success(request, "Recepción confirmada con guía persistente. No constituye aprobación técnica ni comercial.")
            if request.rol_masiscam.rol == "TALLER":
                return redirect(reverse('masiscam:taller_equipo', args=[exp.registro.equipo_id]) + f'?expediente={exp.pk}')
            return redirect("masiscam:expediente_detalle", pk=pk)
    return _detalle(request, exp, form, status=400)


@require_POST
@masiscam_access_required
def guia_emitir(request, pk):
    autorizar(request.user, request.empresa_activa, escribir=True)
    get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    try:
        clave = uuid.UUID(request.POST.get("clave", ""))
        guia = reservar_guia(usuario=request.user, empresa=request.empresa_activa, expediente_id=pk, clave=clave,
                             nueva_version=request.POST.get("nueva_version") == "1")
    except (ValueError, ValidationError):
        return HttpResponse("Clave de emisión inválida o reutilizada.", status=400)
    from .recepcion_tasks import generar_guia_ingreso
    try:
        generar_guia_ingreso.apply_async(args=[guia.pk], retry=False)
    except Exception:
        messages.warning(request, "Emisión reservada. Reintente la generación desde el expediente cuando el worker esté disponible.")
    return redirect("masiscam:expediente_detalle", pk=pk)


@require_POST
@masiscam_access_required
def guia_reintentar(request, pk, guia_pk):
    autorizar(request.user, request.empresa_activa, escribir=True)
    exp = get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    guia = get_object_or_404(EmisionGuiaIngreso, pk=guia_pk, expediente=exp)
    from .recepcion_tasks import generar_guia_ingreso
    try:
        generar_guia_ingreso.apply_async(args=[guia.pk], retry=False)
    except Exception:
        messages.warning(request, "El worker no está disponible; la emisión y su número se conservan.")
    return redirect("masiscam:expediente_detalle", pk=pk)


@require_GET
@masiscam_access_required
def recepcion_documento(request, pk, documento_pk):
    autorizar(request.user, request.empresa_activa)
    exp = get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    from django.db.models import Q
    doc = get_object_or_404(Documento.objects.filter(Q(guia_ingreso__expediente=exp) |
        Q(evidencia_recepcion__recepcion__expediente=exp)).distinct(), pk=documento_pk)
    try:
        archivo = doc.archivo.open("rb")
    except (FileNotFoundError, OSError):
        return HttpResponse("Archivo no disponible; requiere recuperación desde respaldo.", status=503)
    digest = hashlib.sha256()
    for block in iter(lambda: archivo.read(65536), b""):
        digest.update(block)
    if digest.hexdigest() != doc.hash_sha256:
        archivo.close()
        return HttpResponse("Integridad documental no verificable.", status=503)
    archivo.seek(0)
    response = FileResponse(archivo, as_attachment=True, filename=doc.nombre_original, content_type=doc.tipo_mime)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
@masiscam_access_required
def recepcion_foto_previsualizar(request, pk):
    autorizar(request.user, request.empresa_activa)
    exp = get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    from .recepcion_fotos import candidatos, leer, origen
    token = request.GET.get("token", "")
    if not token or len(token) > 40 or token not in {value for value, _ in candidatos(exp.recepcion)}:
        raise Http404
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        from .drive_documents import DocumentUnavailable
        image_data = leer(origen(exp.recepcion, token))
        with Image.open(BytesIO(image_data)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((360, 270))
            preview = BytesIO()
            image.convert("RGB").save(preview, format="JPEG", quality=82, optimize=True)
    except (DocumentUnavailable, OSError, ValidationError):
        logger.exception("No se pudo verificar una fotografía de recepción (expediente %s).", pk)
        return HttpResponse("Fotografía no disponible; no se pudo verificar su integridad.", status=503)
    response = HttpResponse(preview.getvalue(), content_type="image/jpeg")
    response["Content-Disposition"] = content_disposition_header(False, "vista-previa.jpg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response

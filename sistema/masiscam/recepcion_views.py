import hashlib
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .access import masiscam_access_required
from .models import Documento, EmisionGuiaIngreso, ExpedienteMantenimiento
from .recepcion_forms import RecepcionForm
from .recepcion_services import autorizar, abrir_recepcion, reservar_guia, expedientes_autorizados


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
            return redirect("masiscam:expediente_detalle", pk=exp.pk)
    return render(request, "masiscam/recepcion_form.html", {"form": form}, status=400 if request.method == "POST" else 200)


@require_GET
@masiscam_access_required
def expediente_detalle(request, pk):
    autorizar(request.user, request.empresa_activa)
    exp = get_object_or_404(expedientes_autorizados(request.user, request.empresa_activa), pk=pk)
    return _detalle(request, exp)


def _detalle(request, exp, form=None, status=200):
    from .recepcion_confirmacion import habilitada
    from .recepcion_forms import ConfirmacionRecepcionForm
    return render(request, "masiscam/recepcion_detalle.html", {"expediente": exp,
        "snapshot": exp.recepcion.snapshot, "clave_emision": uuid.uuid4(),
        "confirmacion": getattr(exp.recepcion, "confirmacion", None), "cierre_local": habilitada(),
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
        for name in ("general_id", "placa_id"):
            data[name] = data[name].pk if data[name] else None
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

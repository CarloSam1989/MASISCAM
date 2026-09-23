from base64 import b64encode
from io import BytesIO
import mimetypes

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError, SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST


from .drive_documents import EquipoDriveDocuments, DocumentUnavailable
from .access import masiscam_access_required, permiso_masiscam_required, tiene_permiso
from .forms import ClienteForm, DocumentoForm, EquipoForm, FichaEquipoForm, ProyectoForm, VisibilidadProyectoForm, RegistroEquipoForm, datos_cliente
from .models import Cliente, Auditoria, Documento, Equipo, Proyecto, RegistroEquipo, RolMasiscam
from accounts.models import Perfil
from .services import auditar, encolar_carpeta_registro, encolar_drive
from .tasks import crear_carpeta_proyecto, sincronizar_documento


def _proyecto(request, pk):
    return get_object_or_404(Proyecto.objects.prefetch_related("equipos", "documentos"), pk=pk, empresa=request.empresa_activa)


def _contexto_permisos(request):
    return {f"puede_{p}": tiene_permiso(request, p) for p in ("crear", "editar", "archivar", "equipos", "documentos", "reemplazar", "qr", "visibilidad", "historial", "usuarios")}


def _equipos_autorizados(request):
    equipos = Equipo.objects.filter(proyecto__empresa=request.empresa_activa)
    if request.cliente_usuario:
        equipos = equipos.filter(cliente=request.cliente_usuario, cliente__empresa=request.empresa_activa)
    return equipos.select_related("cliente", "proyecto")


def _comprobar_equipo_cliente(request, equipo):
    if request.cliente_usuario and (equipo.cliente_id != request.cliente_usuario.pk
            or equipo.proyecto.empresa_id != request.cliente_usuario.empresa_id
            or equipo.cliente.empresa_id != request.empresa_activa.pk):
        raise PermissionDenied("Este producto no pertenece a su cliente.")
    return equipo


def _documentos_cliente(request):
    return Documento.objects.filter(
        proyecto__empresa=request.empresa_activa,
        equipo__in=_equipos_autorizados(request),
        publico=True, estado_sincronizacion=Documento.Sincronizacion.SINCRONIZADO,
    )


@require_GET
@masiscam_access_required
def cliente_productos(request):
    if not request.cliente_usuario:
        return redirect("masiscam:dashboard")
    equipos = _equipos_autorizados(request)
    if equipos.count() == 1:
        return redirect("masiscam:ficha_detalle", pk=equipos.first().pk)
    return render(request, "masiscam/cliente_productos.html", {"equipos": equipos})


def _es_modal(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _respuesta_formulario_modal(request, template, contexto, form, status=200):
    if _es_modal(request):
        html = render_to_string(template, contexto, request=request)
        return JsonResponse({"success": False, "html": html}, status=status)
    return render(request, template, contexto, status=status)


def _productos(equipos, cliente=None):
    conteos = dict(equipos.order_by().values("tipo_producto").annotate(total=Count("pk")).values_list("tipo_producto", "total"))
    return [
        {"codigo": codigo, "nombre": nombre, "total": conteos.get(codigo, 0),
         "url": reverse("masiscam:producto_listado", args=[codigo.lower()]) + (f"?cliente={cliente.pk}" if cliente else "")}
        for codigo, nombre in Equipo.TipoProducto.choices
        if cliente is None or conteos.get(codigo, 0)
    ]


@masiscam_access_required
def dashboard(request):
    productos = _productos(Equipo.objects.filter(proyecto__empresa=request.empresa_activa))
    return render(request, "masiscam/dashboard.html", {"productos": productos})

@masiscam_access_required
def producto_listado(request, tipo):
    codigo = tipo.upper()
    nombre = dict(Equipo.TipoProducto.choices).get(codigo)
    if nombre is None:
        raise Http404
    base = Equipo.objects.filter(proyecto__empresa=request.empresa_activa, tipo_producto=codigo)
    cliente = None
    cliente_id = request.GET.get("cliente", "").strip()
    clientes = Cliente.objects.filter(empresa=request.empresa_activa)
    if cliente_id:
        if not cliente_id.isascii() or not cliente_id.isdecimal() or len(cliente_id) > 18:
            raise Http404
        cliente = get_object_or_404(clientes, pk=int(cliente_id))
        base = base.filter(cliente=cliente)
    equipos = base.select_related("proyecto", "cliente")
    q = request.GET.get("q", "").strip()
    if q:
        equipos = equipos.filter(
            Q(cliente__razon_social__icontains=q) | Q(cliente__nombre_comercial__icontains=q) | Q(cliente__ruc__icontains=q)
            | Q(cliente__isnull=True, proyecto__razon_social__icontains=q)
            | Q(proyecto__nombre__icontains=q) | Q(ubicacion__icontains=q)
            | Q(sector__icontains=q) | Q(nombre__icontains=q) | Q(marca__icontains=q)
            | Q(modelo__icontains=q) | Q(numero_serie__icontains=q)
        )
    contexto = {"equipos": equipos[:200], "q": q, "producto_nombre": nombre, "producto_codigo": codigo, "producto_singular": Equipo(tipo_producto=codigo).producto_singular, "cliente_seleccionado": cliente, "clientes": clientes,
                "total": base.count(), "publicos": base.filter(consulta_publica_activa=True).count(),
                "inactivos": base.filter(estado=Equipo.Estado.INACTIVO).count()}
    contexto.update(_contexto_permisos(request))
    return render(request, "masiscam/producto_listado.html", contexto)


@masiscam_access_required
def clientes(request):
    q = request.GET.get("q", "").strip()
    clientes_qs = Cliente.objects.filter(empresa=request.empresa_activa)
    if request.GET.get("formato") == "json":
        ruc = request.GET.get("ruc", "").strip()[:20]
        encontrados = clientes_qs.filter(ruc__startswith=ruc).order_by("ruc")[:10] if ruc else []
        return JsonResponse({"clientes": [datos_cliente(cliente) for cliente in encontrados]})
    if q:
        clientes_qs = clientes_qs.filter(Q(nombre_comercial__icontains=q) | Q(razon_social__icontains=q) | Q(ruc__icontains=q) | Q(correo__icontains=q) | Q(telefono__icontains=q))
    contexto = {"pagina": Paginator(clientes_qs, 25).get_page(request.GET.get("page")), "q": q}
    contexto.update(_contexto_permisos(request))
    return render(request, "masiscam/clientes.html", contexto)


@masiscam_access_required
def cliente_detalle(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk, empresa=request.empresa_activa)
    contexto = {"cliente": cliente, "productos": _productos(cliente.equipos.filter(proyecto__empresa=request.empresa_activa), cliente),
                "acceso_cliente": RolMasiscam.objects.filter(cliente=cliente).select_related("perfil__user").first()}
    contexto.update(_contexto_permisos(request))
    return render(request, "masiscam/cliente_detalle.html", contexto)


@require_POST
@permiso_masiscam_required("usuarios")
def cliente_usuario_crear(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk, empresa=request.empresa_activa)
    identificacion = cliente.ruc.strip()
    if not identificacion.isascii() or not identificacion.isdecimal() or len(identificacion) not in {10, 13}:
        messages.error(request, "El cliente debe tener una cédula de 10 dígitos o un RUC de 13 dígitos.")
        return redirect("masiscam:cliente_detalle", pk=cliente.pk)
    try:
        with transaction.atomic():
            cliente = Cliente.objects.select_for_update().get(pk=cliente.pk, empresa=request.empresa_activa)
            if RolMasiscam.objects.filter(cliente=cliente).exists():
                messages.info(request, "El cliente ya tiene un usuario vinculado. No se modificó su clave.")
                return redirect("masiscam:cliente_detalle", pk=cliente.pk)
            User = get_user_model()
            if User.objects.filter(username=identificacion).exists():
                messages.error(request, "La identificación ya está registrada como usuario. No se reasignó la cuenta.")
                return redirect("masiscam:cliente_detalle", pk=cliente.pk)
            usuario = User.objects.create_user(username=identificacion, password=identificacion, email=cliente.correo)
            perfil = Perfil.objects.create(user=usuario, empresa=cliente.empresa)
            RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.CLIENTE, cliente=cliente)
            auditar(empresa=request.empresa_activa, usuario=request.user, accion="USUARIO_CLIENTE_CREADO", objeto=cliente)
    except IntegrityError:
        messages.error(request, "Ya existe un usuario con esa identificación o un acceso para este cliente.")
    else:
        messages.success(request, "Usuario creado. El usuario y la clave inicial son la identificación del cliente.")
    return redirect("masiscam:cliente_detalle", pk=cliente.pk)


def _cliente_formulario(request, cliente=None):
    modal = _es_modal(request)
    respuesta_json = "application/json" in request.headers.get("Accept", "") and not modal
    if respuesta_json and request.method == "POST" and cliente is None:
        existente = Cliente.objects.filter(empresa=request.empresa_activa, ruc=request.POST.get("ruc", "").strip()).first()
        if existente:
            return JsonResponse({"cliente": datos_cliente(existente), "creado": False})
    form = ClienteForm(request.POST if request.method == "POST" else None, instance=cliente, empresa=request.empresa_activa)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                guardado = form.save()
                auditar(empresa=request.empresa_activa, usuario=request.user, accion="CLIENTE_EDITADO" if cliente else "CLIENTE_CREADO", objeto=guardado)
        except IntegrityError:
            if respuesta_json and cliente is None:
                existente = Cliente.objects.filter(empresa=request.empresa_activa, ruc=form.cleaned_data["ruc"]).first()
                if existente:
                    return JsonResponse({"cliente": datos_cliente(existente), "creado": False})
            form.add_error("ruc", "Ya existe un cliente con este RUC en la empresa.")
        else:
            if modal:
                messages.success(request, "Cliente creado correctamente." if cliente is None else "Cliente actualizado correctamente.")
                return JsonResponse({"success": True, "id": guardado.pk})
            if respuesta_json:
                return JsonResponse({"cliente": datos_cliente(guardado), "creado": cliente is None}, status=201 if cliente is None else 200)
            messages.success(request, "Cliente guardado correctamente.")
            return redirect("masiscam:cliente_detalle", pk=guardado.pk)
    if modal and request.method == "POST":
        contexto = {"form": form, "cliente": cliente, "titulo": "Editar cliente" if cliente else "Crear cliente"}
        return _respuesta_formulario_modal(request, "masiscam/cliente_form.html", contexto, form, status=400)
    if respuesta_json and request.method == "POST":
        return JsonResponse({"errores": form.errors.get_json_data()}, status=400)
    return render(request, "masiscam/cliente_form.html", {"form": form, "cliente": cliente, "titulo": "Editar cliente" if cliente else "Crear cliente"})


@permiso_masiscam_required("crear")
def cliente_crear(request):
    return _cliente_formulario(request)


@permiso_masiscam_required("editar")
def cliente_editar(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk, empresa=request.empresa_activa)
    return _cliente_formulario(request, cliente)


def _equipo(request, pk):
    if request.cliente_usuario:
        equipo = get_object_or_404(Equipo.objects.select_related("proyecto", "cliente"), pk=pk)
        return _comprobar_equipo_cliente(request, equipo)
    return get_object_or_404(_equipos_autorizados(request), pk=pk)


def _contexto_formulario_producto(request, form, equipo=None):
    producto = Equipo(tipo_producto=form.tipo_producto)
    contexto = {"form": form, "equipo": equipo, "producto_codigo": form.tipo_producto,
                "producto_singular": producto.producto_singular, "titulo_informacion": producto.titulo_informacion,
                "cliente_seleccionado": form.fields["cliente"].queryset.filter(pk=form.cliente_inicial["id"]).first() if form.cliente_inicial else None,
                "titulo": ("Editar " if equipo else "Crear ") + producto.producto_singular.lower(),
                "cliente_form": ClienteForm(empresa=request.empresa_activa, prefix="nuevo")}
    contexto.update(_contexto_permisos(request))
    return contexto


@permiso_masiscam_required("crear")
def ficha_crear(request):
    tipo = request.GET.get("tipo", "REDUCTOR").upper()
    if tipo not in Equipo.TipoProducto.values:
        raise Http404
    cliente_id = request.GET.get("cliente")
    inicial = {}
    if cliente_id:
        if not cliente_id.isdecimal() or len(cliente_id) > 18:
            raise Http404
        inicial["cliente"] = get_object_or_404(Cliente, pk=cliente_id, empresa=request.empresa_activa).pk
    form = FichaEquipoForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        empresa=request.empresa_activa, tipo_producto=tipo, initial=inicial,
    )
    if request.method == "POST" and form.is_valid():
        try:
            equipo = form.save(usuario=request.user)
        except (IntegrityError, ValidationError) as exc:
            form.add_error(None, str(exc))
        else:
            auditar(empresa=request.empresa_activa, usuario=request.user, accion="FICHA_EQUIPO_CREADA", objeto=equipo)
            if _es_modal(request):
                messages.success(request, "Equipo registrado correctamente.")
                return JsonResponse({"success": True, "id": equipo.pk})
            messages.success(request, "Ficha del equipo creada correctamente.")
            return redirect("masiscam:ficha_detalle", pk=equipo.pk)
    contexto = _contexto_formulario_producto(request, form)
    if _es_modal(request) and request.method == "POST":
        return _respuesta_formulario_modal(request, "masiscam/ficha_form.html", contexto, form, status=400)
    return render(request, "masiscam/ficha_form.html", contexto)


@masiscam_access_required
def ficha_detalle(request, pk):
    equipo = _equipo(request, pk)
    placa = equipo.fotografia_placa
    placa_disponible = bool(placa and placa.storage.exists(placa.name))
    if request.GET.get("foto") == "placa":
        if not placa_disponible:
            raise Http404
        try:
            archivo = placa.open("rb")
        except FileNotFoundError:
            raise Http404
        archivo.close()
        return _archivo_protegido(placa)
    return _render_ficha(request, equipo, placa_disponible=placa_disponible)


@require_GET
@masiscam_access_required
def equipo_informe(request, pk):
    equipo = _equipo(request, pk)
    activo = (
        equipo.proyecto.empresa.activa
        and equipo.estado != Equipo.Estado.INACTIVO
        and equipo.proyecto.estado != Proyecto.Estado.ARCHIVADO
    )
    return render(request, "masiscam/equipo_publico.html", {"equipo": equipo, "activo": activo, "privado": True})


def _render_ficha(request, equipo, registro_form=None, placa_disponible=None, status=200):
    if placa_disponible is None:
        placa = equipo.fotografia_placa
        placa_disponible = bool(placa and placa.storage.exists(placa.name))
    contexto = {"equipo": equipo, "placa_disponible": placa_disponible,
                "registros": equipo.registros.all(),
                "registro_form": registro_form if registro_form is not None else RegistroEquipoForm()}
    contexto.update(_contexto_permisos(request))
    if request.cliente_usuario:
        contexto["documentos_cliente"] = _documentos_cliente(request).filter(equipo=equipo, proyecto=equipo.proyecto)
    return render(request, "masiscam/ficha_detalle.html", contexto, status=status)


@require_POST
@permiso_masiscam_required("editar")
def registro_crear(request, pk):
    equipo = _equipo(request, pk)
    form = RegistroEquipoForm(request.POST)
    if not form.is_valid():
        return _render_ficha(request, equipo, registro_form=form, status=400)
    with transaction.atomic():
        registro, creado = RegistroEquipo.objects.get_or_create(
            equipo=equipo, clave_creacion=form.cleaned_data["clave_creacion"],
            defaults={campo: form.cleaned_data[campo] for campo in ("tipo", "fecha", "observacion")},
        )
        if creado:
            auditar(empresa=request.empresa_activa, usuario=request.user, accion="REGISTRO_EQUIPO_CREADO",
                    objeto=registro, proyecto=equipo.proyecto)
    messages.success(request, "Registro guardado. Puede consultar el estado de Drive en el historial.")
    return redirect(reverse("masiscam:ficha_detalle", args=[pk]) + "#historial-registros")


@require_POST
@permiso_masiscam_required("editar")
def registro_reintentar(request, pk, registro_pk):
    equipo = _equipo(request, pk)
    registro = get_object_or_404(RegistroEquipo, pk=registro_pk, equipo=equipo)
    if not registro.drive_folder_id:
        encolar_carpeta_registro(registro.pk)
        registro.refresh_from_db()
        if registro.drive_error:
            messages.error(request, "No se pudo enviar a Drive. El registro sigue guardado; intente nuevamente.")
        else:
            messages.success(request, "Reintento de Drive enviado.")
    return redirect(reverse("masiscam:ficha_detalle", args=[pk]) + "#historial-registros")


@permiso_masiscam_required("editar")
def ficha_editar(request, pk):
    equipo = _equipo(request, pk)
    form = FichaEquipoForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        empresa=request.empresa_activa,
        equipo=equipo,
    )
    if request.method == "POST" and form.is_valid():
        try:
            equipo = form.save(usuario=request.user)
        except (IntegrityError, ValidationError) as exc:
            form.add_error(None, str(exc))
        else:
            auditar(empresa=request.empresa_activa, usuario=request.user, accion="FICHA_EQUIPO_EDITADA", objeto=equipo)
            messages.success(request, "Ficha del equipo actualizada.")
            return redirect("masiscam:ficha_detalle", pk=equipo.pk)
    return render(request, "masiscam/ficha_form.html", _contexto_formulario_producto(request, form, equipo))


@require_POST
@permiso_masiscam_required("archivar")
def ficha_archivar(request, pk):
    equipo = _equipo(request, pk)
    equipo.estado = Equipo.Estado.INACTIVO
    equipo.consulta_publica_activa = False
    equipo.save(update_fields=["estado", "consulta_publica_activa", "actualizado_en"])
    auditar(empresa=request.empresa_activa, usuario=request.user, accion="FICHA_EQUIPO_ARCHIVADA", objeto=equipo)
    messages.success(request, "Ficha archivada y consulta pública desactivada.")
    return redirect("masiscam:producto_listado", tipo=equipo.tipo_producto.lower())


@require_POST
@permiso_masiscam_required("qr")
def equipo_token_regenerar(request, pk):
    equipo = _equipo(request, pk)
    equipo.regenerar_token()
    equipo.save(update_fields=["token_publico", "actualizado_en"])
    auditar(empresa=request.empresa_activa, usuario=request.user, accion="QR_EQUIPO_REGENERADO", objeto=equipo)
    messages.success(request, "Código QR regenerado; el enlace anterior quedó invalidado.")
    return redirect("masiscam:ficha_detalle", pk=pk)


def _url_publica_equipo(equipo):
    ruta = reverse("masiscam:equipo_informe", args=[equipo.pk])
    return f"{settings.MASISCAM_PUBLIC_BASE_URL.rstrip('/')}{ruta}"


def _qr_data_uri(url):
    import qrcode
    salida = BytesIO()
    qrcode.make(url).save(salida, format="PNG")
    return "data:image/png;base64," + b64encode(salida.getvalue()).decode("ascii")


@require_GET
@permiso_masiscam_required("qr")
def equipo_qr(request, pk):
    import qrcode
    equipo = _equipo(request, pk)
    imagen = qrcode.make(_url_publica_equipo(equipo))
    salida = BytesIO()
    imagen.save(salida, format="PNG")
    respuesta = HttpResponse(salida.getvalue(), content_type="image/png")
    disposicion = "attachment" if request.GET.get("download") == "1" else "inline"
    respuesta["Content-Disposition"] = f'{disposicion}; filename="MASISCAM-{equipo.pk}-QR.png"'
    return respuesta


@require_GET
@permiso_masiscam_required("qr")
def equipo_etiqueta(request, pk):
    equipo = _equipo(request, pk)
    empresa_ruc = None  # El logo propio se sirve desde static; pertenece al producto MASISCAM.
    return render(
        request,
        "masiscam/equipo_etiqueta.html",
        {"equipo": equipo, "empresa_ruc": empresa_ruc, "qr_src": _qr_data_uri(_url_publica_equipo(equipo))},
    )


@require_GET
def equipo_publico(request, token):
    equipo = get_object_or_404(Equipo.objects.select_related("proyecto__empresa"), token_publico=token)
    empresa_ruc = None  # El logo propio se sirve desde static; pertenece al producto MASISCAM.
    activo = (
        equipo.proyecto.empresa.activa
        and equipo.consulta_publica_activa
        and equipo.estado != Equipo.Estado.INACTIVO
        and equipo.proyecto.estado != Proyecto.Estado.ARCHIVADO
    )
    documentos, error_documentos = [], False
    if activo and equipo.drive_folder_id:
        try:
            documentos = EquipoDriveDocuments(equipo).list()
        except Exception:
            error_documentos = True
    response = render(
        request,
        "masiscam/equipo_publico.html",
        {
            "equipo": equipo,
            "activo": activo,
            "empresa_ruc": empresa_ruc,
            "documentos_publicos": True, "documentos_drive": documentos,
            "error_documentos": error_documentos,
        },
    )

    return _cabeceras_documentos(response)


def _cabeceras_documentos(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@require_GET
def equipo_documento_drive(request, token, archivo_id):
    equipo = Equipo.objects.select_related("proyecto").filter(
        token_publico=token, consulta_publica_activa=True, proyecto__empresa__activa=True,
    ).exclude(estado=Equipo.Estado.INACTIVO).exclude(proyecto__estado=Proyecto.Estado.ARCHIVADO).first()
    if equipo is None or not equipo.drive_folder_id:
        return _cabeceras_documentos(HttpResponse("Documento no disponible.", status=404))
    try:
        archivo, nombre, mime = EquipoDriveDocuments(equipo).download(archivo_id)
    except DocumentUnavailable:
        return _cabeceras_documentos(HttpResponse("Documento no disponible.", status=404))
    except Exception:
        return _cabeceras_documentos(HttpResponse("Documento no disponible temporalmente.", status=503))
    response = FileResponse(archivo, as_attachment=False, filename=nombre, content_type=mime)
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'"
    return _cabeceras_documentos(response)


@require_GET
def equipo_foto_publica(request, token, tipo):
    equipo = get_object_or_404(
        Equipo.objects.select_related("proyecto"),
        token_publico=token,
        consulta_publica_activa=True,
        proyecto__empresa__activa=True,
    )
    if equipo.estado == Equipo.Estado.INACTIVO or equipo.proyecto.estado == Proyecto.Estado.ARCHIVADO:
        raise Http404
    campo = equipo.fotografia_general if tipo == "equipo" else equipo.fotografia_placa if tipo == "placa" else None
    if not campo:
        raise Http404
    content_type = mimetypes.guess_type(campo.name)[0] or "application/octet-stream"
    return _archivo_protegido(campo)


@require_GET
def imagen_proyecto_publica(request, token, tipo, objeto_pk):
    proyecto = get_object_or_404(Proyecto, token_publico=token, pagina_publica_activa=True, empresa__activa=True)
    if proyecto.estado == Proyecto.Estado.ARCHIVADO:
        raise Http404
    if tipo == "proyecto" and objeto_pk == 0:
        campo = proyecto.imagen_principal
    elif tipo in {"equipo", "placa"} and proyecto.publicar_equipos:
        equipo = get_object_or_404(proyecto.equipos.exclude(estado=Equipo.Estado.INACTIVO), pk=objeto_pk, visible_publico=True)
        campo = equipo.fotografia_general if tipo == "equipo" else equipo.fotografia_placa
    else:
        raise Http404
    if not campo:
        raise Http404
    return _archivo_protegido(campo)


@permiso_masiscam_required("crear")
def proyecto_crear(request):
    form = ProyectoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                proyecto = form.save(commit=False)
                proyecto.empresa = request.empresa_activa
                proyecto.creado_por = request.user
                proyecto.full_clean(exclude=["imagen_principal"])
                proyecto.save()
                auditar(empresa=request.empresa_activa, usuario=request.user, accion="PROYECTO_CREADO", objeto=proyecto)
        except IntegrityError:
            form.add_error("codigo", "Ya existe un proyecto con este código en la empresa.")
        else:
            encolar_drive(crear_carpeta_proyecto, proyecto.pk)
            if _es_modal(request):
                messages.success(request, "Proyecto creado correctamente.")
                return JsonResponse({"success": True, "id": proyecto.pk})
            messages.success(request, "Proyecto creado; la estructura de Drive se procesará en segundo plano.")
            return redirect("masiscam:proyecto_detalle", pk=proyecto.pk)
    contexto = {"form": form, "titulo": "Crear proyecto"}
    if _es_modal(request) and request.method == "POST":
        return _respuesta_formulario_modal(request, "masiscam/form.html", contexto, form, status=400)
    return render(request, "masiscam/form.html", contexto)


@masiscam_access_required
def proyecto_detalle(request, pk):
    proyecto = _proyecto(request, pk)
    contexto = {"proyecto": proyecto, "documento_form": DocumentoForm(proyecto=proyecto), "equipo_form": EquipoForm(empresa=request.empresa_activa)}
    contexto.update(_contexto_permisos(request))
    return render(request, "masiscam/proyecto_detalle.html", contexto)


@permiso_masiscam_required("editar")
def proyecto_editar(request, pk):
    proyecto = _proyecto(request, pk)
    form = ProyectoForm(request.POST or None, request.FILES or None, instance=proyecto)
    if request.method == "POST" and form.is_valid():
        anterior = {campo: getattr(proyecto, campo) for campo in form.changed_data}
        form.save()
        auditar(empresa=request.empresa_activa, usuario=request.user, accion="PROYECTO_EDITADO", objeto=proyecto, detalle={"campos": form.changed_data, "anterior": {k: str(v) for k, v in anterior.items()}})
        messages.success(request, "Proyecto actualizado.")
        return redirect("masiscam:proyecto_detalle", pk=pk)
    return render(request, "masiscam/form.html", {"form": form, "titulo": "Editar proyecto", "proyecto": proyecto})


@permiso_masiscam_required("visibilidad")
def proyecto_visibilidad(request, pk):
    proyecto = _proyecto(request, pk)
    form = VisibilidadProyectoForm(request.POST or None, instance=proyecto)
    if request.method == "POST" and form.is_valid():
        form.save()
        auditar(empresa=request.empresa_activa, usuario=request.user, accion="VISIBILIDAD_ACTUALIZADA", objeto=proyecto, detalle={"campos": form.changed_data})
        messages.success(request, "Visibilidad pública actualizada.")
        return redirect("masiscam:proyecto_detalle", pk=pk)
    return render(request, "masiscam/form.html", {"form": form, "titulo": "Configurar página pública", "proyecto": proyecto})


@require_POST
@permiso_masiscam_required("archivar")
def proyecto_archivar(request, pk):
    proyecto = _proyecto(request, pk)
    proyecto.estado = Proyecto.Estado.ARCHIVADO
    proyecto.save(update_fields=["estado", "actualizado_en"])
    auditar(empresa=request.empresa_activa, usuario=request.user, accion="PROYECTO_ARCHIVADO", objeto=proyecto)
    messages.success(request, "Proyecto archivado.")
    return redirect("masiscam:proyecto_detalle", pk=pk)


@permiso_masiscam_required("equipos")
def equipo_crear(request, pk):
    proyecto = _proyecto(request, pk)
    if request.method == "GET":
        return render(request, "masiscam/_equipo_form.html", {"form": EquipoForm(empresa=request.empresa_activa), "proyecto": proyecto})
    form = EquipoForm(request.POST, request.FILES, instance=Equipo(proyecto=proyecto), empresa=request.empresa_activa)
    if form.is_valid():
        equipo = form.save(commit=False)
        equipo.proyecto = proyecto
        equipo.save()
        form.guardar_ruc()
        auditar(empresa=request.empresa_activa, usuario=request.user, accion="EQUIPO_CREADO", objeto=equipo)
        if _es_modal(request):
            messages.success(request, "Equipo creado correctamente.")
            return JsonResponse({"success": True, "id": equipo.pk})
        messages.success(request, "Equipo registrado.")
    else:
        if _es_modal(request):
            return _respuesta_formulario_modal(request, "masiscam/_equipo_form.html", {"form": form, "proyecto": proyecto}, form, status=400)
        messages.error(request, "Revise los datos del equipo.")
    return redirect("masiscam:proyecto_detalle", pk=pk)


@permiso_masiscam_required("equipos")
def equipo_editar(request, pk, equipo_pk):
    proyecto = _proyecto(request, pk)
    equipo = get_object_or_404(proyecto.equipos, pk=equipo_pk)
    form = EquipoForm(request.POST or None, request.FILES or None, instance=equipo, empresa=request.empresa_activa)
    if request.method == "POST" and form.is_valid():
        form.save()
        auditar(empresa=request.empresa_activa, usuario=request.user, accion="EQUIPO_EDITADO", objeto=equipo, detalle={"campos": form.changed_data})
        return redirect("masiscam:proyecto_detalle", pk=pk)
    return render(request, "masiscam/form.html", {"form": form, "titulo": "Editar equipo", "proyecto": proyecto, "equipo": equipo})


@permiso_masiscam_required("documentos")
def documento_subir(request, pk):
    proyecto = _proyecto(request, pk)
    if request.method == "GET":
        return render(request, "masiscam/_documento_form.html", {"form": DocumentoForm(proyecto=proyecto), "proyecto": proyecto})
    form = DocumentoForm(request.POST, request.FILES, proyecto=proyecto)
    if form.is_valid():
        archivo = form.cleaned_data["archivo"]
        documento = form.save(commit=False)
        documento.proyecto = proyecto
        documento.subido_por = request.user
        documento.nombre_original = archivo.name
        documento.tipo_mime = archivo.content_type
        documento.tamano = archivo.size
        documento.hash_sha256 = archivo.hash_sha256
        try:
            documento.save()
        except IntegrityError:
            messages.error(request, "Este archivo ya fue cargado en el proyecto.")
        else:
            auditar(empresa=request.empresa_activa, usuario=request.user, accion="DOCUMENTO_SUBIDO", objeto=documento)
            encolar_drive(sincronizar_documento, documento.pk)
            if _es_modal(request):
                messages.success(request, "Documento registrado correctamente.")
                return JsonResponse({"success": True, "id": documento.pk})
            messages.success(request, "Documento registrado y enviado a sincronización.")
    else:
        if _es_modal(request):
            return _respuesta_formulario_modal(request, "masiscam/_documento_form.html", {"form": form, "proyecto": proyecto}, form, status=400)
        messages.error(request, "No se pudo cargar: " + "; ".join(sum(form.errors.values(), [])))
    return redirect("masiscam:proyecto_detalle", pk=pk)


@require_POST
@permiso_masiscam_required("reemplazar")
def documento_archivar(request, pk, documento_pk):
    proyecto = _proyecto(request, pk)
    documento = get_object_or_404(proyecto.documentos, pk=documento_pk)
    documento.estado_sincronizacion = Documento.Sincronizacion.ARCHIVADO
    documento.publico = False
    documento.save(update_fields=["estado_sincronizacion", "publico"])
    auditar(empresa=request.empresa_activa, usuario=request.user, accion="DOCUMENTO_ARCHIVADO", objeto=documento)
    messages.success(request, "Documento archivado. El archivo remoto no se eliminó definitivamente.")
    return redirect("masiscam:proyecto_detalle", pk=pk)


@require_POST
@permiso_masiscam_required("documentos")
def documento_reintentar(request, pk, documento_pk):
    proyecto = _proyecto(request, pk)
    documento = get_object_or_404(proyecto.documentos, pk=documento_pk, estado_sincronizacion=Documento.Sincronizacion.ERROR)
    documento.estado_sincronizacion = Documento.Sincronizacion.PENDIENTE
    documento.error_sincronizacion = ""
    documento.save(update_fields=["estado_sincronizacion", "error_sincronizacion"])
    encolar_drive(sincronizar_documento, documento.pk)
    return redirect("masiscam:proyecto_detalle", pk=pk)


@require_POST
@permiso_masiscam_required("qr")
def token_regenerar(request, pk):
    proyecto = _proyecto(request, pk)
    proyecto.regenerar_token()
    proyecto.save(update_fields=["token_publico", "actualizado_en"])
    auditar(empresa=request.empresa_activa, usuario=request.user, accion="TOKEN_REGENERADO", objeto=proyecto)
    messages.success(request, "Token regenerado; el enlace anterior quedó invalidado.")
    return redirect("masiscam:proyecto_detalle", pk=pk)


@require_GET
@permiso_masiscam_required("qr")
def qr_descargar(request, pk):
    import qrcode
    proyecto = _proyecto(request, pk)
    ruta_publica = reverse("masiscam:publico", args=[proyecto.token_publico])
    url = f"{settings.MASISCAM_PUBLIC_BASE_URL.rstrip('/')}{ruta_publica}"
    imagen = qrcode.make(url)
    salida = BytesIO()
    imagen.save(salida, format="PNG")
    respuesta = HttpResponse(salida.getvalue(), content_type="image/png")
    disposicion = "attachment" if request.GET.get("download") == "1" else "inline"
    respuesta["Content-Disposition"] = f'{disposicion}; filename="MASISCAM-{proyecto.pk}-QR.png"'
    auditar(empresa=request.empresa_activa, usuario=request.user, accion="QR_DESCARGADO", objeto=proyecto)
    return respuesta


@require_GET
@permiso_masiscam_required("qr")
def etiqueta_qr(request, pk):
    proyecto = _proyecto(request, pk)
    ruta = reverse("masiscam:publico", args=[proyecto.token_publico])
    url = f"{settings.MASISCAM_PUBLIC_BASE_URL.rstrip('/')}{ruta}"
    return render(request, "masiscam/etiqueta_qr.html", {"proyecto": proyecto, "qr_src": _qr_data_uri(url)})


@masiscam_access_required
def historial(request, pk):
    if not tiene_permiso(request, "historial"):
        raise PermissionDenied("No tiene permiso para ver el historial.")
    proyecto = _proyecto(request, pk)
    return render(request, "masiscam/historial.html", {"proyecto": proyecto, "eventos": proyecto.historial.select_related("usuario")[:200]})


@require_GET
def publico(request, token):
    proyecto = get_object_or_404(Proyecto.objects.prefetch_related("equipos", "documentos"), token_publico=token)
    activo = proyecto.empresa.activa and proyecto.pagina_publica_activa and proyecto.estado != Proyecto.Estado.ARCHIVADO
    documentos = proyecto.documentos.filter(publico=True, estado_sincronizacion=Documento.Sincronizacion.SINCRONIZADO) if activo else Documento.objects.none()
    equipos = proyecto.equipos.filter(visible_publico=True).exclude(estado=Equipo.Estado.INACTIVO) if activo and proyecto.publicar_equipos else proyecto.equipos.none()
    if not proyecto.publicar_descripcion:
        proyecto.descripcion = ""
    return render(request, "masiscam/publico.html", {"proyecto": proyecto, "activo": activo, "documentos": documentos, "equipos": equipos})


@require_GET
def documento_publico(request, token, documento_pk):
    documento = get_object_or_404(Documento.objects.select_related("proyecto"), pk=documento_pk, proyecto__token_publico=token, proyecto__pagina_publica_activa=True, proyecto__empresa__activa=True, publico=True, estado_sincronizacion=Documento.Sincronizacion.SINCRONIZADO)
    if documento.proyecto.estado == Proyecto.Estado.ARCHIVADO:
        raise Http404
    return _archivo_protegido(documento.archivo, filename=documento.nombre_original, attachment=True)


def _archivo_protegido(campo, *, filename=None, attachment=False):
    from pathlib import Path
    if not campo:
        raise Http404
    try:
        root = Path(settings.MEDIA_ROOT).resolve()
        candidate = Path(campo.path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise Http404
        response = FileResponse(campo.open("rb"), as_attachment=attachment,
                                filename=filename or candidate.name,
                                content_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
    except (OSError, ValueError, SuspiciousFileOperation):
        raise Http404 from None
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
@masiscam_access_required
def proyecto_imagen_privada(request, pk):
    return _archivo_protegido(_proyecto(request, pk).imagen_principal)


@require_GET
@masiscam_access_required
def equipo_foto_privada(request, pk, tipo):
    equipo = _equipo(request, pk)
    if tipo not in {"equipo", "placa"}:
        raise Http404
    return _archivo_protegido(equipo.fotografia_general if tipo == "equipo" else equipo.fotografia_placa)


@require_GET
@masiscam_access_required
def documento_privado(request, pk, documento_pk):
    proyecto = _proyecto(request, pk)
    documentos = proyecto.documentos.exclude(estado_sincronizacion=Documento.Sincronizacion.ARCHIVADO)
    if request.cliente_usuario:
        documentos = documentos.filter(equipo__in=_equipos_autorizados(request))
    documento = get_object_or_404(documentos, pk=documento_pk)
    return _archivo_protegido(documento.archivo, filename=documento.nombre_original, attachment=True)


@require_GET
@masiscam_access_required
def proyectos(request):
    contexto = {"proyectos": Proyecto.objects.filter(empresa=request.empresa_activa).order_by("-actualizado_en")[:200]}
    contexto.update(_contexto_permisos(request))
    return render(request, "masiscam/proyectos.html", contexto)

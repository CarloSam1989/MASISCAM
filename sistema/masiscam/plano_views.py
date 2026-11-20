import json
import uuid
from decimal import Decimal
from functools import wraps

from django import forms
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .access import masiscam_access_required
from .models import Diagnostico, ReferenciaTecnica, Proyecto
from .tecnica_forms import BaseForm
from .tecnica_views import local
from . import tecnica_services as tecnica, plano_services as svc


def errores_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except ValidationError as exc:
            return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
        except PermissionDenied:
            return JsonResponse({'error': 'No tiene permiso para esta operación o expediente.'}, status=403)
        except ObjectDoesNotExist:
            return JsonResponse({'error': 'El elemento no está disponible.'}, status=404)
        except IntegrityError:
            return JsonResponse({'error': 'Conflicto de versión o datos protegidos. Recargue antes de continuar.'}, status=409)
        except OSError:
            return JsonResponse({'error': 'No se pudo guardar o leer el archivo. No se confirmó la operación.'}, status=503)
    return wrapped


def contexto(request, pk):
    ref = get_object_or_404(tecnica.referencias_visibles(request.user, request.empresa_activa), pk=pk, tipo='PLANO')
    obj = None
    diagnostico_raw = request.GET.get('diagnostico')
    if not diagnostico_raw and request.rol_masiscam.rol == 'TALLER':
        scope = tecnica.expedientes_operativos(request.user, request.empresa_activa).filter(cierre__isnull=True)
        if ref.expediente_trabajo_id:
            scope = scope.filter(pk=ref.expediente_trabajo_id)
        else:
            scope = scope.filter(registro__equipo_id__in=ref.aplicabilidades.filter(activa=True).values('equipo_id'))
        candidatos = list(scope[:2])
        if len(candidatos) == 1:
            etapa = tecnica._etapa(candidatos[0], 'DIAGNOSTICO')
            diagnostico_raw = etapa.diagnostico_id if etapa else None
    if diagnostico_raw:
        try:
            diagnostico_id = int(diagnostico_raw)
        except ValueError:
            raise Http404 from None
        obj = get_object_or_404(Diagnostico, pk=diagnostico_id)
        rol = tecnica.autorizar_tecnica(request.user, request.empresa_activa)
        tecnica.expediente_autorizado(request.user, request.empresa_activa, obj.expediente_id)
        preliminar = getattr(obj, 'solicitud_repuestos', None)
        if (rol.rol == 'OFICINA' and obj.estado not in {'APROBADO', 'FINALIZADO'}
                and not (preliminar and preliminar.snapshot.get('preliminar'))):
            raise PermissionDenied('Oficina utiliza únicamente el listado aprobado.')
        if not (tecnica.plano_de_trabajo(ref, obj.expediente)
                or ref.aplicabilidades.filter(equipo=obj.expediente.registro.equipo, activa=True).exists()
                or obj.piezas.filter(parte__referencia=ref).exists()):
            raise PermissionDenied('Plano sin aplicabilidad ni cita en este diagnóstico.')
    editable = bool(obj and request.rol_masiscam.rol in tecnica.OPERADORES and obj.tecnico_id == request.user.pk
        and not obj.enviado_en and tecnica.vigente(obj) and not hasattr(obj, 'solicitud_repuestos'))
    return ref, obj, editable


def codigo_parte(parte):
    if not parte:
        return ''
    componente = parte.componente
    return parte.numero_parte or (componente.numero_parte or componente.codigo if componente else '')


def piezas_json(obj):
    if not obj:
        return []
    return [dict(id=p.pk, parte=p.parte_id, posicion=p.posicion_plano_id,
        fuente_url=(reverse('masiscam:plano', args=[p.parte.referencia_id]) + '?diagnostico=' + str(obj.pk)) if p.parte_id else None,
        descripcion=p.descripcion, codigo=p.numero_parte or codigo_parte(p.parte), cantidad=str(p.cantidad), unidad=p.unidad,
        condicion=p.condicion, accion=p.accion, observaciones=p.observaciones, snapshot=p.snapshot,
        fotos=[dict(id=f.pk, url=reverse('masiscam:tecnica_documento', args=[obj.expediente_id, f.documento_id]),
            leyenda=f.leyenda) for f in p.fotografias.all()])
        for p in obj.piezas.select_related('parte__componente').prefetch_related('fotografias').order_by('pk')]


@require_GET
@local
@masiscam_access_required
def plano(request, pk):
    ref, obj, editable = contexto(request, pk)
    from .flujo_forms import PlanoTrabajoForm, EnviarPiezasForm
    return render(request, 'masiscam/plano.html', dict(referencia=ref, diagnostico=obj,
        revision_plano=svc.revision_plano(ref) if ref.expediente_trabajo_id else None,
        plano_form=PlanoTrabajoForm(auto_id='pl_upload_%s') if editable else None,
        enviar_form=EnviarPiezasForm(initial={'version':obj.version_edicion}, auto_id='pl_send_%s') if editable else None,
        editable_plano=editable, configurar=(not ref.expediente_trabajo_id and
            (request.rol_masiscam.rol == 'ADMIN' and ref.estado in {'BORRADOR', 'PUBLICADA'} or
             request.rol_masiscam.rol == 'OFICINA' and ref.estado == 'BORRADOR'))))


@require_GET
@local
@masiscam_access_required
@errores_api
def plano_datos(request, pk):
    ref, obj, editable = contexto(request, pk)
    info = svc.documento_info(ref, request.empresa_activa)
    mapa = ref.mapas.order_by('-version').first()
    vigente = mapa and mapa.hash_documento == ref.documento.hash_sha256
    cantidades = {}
    if obj:
        from .models import Auditoria
        for evento in Auditoria.objects.filter(expediente=obj.expediente,
                accion='PLANO_POSICION_TRABAJO', detalle__diagnostico=obj.pk,
                detalle__operacion='guardar', detalle__resultado=ref.pk).order_by('pk'):
            cantidades[evento.detalle['parte']] = evento.detalle['cantidad']
    return JsonResponse(dict(documento=info, editable=editable, version=obj.version_edicion if obj else None,
        marcable=bool(editable and (tecnica.plano_de_trabajo(ref, obj.expediente) or
            ref.estado == 'PUBLICADA' and ref.aplicabilidades.filter(equipo=obj.expediente.registro.equipo, activa=True).exists())),
        seleccionable=bool(editable and (tecnica.plano_de_trabajo(ref, obj.expediente) or
            ref.estado == 'PUBLICADA' and ref.aplicabilidades.filter(equipo=obj.expediente.registro.equipo, activa=True).exists())),
        estado=obj.estado if obj else None, mapa_version=mapa.version if mapa else 0,
        mapa_provisional=mapa.provisional if mapa else ref.estado == 'BORRADOR',
        puntos=[dict(id=p.pk, parte=p.parte_id, numero=p.numero, pagina=p.pagina,
            x=str(p.x), y=str(p.y)) for p in mapa.posiciones.all()] if vigente else [],
        partes=[dict(id=p.pk, posicion=p.posicion, codigo=codigo_parte(p), descripcion=p.descripcion,
            unidad=p.unidad, cantidad=str(p.cantidad_referencia) if p.cantidad_referencia is not None else '',
            cantidad_seleccion=cantidades.get(p.pk) or '',
            pagina=p.pagina) for p in ref.partes.select_related('componente').order_by('pk')], piezas=piezas_json(obj)))


class PosicionTrabajoForm(BaseForm):
    diagnostico_id = forms.IntegerField(min_value=1)
    version = forms.IntegerField(min_value=1)
    mapa_version = forms.IntegerField(min_value=0)
    operacion = forms.ChoiceField(choices=[(s,s) for s in ('guardar','seleccionar','retirar')])
    punto_id = forms.IntegerField(min_value=1, required=False)
    parte_id = forms.IntegerField(min_value=1, required=False)
    pagina = forms.IntegerField(min_value=1, required=False)
    x = forms.DecimalField(max_digits=9, decimal_places=8, min_value=0, max_value=1, required=False)
    y = forms.DecimalField(max_digits=9, decimal_places=8, min_value=0, max_value=1, required=False)
    posicion = forms.CharField(max_length=80, required=False)
    descripcion = forms.CharField(max_length=20000, required=False)
    cantidad = forms.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0.000001'), required=False)
    condicion = forms.CharField(max_length=20000, required=False)
    unidad = forms.CharField(max_length=80, required=False)

    def clean(self):
        data = super().clean()
        if data.get('operacion') == 'guardar':
            for campo in ('pagina','x','y','cantidad'):
                if data.get(campo) is None: self.add_error(campo, 'Campo obligatorio.')
            if not data.get('parte_id') and not data.get('punto_id'):
                for campo in ('posicion','descripcion'):
                    if not data.get(campo): self.add_error(campo, 'Campo obligatorio.')
        return data


@require_POST
@local
@masiscam_access_required
@errores_api
def plano_posicion(request, pk):
    if request.content_type == 'application/json':
        if len(request.body) > 250000: raise ValidationError('Solicitud demasiado grande.')
        try: data = json.loads(request.body)
        except (ValueError, TypeError): raise ValidationError('Solicitud de posición inválida.') from None
        if not isinstance(data, dict): raise ValidationError('Solicitud de posición inválida.')
    else:
        data = request.POST
    form = PosicionTrabajoForm(data)
    if not form.is_valid():
        return JsonResponse(dict(error='Revise número, descripción, cantidad y coordenadas.', campos=form.errors.get_json_data()),status=400)
    ref = svc.guardar_posicion_trabajo(usuario=request.user, empresa=request.empresa_activa,
        referencia_id=pk, fotografias=request.FILES.getlist('fotografias'), **form.cleaned_data)
    obj = Diagnostico.objects.get(pk=form.cleaned_data['diagnostico_id'])
    query = f'?diagnostico={obj.pk}&flujo=1'
    return JsonResponse(dict(datos_url=reverse('masiscam:plano_datos',args=[ref.pk])+query,
        plano_url=reverse('masiscam:plano',args=[ref.pk])+query,
        posicion_url=reverse('masiscam:plano_posicion',args=[ref.pk]),
        mapa_url=reverse('masiscam:plano_mapa',args=[ref.pk]),
        referencia=dict(titulo=ref.titulo, revision=ref.revision, estado=ref.estado,
            documento_url=reverse('masiscam:biblioteca_documento',args=[ref.pk]),
            componente_url=reverse('masiscam:plano_componente',args=[ref.pk])+query),
        mensaje='Posición y selección guardadas en borrador.' if data['operacion']!='retirar'
                else 'Posición retirada del mapa provisional. El componente y sus selecciones se conservan.'))


@require_POST
@local
@masiscam_access_required
def plano_componente(request, pk):
    from .flujo_forms import ComponenteTrabajoForm
    ref, obj, editable = contexto(request, pk)
    if not editable or not obj:
        raise PermissionDenied('Plano de solo lectura.')
    form = ComponenteTrabajoForm(request.POST)
    if form.is_valid():
        data = dict(form.cleaned_data)
        key = data.pop('clave')
        try:
            svc.registrar_componente_trabajo(usuario=request.user, empresa=request.empresa_activa,
                referencia_id=ref.pk, diagnostico_id=obj.pk, clave=key, datos=data)
        except (ValidationError, IntegrityError) as exc:
            from .tecnica_views import _error
            _error(form, exc)
        else:
            return redirect(reverse('masiscam:plano', args=[pk]) + f'?diagnostico={obj.pk}&flujo=1')
    return render(request, 'masiscam/plano.html', dict(referencia=ref, diagnostico=obj,
        editable_plano=editable, componente_form=form, configurar=False), status=400)


@require_POST
@local
@masiscam_access_required
@errores_api
def plano_mapa(request, pk):
    rol = tecnica.autorizar_tecnica(request.user, request.empresa_activa, {'ADMIN', 'TALLER', 'OFICINA'})
    if rol.rol == 'TALLER':
        ref = ReferenciaTecnica.objects.filter(pk=pk, empresa=request.empresa_activa,
            tipo='PLANO', estado='BORRADOR', expediente_trabajo__isnull=False).first()
        if not ref:
            raise PermissionDenied('TALLER solo marca el plano provisional de su expediente.')
        tecnica.expediente_autorizado(request.user, request.empresa_activa, ref.expediente_trabajo_id, {'TALLER'})
    elif rol.rol == 'OFICINA' and not ReferenciaTecnica.objects.filter(pk=pk, empresa=request.empresa_activa, tipo='PLANO', estado='BORRADOR', expediente_trabajo__isnull=True).exists():
        raise PermissionDenied('OFICINA solo configura planos de biblioteca en borrador.')
    if len(request.body) > 250000:
        raise ValidationError('La configuración excede el tamaño permitido.')
    try:
        data = json.loads(request.body)
        key = uuid.UUID(data['clave'])
        posiciones = data['posiciones']
        version = data['version']
    except (ValueError, KeyError, TypeError):
        raise ValidationError('Solicitud de mapa inválida.') from None
    mapa = svc.guardar_mapa(usuario=request.user, empresa=request.empresa_activa, referencia_id=pk,
        base_version=version, posiciones=posiciones, clave=key)
    return JsonResponse({'version': mapa.version, 'provisional': mapa.provisional,
        'mensaje': 'Posiciones provisionales guardadas; pendientes de revisión de ADMINISTRADOR.' if mapa.provisional else 'Posiciones revisadas y publicadas en una nueva versión.'})


class SeleccionForm(BaseForm):
    version = forms.IntegerField(min_value=1)
    parte_id = forms.IntegerField(min_value=1)
    posicion_plano_id = forms.IntegerField(min_value=1, required=False)
    cantidad = forms.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0.000001'))
    unidad = forms.CharField(max_length=80)
    condicion = forms.CharField(max_length=20000)
    accion = forms.ChoiceField(choices=[(s, s) for s in ('REEMPLAZAR', 'REPARAR', 'REUTILIZAR', 'EVALUAR')])
    observaciones = forms.CharField(max_length=20000, required=False)


@require_POST
@local
@masiscam_access_required
@errores_api
def plano_pieza(request, pk):
    obj = get_object_or_404(Diagnostico, pk=pk)
    tecnica.expediente_autorizado(request.user, request.empresa_activa, obj.expediente_id, {'TALLER'})
    op = request.POST.get('operacion', 'guardar')
    form = SeleccionForm(request.POST) if op == 'guardar' else BaseForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': 'Revise los campos obligatorios y la cantidad positiva.',
            'campos': form.errors.get_json_data()}, status=400)
    data = form.cleaned_data.copy()
    key = data.pop('clave')
    try:
        version = data.pop('version') if op == 'guardar' else int(request.POST['version'])
        pieza_id = int(request.POST['pieza_id']) if request.POST.get('pieza_id') else None
        evidencia_id = int(request.POST['evidencia_id']) if request.POST.get('evidencia_id') else None
    except (ValueError, KeyError):
        raise ValidationError('Identificador o versión inválidos.') from None
    if op == 'guardar':
        data['origen'] = 'BIBLIOTECA'
    svc.guardar_seleccion(usuario=request.user, empresa=request.empresa_activa, diagnostico_id=obj.pk,
        version=version, clave=key, datos=data if op == 'guardar' else None, pieza_id=pieza_id,
        operacion=op, evidencia_id=evidencia_id, fotografias=request.FILES.getlist('fotografias'))
    obj.refresh_from_db()
    return JsonResponse(dict(version=obj.version_edicion, piezas=piezas_json(obj), mensaje='Borrador guardado. No se han autorizado compras ni reparaciones.'))


class CatalogoForm(BaseForm):
    """Alta de un catálogo técnico: independiente de clientes y proyectos."""
    archivo = forms.FileField(label='Plano (PDF)')
    excel = forms.FileField(required=False, label='Excel de repuestos (.xlsx)')
    marca = forms.CharField(max_length=120, label='Marca')
    modelo = forms.CharField(max_length=120, label='Modelo')
    ratio = forms.CharField(max_length=80, label='Relación de transmisión (ratio)')
    rotacion = forms.CharField(max_length=80, label='Rotación')
    operacion = forms.CharField(max_length=80, required=False, label='Operación (solo si distingue configuraciones)')

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        if not archivo.name.lower().endswith('.pdf'):
            raise forms.ValidationError('Seleccione el plano en PDF.')
        return archivo

    def clean_excel(self):
        excel = self.cleaned_data.get('excel')
        if excel and not excel.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Seleccione un archivo .xlsx.')
        return excel

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


@require_http_methods(['GET', 'POST'])
@local
@masiscam_access_required
def plano_importar(request):
    tecnica.autorizar_tecnica(request.user, request.empresa_activa, {'ADMIN', 'OFICINA'})
    from django.contrib import messages
    form = CatalogoForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        try:
            materiales = svc.leer_excel_materiales(datos['excel']) if datos.get('excel') else None
            ref = svc.crear_catalogo(usuario=request.user, empresa=request.empresa_activa, archivo=datos['archivo'],
                marca=datos['marca'], modelo=datos['modelo'], ratio=datos['ratio'], rotacion=datos['rotacion'],
                operacion=datos['operacion'], materiales=materiales, clave=datos['clave'])
        except (ValidationError, IntegrityError, OSError) as exc:
            form.add_error(None, ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'No se pudo guardar el catálogo.')
        else:
            messages.success(request, 'Catálogo guardado como borrador. Revise las piezas, ubique los marcadores y envíelo a ADMINISTRADOR.')
            return redirect('masiscam:biblioteca_referencia', pk=ref.pk)
    return render(request, 'masiscam/plano_importar.html', {'form': form}, status=400 if request.method == 'POST' else 200)


@require_http_methods(['GET', 'POST'])
@local
@masiscam_access_required
def repuestos_importar(request, pk):
    """Excel de repuestos sobre un catálogo existente: vista previa y confirmación explícita."""
    from django.contrib import messages
    tecnica.autorizar_tecnica(request.user, request.empresa_activa, {'ADMIN', 'OFICINA'})
    ref = get_object_or_404(tecnica.referencias_visibles(request.user, request.empresa_activa), pk=pk, tipo='PLANO')
    vista, materiales, error = None, '', None
    if request.method == 'POST':
        try:
            if request.POST.get('op') == 'previsualizar':
                excel = request.FILES.get('excel')
                if not excel or not excel.name.lower().endswith('.xlsx'):
                    raise ValidationError('Seleccione un archivo .xlsx.')
                materiales = svc.leer_excel_materiales(excel)
                vista = svc.previsualizar_repuestos(usuario=request.user, empresa=request.empresa_activa,
                    referencia_id=ref.pk, materiales=materiales)
            elif request.POST.get('op') == 'aplicar':
                materiales = request.POST.get('materiales', '')
                destino = svc.aplicar_repuestos(usuario=request.user, empresa=request.empresa_activa, referencia_id=ref.pk,
                    materiales=materiales, huella=request.POST.get('huella', ''), clave=request.POST.get('clave', ''))
                messages.success(request, 'Repuestos importados' + (' en una nueva revisión borrador; la versión publicada no se modificó.'
                    if destino.pk != ref.pk else ' en el borrador del catálogo.'))
                return redirect('masiscam:biblioteca_referencia', pk=destino.pk)
            else:
                raise ValidationError('Operación inválida.')
        except (ValidationError, IntegrityError, OSError, ValueError) as exc:
            error = ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'No se pudo importar el archivo.'
            if request.POST.get('op') == 'aplicar':
                try:
                    vista = svc.previsualizar_repuestos(usuario=request.user, empresa=request.empresa_activa,
                        referencia_id=ref.pk, materiales=materiales)
                except ValidationError:
                    vista = None
    return render(request, 'masiscam/plano_repuestos_importar.html', dict(referencia=ref, vista=vista,
        materiales=materiales, clave=uuid.uuid4(), error=error), status=400 if error else 200)

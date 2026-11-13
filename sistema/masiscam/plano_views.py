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
    if request.GET.get('diagnostico'):
        try:
            diagnostico_id = int(request.GET['diagnostico'])
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


def piezas_json(obj):
    if not obj:
        return []
    return [dict(id=p.pk, parte=p.parte_id, posicion=p.posicion_plano_id,
        fuente_url=(reverse('masiscam:plano', args=[p.parte.referencia_id]) + '?diagnostico=' + str(obj.pk)) if p.parte_id else None,
        descripcion=p.descripcion, codigo=p.numero_parte, cantidad=str(p.cantidad), unidad=p.unidad,
        condicion=p.condicion, accion=p.accion, observaciones=p.observaciones, snapshot=p.snapshot,
        fotos=[dict(id=f.pk, url=reverse('masiscam:tecnica_documento', args=[obj.expediente_id, f.documento_id]),
            leyenda=f.leyenda) for f in p.fotografias.all()])
        for p in obj.piezas.select_related('parte').prefetch_related('fotografias').order_by('pk')]


@require_GET
@local
@masiscam_access_required
def plano(request, pk):
    ref, obj, editable = contexto(request, pk)
    from .flujo_forms import ComponenteTrabajoForm
    return render(request, 'masiscam/plano.html', dict(referencia=ref, diagnostico=obj,
        componente_form=ComponenteTrabajoForm() if editable and tecnica.plano_de_trabajo(ref, obj.expediente) else None,
        editable_plano=editable, configurar=(request.rol_masiscam.rol == 'PLANTA' and
            (ref.estado == 'PUBLICADA' or ref.estado == 'BORRADOR' and ref.expediente_trabajo_id)
            or editable and request.rol_masiscam.rol == 'TALLER' and tecnica.plano_de_trabajo(ref, obj.expediente))))


@require_GET
@local
@masiscam_access_required
@errores_api
def plano_datos(request, pk):
    ref, obj, editable = contexto(request, pk)
    info = svc.documento_info(ref, request.empresa_activa)
    mapa = ref.mapas.order_by('-version').first()
    vigente = mapa and mapa.hash_documento == ref.documento.hash_sha256
    return JsonResponse(dict(documento=info, editable=editable, version=obj.version_edicion if obj else None,
        seleccionable=bool(editable and (tecnica.plano_de_trabajo(ref, obj.expediente) or
            ref.estado == 'PUBLICADA' and ref.aplicabilidades.filter(equipo=obj.expediente.registro.equipo, activa=True).exists())),
        estado=obj.estado if obj else None, mapa_version=mapa.version if mapa else 0,
        mapa_provisional=mapa.provisional if mapa else ref.estado == 'BORRADOR',
        puntos=[dict(id=p.pk, parte=p.parte_id, numero=p.numero, pagina=p.pagina,
            x=str(p.x), y=str(p.y)) for p in mapa.posiciones.all()] if vigente else [],
        partes=[dict(id=p.pk, posicion=p.posicion, codigo=p.numero_parte, descripcion=p.descripcion,
            unidad=p.unidad, cantidad=str(p.cantidad_referencia) if p.cantidad_referencia is not None else '',
            pagina=p.pagina) for p in ref.partes.order_by('pk')], piezas=piezas_json(obj)))


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
    rol = tecnica.autorizar_tecnica(request.user, request.empresa_activa, {'PLANTA', 'TALLER'})
    if rol.rol == 'TALLER':
        ref = ReferenciaTecnica.objects.filter(pk=pk, empresa=request.empresa_activa,
            tipo='PLANO', estado='BORRADOR', expediente_trabajo__isnull=False).first()
        if not ref:
            raise PermissionDenied('TALLER solo marca el plano provisional de su expediente.')
        tecnica.expediente_autorizado(request.user, request.empresa_activa, ref.expediente_trabajo_id, {'TALLER'})
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
        'mensaje': 'Posiciones provisionales guardadas; pendientes de revisión de PLANTA.' if mapa.provisional else 'Posiciones revisadas y publicadas en una nueva versión.'})


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
    tecnica.expediente_autorizado(request.user, request.empresa_activa, obj.expediente_id, {'TECNICO','TALLER'})
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


class ImportarForm(BaseForm):
    proyecto_id = forms.ModelChoiceField(queryset=Proyecto.objects.none(), label='Proyecto de la empresa para custodiar el original')
    archivo = forms.FileField(label='PDF original M20A-HNRC · 3:1 · Figura 1')
    materiales = forms.CharField(max_length=250000, widget=forms.Textarea(attrs={'rows': 12}), label='CSV de materiales para revisión')
    procedencia = forms.CharField(max_length=20000, widget=forms.Textarea(attrs={'rows': 2}))
    autorizacion = forms.CharField(max_length=20000, widget=forms.Textarea(attrs={'rows': 2}), label='Fundamento de uso autorizado')
    version_fabricante = forms.CharField(required=False, max_length=100, label='Revisión del fabricante verificada (vacío si no consta)')
    sustituye_id = forms.ModelChoiceField(queryset=ReferenciaTecnica.objects.none(), required=False, label='Revisión anterior, si existe (conserva sus partes; CSV solo cabecera)')
    confirmado = forms.BooleanField(required=True, label='He revisado el PDF y todas las filas; códigos y cantidades no verificados permanecen vacíos.')

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proyecto_id'].queryset = Proyecto.objects.filter(empresa=empresa)
        self.fields['sustituye_id'].queryset = ReferenciaTecnica.objects.filter(empresa=empresa, codigo='M20A-HNRC-FIG1')
        self.fields['confirmado'].widget.attrs['class'] = 'form-check-input'


@require_http_methods(['GET', 'POST'])
@local
@masiscam_access_required
def plano_importar(request):
    tecnica.autorizar_tecnica(request.user, request.empresa_activa, {'PLANTA'})
    from pathlib import Path
    seed = (Path(__file__).parent / 'data/m20a_hnrc_fig1_revision.csv').read_text(encoding='utf-8')
    form = ImportarForm(request.POST or None, request.FILES or None, empresa=request.empresa_activa,
        initial={'materiales': seed})
    rows = []
    if request.method == 'POST':
        try:
            rows = svc.leer_materiales(request.POST.get('materiales', ''))
            if request.POST.get('op') == 'revisar' and request.headers.get('Accept') == 'application/json':
                return JsonResponse({'filas': rows})
            if request.POST.get('op') == 'guardar' and form.is_valid():
                data = form.cleaned_data.copy()
                data['proyecto_id'] = data['proyecto_id'].pk
                data['sustituye_id'] = data['sustituye_id'].pk if data['sustituye_id'] else None
                ref = svc.importar_m20(usuario=request.user, empresa=request.empresa_activa, **data)
                return redirect('masiscam:biblioteca_referencia', pk=ref.pk)
        except (ValidationError, IntegrityError, OSError) as exc:
            if request.headers.get('Accept') == 'application/json':
                return JsonResponse({'error': ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'No se confirmó la revisión.'}, status=400)
            form.add_error(None, ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'No se confirmó la importación.')
    return render(request, 'masiscam/plano_importar.html', {'form': form, 'filas': rows},
        status=400 if form.errors else 200)

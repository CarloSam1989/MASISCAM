from .campos import DecimalFlexible
from decimal import Decimal
from django import forms
from .tecnica_forms import BaseForm, AplicabilidadForm
from .models import PiezaRequerida, ReferenciaTecnica
from .flujo_services import disponible
from .forms import ArchivosRegistroField


class PlanoTrabajoForm(BaseForm):
    archivo = forms.FileField(label='Plano PDF', widget=forms.ClearableFileInput(attrs={'accept': '.pdf,application/pdf'}))


class AplicarPlanoExistenteForm(AplicabilidadForm):
    referencia_id = forms.ModelChoiceField(queryset=ReferenciaTecnica.objects.none(), label='Plano publicado existente')

    def __init__(self, *args, empresa, usuario, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        # El destino se obtiene del expediente en servidor, nunca del formulario.
        self.fields.pop('equipo_id')
        self.fields.pop('activa')
        from .tecnica_services import referencias_visibles
        self.fields['referencia_id'].queryset = referencias_visibles(usuario, empresa).filter(
            empresa=empresa, tipo='PLANO', estado='PUBLICADA').order_by('codigo', '-revision')


class ComponenteTrabajoForm(BaseForm):
    posicion = forms.CharField(max_length=80, label='Número o posición en el plano')
    numero_parte = forms.CharField(max_length=120, required=False, label='Código, si figura en el plano')
    descripcion = forms.CharField(max_length=20000, label='Descripción del componente')
    unidad = forms.CharField(max_length=80, required=False, label='Unidad, si figura en el plano')
    pagina = forms.CharField(max_length=40, required=False, label='Página')


class EnviarPiezasForm(BaseForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ConceptoForm(BaseForm):
    """Trabajo o consumible que TALLER registra para que OFICINA lo valore."""
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    tipo=forms.ChoiceField(choices=[('MANTENIMIENTO','Mantenimiento'),('REPARACION','Reparación'),('SERVICIO','Servicio'),('CONSUMIBLE','Consumible')],label='Tipo')
    descripcion=forms.CharField(max_length=500,label='Descripción del trabajo o consumible')
    cantidad=DecimalFlexible(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'),label='Cantidad')
    unidad=forms.CharField(max_length=40,label='Unidad (p. ej. servicio, hora, L)')


class CotizacionForm(BaseForm):
    referencia=forms.CharField(max_length=200,label='Número y versión del presupuesto')
    archivo=forms.FileField(required=False,label='Presupuesto PDF (puede adjuntarse antes de la aceptación)')
    observaciones=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}))
    condiciones=forms.CharField(required=False,max_length=20000,label='Condiciones comerciales',widget=forms.Textarea(attrs={'rows':3}))

    EXTRAS=3

    def __init__(self,*args,solicitud=None,propuesta=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.filas=[]; self.extras=[]; self.conceptos=[]
        if solicitud is None: return
        self.fields['factura_numero']=forms.CharField(max_length=200,required=False,label='Número de factura')
        self.fields['factura_fecha']=forms.DateField(required=False,label='Fecha de factura',widget=forms.DateInput(attrs={'type':'date'}))
        actual=propuesta.snapshot.get('cotizacion',{}) if propuesta and propuesta.diagnostico_id==solicitud.diagnostico_id else {}
        if actual.get('moneda','USD')!='USD': actual={}
        previas=actual.get('lineas',[])
        por_concepto={r['concepto']:r for r in previas if r.get('concepto') is not None}
        adicionales=[r for r in previas if r.get('concepto') is None and 'pieza' not in r]
        self.initial.update(condiciones=actual.get('condiciones',''),factura_numero=actual.get('factura',{}).get('numero',''),
            factura_fecha=actual.get('factura',{}).get('fecha',''),
            referencia=propuesta.snapshot.get('presupuesto_referencia','') if propuesta else '',
            observaciones=propuesta.observaciones if propuesta else '')
        # Conceptos registrados por TALLER (versión congelada): OFICINA solo asigna el precio.
        for c in solicitud.snapshot.get('conceptos',[]):
            nombre=f"c{c['id']}_precio"
            self.fields[nombre]=DecimalFlexible(max_digits=14,decimal_places=2,min_value=0,label=f"Precio unitario de {c['descripcion']}")
            self.initial[nombre]=por_concepto.get(c['id'],{}).get('precio_unitario','')
            self.conceptos.append(c)
            self.filas.append(dict(concepto=c,precio=self[nombre],total=por_concepto.get(c['id'],{}).get('total','')))
        # Servicios que TALLER no registró: exigen justificación y pasan por la revisión conjunta.
        for i in range(self.EXTRAS):
            previa=adicionales[i] if i<len(adicionales) else {}
            campos=dict(tipo=forms.ChoiceField(choices=[('SERVICIO','Servicio'),('CONSUMIBLE','Consumible')],required=False,label='Tipo'),
                descripcion=forms.CharField(max_length=500,required=False,label='Servicio o consumible adicional'),
                cantidad=DecimalFlexible(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'),required=False,label='Cantidad'),
                unidad=forms.CharField(max_length=40,required=False,label='Unidad'),
                precio=DecimalFlexible(max_digits=14,decimal_places=2,min_value=0,required=False,label='Precio unitario (USD)'),
                justificacion=forms.CharField(max_length=1000,required=False,label='Justificación (obligatoria)'))
            for nombre,campo in campos.items():
                self.fields[f'x{i}_{nombre}']=campo
                self.initial[f'x{i}_{nombre}']=previa.get('precio_unitario' if nombre=='precio' else nombre,'')
            self.extras.append({n:self[f'x{i}_{n}'] for n in campos}|dict(total=previa.get('total',''),indice=i))
        for field in self.fields.values(): field.widget.attrs.setdefault('class','form-control')
        self.fields['archivo'].widget.attrs['accept']='.pdf,application/pdf'

    def clean(self):
        data=super().clean()
        if self.data.get('moneda','USD')!='USD':
            raise forms.ValidationError('Las nuevas cotizaciones se registran exclusivamente en USD.')
        if bool(data.get('factura_numero'))!=bool(data.get('factura_fecha')):
            raise forms.ValidationError('Registre juntos el número y la fecha de la factura.')
        self.lineas_validas=[]
        for c in self.conceptos:
            if data.get(f"c{c['id']}_precio") is not None:
                self.lineas_validas.append(dict(concepto=c['id'],precio_unitario=str(data[f"c{c['id']}_precio"])))
        for i in range(self.EXTRAS if self.filas is not None else 0):
            d={n:data.get(f'x{i}_{n}') for n in ('tipo','descripcion','cantidad','unidad','precio','justificacion')}
            if not (d['descripcion'] or d['precio'] is not None or d['cantidad'] is not None or d['justificacion']): continue
            if not d['descripcion'] or d['precio'] is None or d['cantidad'] is None or not d['unidad']:
                raise forms.ValidationError(f'El servicio adicional {i+1} requiere descripción, cantidad, unidad y precio unitario.')
            if not (d['justificacion'] or '').strip():
                raise forms.ValidationError(f'El servicio adicional {i+1} no fue registrado por TALLER: indique su justificación.')
            self.lineas_validas.append(dict(tipo=d['tipo'] or 'SERVICIO',descripcion=d['descripcion'],cantidad=str(d['cantidad']),
                unidad=d['unidad'],precio_unitario=str(d['precio']),justificacion=d['justificacion'].strip()))
        if (self.conceptos or self.extras) and not self.lineas_validas:
            raise forms.ValidationError('Indique al menos un servicio o consumible cotizado. Los repuestos no se cotizan.')
        return data

    def clean_archivo(self):
        archivo=self.cleaned_data.get('archivo')
        if archivo:
            from .plano_services import validar_pdf_trabajo
            validar_pdf_trabajo(archivo)
        return archivo

    def datos(self):
        data=self.cleaned_data
        return dict(referencia=data['referencia'],observaciones=data['observaciones'],archivo=data['archivo'],
            cotizacion=dict(moneda='USD',condiciones=data['condiciones'],factura=dict(numero=data['factura_numero'],
                fecha=data['factura_fecha'].isoformat() if data['factura_fecha'] else ''),
                lineas=self.lineas_validas))


class AdquisicionForm(BaseForm):
    pieza_id=forms.ModelChoiceField(queryset=PiezaRequerida.objects.none(),label='Pieza solicitada')
    cantidad=DecimalFlexible(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'))
    proveedor=forms.CharField(max_length=200,label='Proveedor o procedencia')
    referencia=forms.CharField(max_length=200,label='Factura, orden o referencia de adquisición')
    fecha=forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    archivo=forms.FileField(required=False,label='Documento de adquisición')
    observaciones=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self,*args,solicitud,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['pieza_id'].queryset=solicitud.diagnostico.piezas.all()
        self.fields['pieza_id'].label_from_instance=lambda p:f'{p.numero_parte or "Código pendiente"} · {p.descripcion} · {p.cantidad} {p.unidad}'


class EnvioForm(BaseForm):
    numero_guia=forms.CharField(max_length=200,label='Número de guía de entrega')
    fecha=forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))

    def __init__(self,*args,solicitud,**kwargs):
        super().__init__(*args,**kwargs)
        self.lineas=[]
        for adq in solicitud.adquisiciones.select_related('pieza').order_by('pk'):
            n=disponible(adq)
            name=f'cantidad_{adq.pk}'
            self.lineas.append((name,adq.pk))
            self.fields[name]=DecimalFlexible(required=False,max_digits=20,decimal_places=6,min_value=0,max_value=adq.cantidad,
                label=f'{adq.pieza.descripcion} · {adq.unidad} · disponible {n}',help_text='Indica la cantidad que sale físicamente. Deja vacío lo que no se envía.')
            self.fields[name].widget.attrs.update({'class':'form-control','inputmode':'decimal'})

    def filas(self):
        return [dict(adquisicion=pk,cantidad=str(self.cleaned_data[name])) for name,pk in self.lineas if self.cleaned_data.get(name)]


class ConfirmarEnvioForm(BaseForm):
    fecha=forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    observaciones=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self,*args,envio,**kwargs):
        super().__init__(*args,**kwargs)
        self.lineas=[]
        for det in envio.lineas.select_related('adquisicion__pieza').order_by('pk'):
            name=f'cantidad_{det.pk}'; motivo=f'diferencia_{det.pk}'
            self.lineas.append((name,motivo,det.pk))
            self.fields[name]=DecimalFlexible(max_digits=20,decimal_places=6,min_value=0,
                label=f'{det.adquisicion.pieza.descripcion} · enviados {det.cantidad} {det.unidad}',
                help_text='Confirma la cantidad realmente recibida. Cero registra un faltante; no se completa automáticamente.')
            self.fields[motivo]=forms.CharField(required=False,max_length=20000,label='Faltantes o diferencias',widget=forms.Textarea(attrs={'rows':2}))
        for f in self.fields.values():
            if not isinstance(f.widget,forms.HiddenInput): f.widget.attrs['class']='form-control'

    def filas(self):
        return [dict(detalle=pk,cantidad=str(self.cleaned_data[name]),discrepancia=self.cleaned_data[motivo]) for name,motivo,pk in self.lineas]


class PiezaManualForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    descripcion=forms.CharField(max_length=20000,label='Descripción de la pieza')
    numero_parte=forms.CharField(required=False,max_length=120,label='Código, si se conoce')
    cantidad=DecimalFlexible(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'))
    unidad=forms.CharField(max_length=80)
    condicion=forms.CharField(max_length=20000,label='Condición observada',widget=forms.Textarea(attrs={'rows':2}))
    accion=forms.ChoiceField(choices=[('','Selecciona una acción'),('REEMPLAZAR','Reemplazar'),('REPARAR','Reparar'),('REUTILIZAR','Reutilizar'),('EVALUAR','Evaluar')])
    motivo_manual=forms.CharField(max_length=20000,label='Identificación y motivo del registro manual',widget=forms.Textarea(attrs={'rows':2}))
    observaciones=forms.CharField(required=False,max_length=20000,widget=forms.Textarea(attrs={'rows':2}))
    fotografias=ArchivosRegistroField()

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['fotografias'].label='Fotografías de la pieza (máximo seis)'
        self.fields['fotografias'].widget.attrs['accept']='.jpg,.jpeg,.png,.webp'

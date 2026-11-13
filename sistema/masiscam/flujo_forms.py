from decimal import Decimal
from django import forms
from .tecnica_forms import BaseForm
from .models import PiezaRequerida
from .flujo_services import disponible
from .forms import ArchivosRegistroField


class PlanoTrabajoForm(BaseForm):
    archivo = forms.FileField(label='Plano PDF', widget=forms.ClearableFileInput(attrs={'accept': '.pdf,application/pdf'}))


class ComponenteTrabajoForm(BaseForm):
    posicion = forms.CharField(max_length=80, label='Número o posición en el plano')
    numero_parte = forms.CharField(max_length=120, required=False, label='Código, si figura en el plano')
    descripcion = forms.CharField(max_length=20000, label='Descripción del componente')
    unidad = forms.CharField(max_length=80, required=False, label='Unidad, si figura en el plano')
    pagina = forms.CharField(max_length=40, required=False, label='Página')


class EnviarPiezasForm(BaseForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class CotizacionForm(BaseForm):
    referencia=forms.CharField(max_length=200,label='Número y versión del presupuesto')
    archivo=forms.FileField(required=False,label='Presupuesto PDF (puede adjuntarse antes de la aceptación)')
    observaciones=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}))


class AdquisicionForm(BaseForm):
    pieza_id=forms.ModelChoiceField(queryset=PiezaRequerida.objects.none(),label='Pieza solicitada')
    cantidad=forms.DecimalField(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'))
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
            self.fields[name]=forms.DecimalField(required=False,max_digits=20,decimal_places=6,min_value=0,max_value=adq.cantidad,
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
            self.fields[name]=forms.DecimalField(max_digits=20,decimal_places=6,min_value=0,
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
    cantidad=forms.DecimalField(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'))
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

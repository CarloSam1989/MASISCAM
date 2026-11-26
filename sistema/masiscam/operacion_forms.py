from .campos import DecimalFlexible
from django import forms
from django.utils import timezone
from decimal import Decimal
from .tecnica_forms import BaseForm
from .models import (Diagnostico,PropuestaComercial,RecepcionRepuesto,PiezaRequerida,
    ActividadReparacion,ResultadoReparacion)

AREAS={'mantenimiento':{'TALLER'},'propuesta':{'OFICINA'},'decision':{'OFICINA'},'repuesto':{'OFICINA', 'ADMIN', 'TALLER'},
    'actividad':{'TALLER'},'uso':{'TALLER'},'foto':{'TALLER'},'resultado':{'TALLER'},
    'revision':{'ADMIN'},'documento':{'OFICINA', 'ADMIN'},'respaldo':{'OFICINA'},'foto_repuesto':{'OFICINA', 'ADMIN', 'TALLER'}}
LABELS={'mantenimiento':'Registrar mantenimiento','propuesta':'Propuesta comercial','decision':'Decisión del cliente','repuesto':'Registrar repuestos del cliente',
    'actividad':'Iniciar actividad','uso':'Repuesto utilizado','foto':'Fotografía de actividad',
    'resultado':'Resultado de actividad','revision':'Revisión de ADMINISTRADOR','documento':'Emitir documento interno',
    'respaldo':'Adjuntar respaldo comercial','foto_repuesto':'Fotografía de repuesto recibido'}


class MantenimientoForm(BaseForm):
    """Pantalla única de mantenimiento: componentes precargados, observaciones y resultado; las fotografías van aparte."""
    inicio = forms.DateTimeField(widget=forms.HiddenInput,initial=timezone.now)
    componentes = forms.MultipleChoiceField(required=False,widget=forms.CheckboxSelectMultiple,label='Componentes intervenidos')
    observaciones = forms.CharField(required=False,max_length=20000,widget=forms.Textarea,label='Observaciones')
    resultado = forms.CharField(required=False,max_length=20000,widget=forms.Textarea,label='Resultado del mantenimiento')

    def __init__(self,*args,exp,**kwargs):
        from . import operacion_services as svc
        super().__init__(*args,**kwargs)
        piezas = svc.componentes_autorizados(exp)
        self.fields['componentes'].choices = [(str(p['pk']),p['texto']) for p in piezas]
        self.fields['componentes'].initial = [str(p['pk']) for p in piezas]
        self.fields['componentes'].widget.attrs.pop('class',None)
        for nombre in ('observaciones','resultado'): self.fields[nombre].widget.attrs['rows']=3


ACTIVIDADES_BASE=('Mantenimiento','Reparación','Cambio de aceite','Calibración')
SIN_ACTIVIDADES='Primero registre actividades técnicas antes de usar repuestos.'


def actividades_aprobadas(exp):
    """Actividades ofrecidas al iniciar la reparación: los conceptos técnicos de TALLER aprobados por ADMINISTRADOR en el
    diagnóstico vigente; si no hay conceptos específicos, las actividades base."""
    from . import operacion_services as svc
    try: auth=svc.autorizacion_vigente(exp)
    except Exception: auth=None
    diag=auth.propuesta.diagnostico if auth else None
    conceptos=[]
    if diag:
        for c in diag.conceptos.filter(tipo__in=['MANTENIMIENTO','REPARACION','SERVICIO']).order_by('pk'):
            if c.descripcion not in conceptos: conceptos.append(c.descripcion)
    return (conceptos or list(ACTIVIDADES_BASE)),(diag.piezas.order_by('pk') if diag else PiezaRequerida.objects.none())


class OperacionForm(BaseForm):
    def __init__(self,*args,op,exp,politica=None,**kwargs):
        super().__init__(*args,**kwargs)
        F=self.fields
        def text(name,required=True):
            F[name]=forms.CharField(required=required,max_length=20000,widget=forms.Textarea,label=name.replace('_',' ').capitalize())
        def date(name):
            F[name]=forms.DateTimeField(widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}),label=name.replace('_',' ').capitalize())
        def choice(name,query):
            F[name]=forms.ModelChoiceField(queryset=query,label=name.replace('_id','').replace('_',' ').capitalize())
            def label(obj):
                if isinstance(obj,Diagnostico):
                    return f'Diagnóstico r{obj.revision} · {obj.get_estado_display()}'
                if isinstance(obj,PropuestaComercial):
                    return f'Propuesta v{obj.version} · diagnóstico r{obj.diagnostico.revision}'
                if isinstance(obj,PiezaRequerida):
                    return f'{obj.numero_parte or "Identificación pendiente"} · {obj.descripcion[:100]} · {obj.cantidad} {obj.unidad}'
                if isinstance(obj,RecepcionRepuesto):
                    return f'Recepción {obj.pk} · {obj.descripcion[:100]} · {obj.cantidad} {obj.unidad}'
                if isinstance(obj,ActividadReparacion):
                    return f'Actividad {obj.pk} · {obj.trabajo[:100]}'
                return f'Actividad {obj.actividad_id} · resultado v{obj.version}'
            F[name].label_from_instance=label
        def qty():
            F['cantidad']=DecimalFlexible(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'))
        def file(name,required=False):
            F[name]=forms.FileField(required=required,label=name.capitalize())
        if op=='propuesta':
            choice('diagnostico_id',Diagnostico.objects.filter(expediente=exp,estado__in=['APROBADO','FINALIZADO']))
            text('alcance'); text('observaciones',False); file('archivo')
            F['presupuesto_referencia']=forms.CharField(max_length=200,required=False,
                label='Referencia y versión del presupuesto',
                help_text='Puede quedar pendiente en borrador. Para aceptar se exige referencia y copia del presupuesto. No autoriza reparar.')
            F['alcance'].help_text='Alcance exacto sometido al cliente. Una aceptación parcial requiere otra versión de propuesta.'
            F['archivo'].label='Presupuesto documentado (pendiente permitido antes de aceptación)'
        elif op=='decision':
            choice('propuesta_id',PropuestaComercial.objects.filter(expediente=exp,decision__isnull=True))
            F['decision']=forms.ChoiceField(choices=[('RECHAZADO','Rechazo documentado')]+([('APROBADO','Aceptación total'),('PARCIAL','Aceptación parcial: nueva versión de alcance')] if politica else []))
            date('fecha_cliente'); text('representante_cliente'); text('motivo'); file('archivo',True)
            F['motivo'].help_text='Documente aceptación o rechazo de la versión y alcance completos, y cómo se verificó el representante. No sustituye el respaldo.'
            text('identidad',False)
            F['calidad']=forms.ChoiceField(required=False,choices=[('','Pendiente'),('TITULAR','Cliente titular'),('REPRESENTANTE','Representante autorizado')])
            text('calidad_representante',False); text('medio_verificacion',False); text('respaldo_verificacion',False)
            F['canal']=forms.ChoiceField(required=False,choices=[('','Pendiente'),('PRESENCIAL_FIRMADA','Presencial firmada'),('ELECTRONICA_VERIFICABLE','Electrónica con evidencia verificable')])
            F['verificacion_confirmada']=forms.BooleanField(required=False,label='He verificado identidad, facultades y suficiencia del respaldo de la decisión y su alcance')
            text('alcance_autorizado',False); file('archivo_facultad')
            F['archivo_facultad'].label='Respaldo de facultades del representante (obligatorio para aceptar como representante)'
            F['alcance_autorizado'].help_text='Solo aceptación parcial: describa exclusivamente los trabajos aceptados. Se crea nueva versión sin trasladar aprobaciones.'
        elif op=='respaldo':
            choice('propuesta_id',PropuestaComercial.objects.filter(expediente=exp,decision__isnull=True))
            text('leyenda'); file('archivo',True)
        elif op=='foto_repuesto':
            choice('recepcion_id',RecepcionRepuesto.objects.filter(expediente=exp))
            text('leyenda'); file('archivo',True)
        elif op=='repuesto':
            choice('pieza_id',PiezaRequerida.objects.filter(diagnostico__expediente=exp))
            F['pieza_id'].required=False
            for name in ('procedencia','entregado_por','descripcion','unidad'):
                text(name)
            for name in ('numero_parte','guia','discrepancia'):
                text(name,False)
            qty(); date('fecha'); file('archivo'); file('fotografia')
        elif op=='actividad':
            opciones,piezas=actividades_aprobadas(exp)
            F['trabajo']=forms.ChoiceField(choices=[('','Seleccione la actividad')]+[(x,x) for x in opciones],label='Actividad o trabajo realizado')
            if len(opciones)==1: F['trabajo'].initial=opciones[0]
            self._piezas={str(p.pk):f'{p.numero_parte or "Sin N.º de parte"} · {p.descripcion[:120]}' for p in piezas}
            F['componentes']=forms.MultipleChoiceField(choices=list(self._piezas.items()),widget=forms.CheckboxSelectMultiple,
                label='Componentes intervenidos',help_text='Piezas registradas en el expediente.' if self._piezas else 'No hay piezas registradas en el expediente.')
            if len(self._piezas)==1: F['componentes'].initial=list(self._piezas)
            date('inicio'); text('observaciones',False)
        elif op in {'uso','foto','resultado'}:
            choice('actividad_id',ActividadReparacion.objects.filter(expediente=exp))
            if op=='uso':
                choice('recepcion_id',RecepcionRepuesto.objects.filter(expediente=exp)); qty(); text('observaciones',False)
            elif op=='foto':
                F['clase']=forms.ChoiceField(choices=[(x,x.capitalize()) for x in ('ANTES','DURANTE','DESPUES')])
                text('leyenda'); file('archivo',True)
            else:
                date('fin'); text('trabajo_realizado'); text('resultado'); text('observaciones',False)
        elif op=='revision':
            choice('resultado_id',ResultadoReparacion.objects.filter(expediente=exp,revision__isnull=True))
            F['decision']=forms.ChoiceField(choices=[('APROBADO','Aprobado'),('OBSERVADO','Observado')]); text('motivo')
        elif op=='documento':
            F['tipo']=forms.ChoiceField(choices=[('REPUESTOS','Comprobante de repuestos'),('REPARACION','Reporte de reparación')])
            F['nueva_version']=forms.BooleanField(required=False)
            text('motivo',False)
        for f in F.values():
            if isinstance(f.widget,forms.CheckboxSelectMultiple): continue
            f.widget.attrs.setdefault('class','form-select' if isinstance(f.widget,forms.Select) else 'form-control')
            if isinstance(f.widget,forms.Textarea):
                f.widget.attrs['rows']=3
        labels={'numero_parte':'Número de parte','descripcion':'Descripción','guia':'Guía de remisión / ingreso',
            'fotografia':'Fotografía','fecha':'Fecha de recepción','fecha_cliente':'Fecha de la decisión del cliente',
            'fin':'Fecha y hora de finalización','inicio':'Fecha y hora de inicio','archivo':'Documento de respaldo',
            'nueva_version':'Emitir una nueva versión','entregado_por':'Proveedor / responsable de entrega'}
        for name,label in labels.items():
            if name in F:
                F[name].label=label

    def clean_componentes(self):
        valores=self.cleaned_data['componentes']
        return chr(10).join(self._piezas[v] for v in valores if v in self._piezas)

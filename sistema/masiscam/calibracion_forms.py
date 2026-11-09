from decimal import Decimal
from django import forms
from django.forms import formset_factory
from .tecnica_forms import BaseForm
from .models import PlantillaCalibracion,Proyecto,Equipo


class PlantillaForm(BaseForm):
    codigo=forms.CharField(max_length=80)
    titulo=forms.CharField(max_length=200,label='Título')
    tipo_equipo=forms.ChoiceField(choices=[('REDUCTOR','Reductor'),('BOMBA','Bomba')])
    marca=forms.CharField(max_length=200,required=False,label='Marca documentada')
    modelo=forms.CharField(max_length=200,required=False,label='Modelo documentado')
    version_equipo=forms.CharField(max_length=200,required=False,label='Versión del equipo según fuente')
    procedimiento_version=forms.CharField(max_length=200,required=False,label='Versión del procedimiento técnico')
    proyecto_id=forms.ModelChoiceField(queryset=Proyecto.objects.none(),label='Proyecto real de la fuente')
    sustituye_id=forms.ModelChoiceField(queryset=PlantillaCalibracion.objects.none(),required=False,label='Versión fuente anterior')
    archivo=forms.FileField(label='Protocolo / fuente técnica documentada')
    parametros=forms.JSONField(required=False,label='Parámetros adicionales documentados',help_text='Lista de código, nombre, unidad, fuente, tolerancia, criterio y obligatoriedad explícita. Sin valores predeterminados. Datos incompletos solo como borrador.')
    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['proyecto_id'].queryset=Proyecto.objects.filter(empresa=empresa)
        self.fields['sustituye_id'].queryset=PlantillaCalibracion.objects.filter(empresa=empresa)


class PuntoForm(forms.Form):
    codigo=forms.CharField(required=False,max_length=80,label='Código según protocolo')
    nombre=forms.CharField(required=False,max_length=200,label='Posición / punto según fuente')
    unidad=forms.CharField(required=False,max_length=80)
    fuente=forms.CharField(required=False,max_length=2000,label='Referencia documental')
    minimo=forms.DecimalField(required=False,max_digits=30,decimal_places=12,label='Mínimo de referencia')
    maximo=forms.DecimalField(required=False,max_digits=30,decimal_places=12,label='Máximo de referencia')
    tolerancia=forms.CharField(required=False,max_length=2000,label='Tolerancia documentada')
    criterio=forms.CharField(required=False,max_length=2000,label='Criterio documentado de evaluación')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values(): f.widget.attrs['class']='form-control'


PuntosFormSet=formset_factory(PuntoForm,extra=16,max_num=16,validate_max=True)


class InicioForm(BaseForm):
    plantilla_id=forms.ModelChoiceField(queryset=PlantillaCalibracion.objects.none(),label='Plantilla exacta validada/aplicable')
    motivo=forms.CharField(required=False,widget=forms.Textarea,label='Motivo de corrección (PLANTA)')
    def __init__(self,*args,exp,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['plantilla_id'].queryset=PlantillaCalibracion.objects.filter(empresa=exp.empresa,aplicabilidades__equipo=exp.registro.equipo).distinct()
        self.fields['plantilla_id'].label_from_instance=lambda p:f'{p.codigo} v{p.version} · {p.titulo}'


class DatosForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    fecha=forms.DateTimeField(required=False,widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}))
    instrumentos=forms.CharField(required=False,widget=forms.Textarea,label='Identificación de instrumentos y documentación conocida')
    observaciones=forms.CharField(required=False,widget=forms.Textarea)
    resultados=forms.CharField(required=False,widget=forms.Textarea,label='Resultados registrados por el técnico')
    motivo_sin_evidencia=forms.CharField(required=False,widget=forms.Textarea,label='Ausencia de evidencias: motivo documentado')
    def __init__(self,*args,calibracion,**kwargs):
        initial={f:getattr(calibracion,f) for f in ('fecha','instrumentos','observaciones','resultados','motivo_sin_evidencia')}
        initial['version']=calibracion.version_edicion
        super().__init__(*args,initial=initial,**kwargs)
        self.parametros=[]
        for n,r in enumerate(calibracion.plantilla.definicion.get('parametros',[])):
            name=f'param_{n}';self.parametros.append((name,r['codigo']))
            self.fields[name]=forms.DecimalField(required=False,max_digits=30,decimal_places=12,
                initial=calibracion.parametros.get(r['codigo']),label=f"{r['nombre']} ({r.get('unidad','unidad pendiente')})",widget=forms.NumberInput(attrs={'class':'form-control','step':'any'}))


class MedicionForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    punto=forms.ChoiceField(label='Punto definido por plantilla')
    valor=forms.DecimalField(required=False,max_digits=30,decimal_places=12,label='Valor registrado (vacío = pendiente)')
    observaciones=forms.CharField(required=False,widget=forms.Textarea)
    def __init__(self,*args,calibracion,**kwargs):
        super().__init__(*args,initial={'version':calibracion.version_edicion},**kwargs)
        self.fields['punto'].choices=[(r['codigo'],f"{r['nombre']} ({r.get('unidad','unidad pendiente')})") for r in calibracion.plantilla.definicion['mediciones']]


class EvidenciaForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    clase=forms.ChoiceField(choices=[('FOTO','Fotografía'),('CERTIFICADO','Documento del instrumento')])
    leyenda=forms.CharField(widget=forms.Textarea)
    archivo=forms.FileField()


class DecisionForm(BaseForm):
    decision=forms.ChoiceField(choices=[(s,s.capitalize()) for s in ('APROBADA','OBSERVADA','RECHAZADA')])
    motivo=forms.CharField(widget=forms.Textarea,label='Fundamento de revisión independiente')
    def __init__(self,*args,calibracion,**kwargs):
        super().__init__(*args,**kwargs);self.evaluaciones=[]
        for n,r in enumerate(calibracion.plantilla.definicion['mediciones']):
            name=f'eval_{n}';self.evaluaciones.append((name,r['codigo']))
            choices=[('PENDIENTE','Pendiente de evaluación')]
            if r.get('criterio','').strip(): choices += [('CONFORME','Conforme según revisión manual'),('NO_CONFORME','No conforme según revisión manual')]
            self.fields[name]=forms.ChoiceField(choices=choices,label=f"{r['nombre']} · {r.get('criterio') or 'Sin criterio aprobado'}",widget=forms.Select(attrs={'class':'form-select'}))


class InformeForm(BaseForm):
    nueva_version=forms.BooleanField(required=False,label='Emitir nueva versión')
    motivo=forms.CharField(required=False,widget=forms.Textarea)


class ValidarForm(BaseForm):
    fundamento=forms.CharField(widget=forms.Textarea,label='Fundamento de validación independiente',
        help_text='Documente la fuente, competencia técnica del responsable y verificación de versión/aplicabilidad. El rol PLANTA no sustituye esa validación.')


class AplicarForm(ValidarForm):
    equipo_id=forms.ModelChoiceField(queryset=Equipo.objects.none(),label='Equipo explícitamente aplicable')
    version_equipo=forms.CharField(max_length=200,label='Versión del equipo comprobada contra la fuente técnica')
    def __init__(self,*args,empresa,plantilla,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['equipo_id'].queryset=Equipo.objects.filter(proyecto__empresa=empresa,tipo_producto=plantilla.tipo_equipo)

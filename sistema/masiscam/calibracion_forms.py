from .campos import DecimalFlexible
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
    minimo=DecimalFlexible(required=False,max_digits=30,decimal_places=12,label='Mínimo de referencia')
    maximo=DecimalFlexible(required=False,max_digits=30,decimal_places=12,label='Máximo de referencia')
    tolerancia=forms.CharField(required=False,max_length=2000,label='Tolerancia documentada')
    criterio=forms.CharField(required=False,max_length=2000,label='Criterio documentado de evaluación')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values(): f.widget.attrs['class']='form-control'


PuntosFormSet=formset_factory(PuntoForm,extra=16,max_num=16,validate_max=True)


class InicioForm(BaseForm):
    plantilla_id=forms.ModelChoiceField(queryset=PlantillaCalibracion.objects.none(),label='Plantilla exacta validada/aplicable')
    motivo=forms.CharField(required=False,widget=forms.Textarea,label='Motivo de corrección (ADMINISTRADOR)')
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
            self.fields[name]=DecimalFlexible(required=False,max_digits=30,decimal_places=12,
                initial=calibracion.parametros.get(r['codigo']),label=f"{r['nombre']} ({r.get('unidad','unidad pendiente')})",widget=forms.NumberInput(attrs={'class':'form-control','step':'any'}))


class FormatoCalibracionForm(BaseForm):
    """Hoja única de calibración (datos, shims, mediciones y cierre) sobre el protocolo aprobado de la plantilla.

    Los datos generales del equipo no se piden: se leen del registro de TALLER. Los campos de shims y datos de calibración
    son los parámetros del protocolo; no se crean valores ni puntos que el protocolo no defina."""
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    fecha=forms.DateTimeField(required=False,label='Fecha',widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}))
    instrumentos=forms.CharField(required=False,widget=forms.Textarea,label='Instrumentos utilizados')
    resultados=forms.CharField(required=False,widget=forms.Textarea,label='Resultado de la calibración')
    observaciones=forms.CharField(required=False,widget=forms.Textarea,label='Observaciones finales')
    motivo_sin_evidencia=forms.CharField(required=False,widget=forms.Textarea,label='Motivo si no hay evidencias')
    evidencia_clase=forms.ChoiceField(required=False,choices=[('FOTO','Fotografía'),('CERTIFICADO','Documento del instrumento')],label='Tipo de evidencia')
    evidencia_leyenda=forms.CharField(required=False,max_length=500,label='Descripción de la evidencia')
    evidencia_archivo=forms.FileField(required=False,label='Archivo de evidencia')

    def __init__(self,*args,calibracion,**kwargs):
        initial={f:getattr(calibracion,f) for f in ('fecha','instrumentos','observaciones','resultados','motivo_sin_evidencia')}
        initial['version']=calibracion.version_edicion
        super().__init__(*args,initial=initial,**kwargs)
        self.parametros=[];self.puntos=[];shims=[];datos=[];medidas=[]
        definicion=calibracion.plantilla.definicion
        for n,r in enumerate(definicion.get('parametros',[])):
            name=f'param_{n}';self.parametros.append((name,r['codigo']))
            self.fields[name]=DecimalFlexible(required=False,max_digits=30,decimal_places=12,initial=calibracion.parametros.get(r['codigo']),
                label=f"{r['nombre']} ({r.get('unidad') or 'unidad pendiente'})",widget=forms.NumberInput(attrs={'class':'form-control','step':'any'}))
            (shims if 'shim' in f"{r['nombre']} {r['codigo']}".lower() else datos).append(name)
        valores={m.punto:m.valor for m in calibracion.mediciones.all()}
        for n,r in enumerate(definicion['mediciones']):
            name=f'medicion_{n}';self.puntos.append((name,r['codigo']))
            self.fields[name]=DecimalFlexible(required=False,max_digits=30,decimal_places=12,initial=valores.get(r['codigo']),
                label=f"{r['nombre']} ({r.get('unidad') or 'unidad pendiente'})",widget=forms.NumberInput(attrs={'class':'form-control','step':'any'}))
            medidas.append((n+1,name))
        self.bf_shims=[self[x] for x in shims];self.bf_datos=[self[x] for x in datos]
        self.bf_mediciones=[(n,self[x]) for n,x in medidas]

    def clean(self):
        data=super().clean()
        if data.get('evidencia_archivo') and not (data.get('evidencia_leyenda') or '').strip():
            self.add_error('evidencia_leyenda','Describa la evidencia que adjunta.')
        return data


class HojaInicioForm(BaseForm):
    """Abre la hoja de calibración de TALLER (o su nueva revisión, con motivo). No pide protocolo ni aprobación."""
    motivo=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':3}),label='Motivo de la nueva revisión de la hoja')


class HojaCalibracionForm(FormatoCalibracionForm):
    """Hoja de calibración con el orden del formato físico (shims, datos de calibración, presiones, 16 mediciones, observaciones, fecha).

    Todo se lee de la ficha técnica de la configuración del equipo; las unidades se eligen (no hay unidad por defecto) y se recuerdan
    para la próxima hoja de la misma configuración, igual que tolerancia, mínimo y máximo, que solo se precargan para verificarlos."""
    unidad_longitud=forms.ChoiceField(required=False,label='Unidad de longitud (shims, backlash, referencias y mediciones)',
        choices=[('','Selecciona')]+[(u,u) for u in ('mm','µm','in','mil')],widget=forms.Select(attrs={'class':'form-select'}))
    unidad_presion=forms.ChoiceField(required=False,label='Unidad de presión',
        choices=[('','Selecciona')]+[(u,u) for u in ('psi','bar','kPa','MPa')],widget=forms.Select(attrs={'class':'form-select'}))

    def __init__(self,*args,calibracion,**kwargs):
        super().__init__(*args,calibracion=calibracion,**kwargs)
        from . import calibracion_services as servicios
        for campo in ('instrumentos','resultados','motivo_sin_evidencia'): del self.fields[campo]
        self.fields['observaciones'].label='Observaciones'
        self.fields['evidencia_clase'].label='Tipo de evidencia (opcional)'
        previos=servicios.valores_previos(calibracion);guardadas=(calibracion.parametros or {}).get('_unidades',{})
        for grupo in ('longitud','presion'):
            self.fields[f'unidad_{grupo}'].initial=guardadas.get(grupo) or previos.get('unidades',{}).get(grupo,'')
        self.precargados=[]
        for n,(codigo,nombre,grupo,no_negativo) in enumerate(servicios.PARAMETROS_HOJA):
            campo=self.fields[f'param_{n}'];campo.label=nombre
            if no_negativo: campo.widget.attrs['min']='0'
            if (calibracion.parametros or {}).get(codigo) in (None,'') and codigo in servicios.CAMPOS_REFERENCIA and previos.get(codigo) not in (None,''):
                campo.initial=previos[codigo];self.precargados.append(nombre)
        valores={m.punto:m.valor for m in calibracion.mediciones.all()}
        for n,fila in enumerate(calibracion.plantilla.definicion['mediciones']):
            campo=self.fields[f'medicion_{n}'];campo.label=f'Medición {n+1}'
            if valores.get(fila['codigo']) is not None: campo.initial=servicios._decimal_texto(valores[fila['codigo']])   # sin ceros de relleno
        self._bound_fields_cache={}   # las etiquetas cambiaron: se vuelven a crear los campos enlazados
        nombres={codigo:nombre for nombre,codigo in self.parametros}
        self.bf_unidad_longitud=self['unidad_longitud'];self.bf_unidad_presion=self['unidad_presion']
        self.bf_shims=[self[nombres[c]] for c in ('SHIM-VS','SHIM-VI','SHIM-H')]
        self.bf_datos=[self[nombres[c]] for c in ('BACKLASH','TOLERANCIA','CAL-TOTAL','MINIMO','MAXIMO')]
        self.bf_presiones=[self[nombres[c]] for c in ('PRES-V','PRES-H')]
        self.bf_mediciones=[(n,self[nombre]) for n,(nombre,_) in enumerate(self.puntos,1)]
        if not self.is_bound and calibracion.fecha is None:
            from django.utils import timezone
            self.initial['fecha']=timezone.localtime().replace(second=0,microsecond=0)   # propuesta: la fecha de hoy; el técnico puede cambiarla


class MedicionForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    punto=forms.ChoiceField(label='Punto definido por plantilla')
    valor=DecimalFlexible(required=False,max_digits=30,decimal_places=12,label='Valor registrado (vacío = pendiente)')
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
        help_text='Documente la fuente, competencia técnica del responsable y verificación de versión/aplicabilidad. El rol ADMINISTRADOR no sustituye esa validación.')


class AplicarForm(ValidarForm):
    equipo_id=forms.ModelChoiceField(queryset=Equipo.objects.none(),label='Equipo explícitamente aplicable')
    version_equipo=forms.CharField(max_length=200,label='Versión del equipo comprobada contra la fuente técnica')
    def __init__(self,*args,empresa,plantilla,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['equipo_id'].queryset=Equipo.objects.filter(proyecto__empresa=empresa,tipo_producto=plantilla.tipo_equipo)

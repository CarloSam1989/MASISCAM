import uuid
from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from .models import (Inspeccion,Diagnostico,Documento,Proyecto,Equipo,RolMasiscam,
    ReferenciaTecnica,ComponenteTecnico,ParteReferenciaTecnica,EvidenciaTecnica)
from .tecnica_services import referencias_visibles


class BaseForm(forms.Form):
    clave=forms.UUIDField(initial=uuid.uuid4,widget=forms.HiddenInput)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault('class','form-select' if isinstance(f.widget,forms.Select) else 'form-control')
            if isinstance(f.widget,forms.Textarea):
                f.widget.attrs['rows']=3


class AsignacionForm(BaseForm):
    tecnico_id=forms.ModelChoiceField(queryset=get_user_model().objects.none(),label='Técnico principal')
    motivo=forms.CharField(max_length=20000,widget=forms.Textarea)

    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        ids=RolMasiscam.objects.filter(perfil__empresa=empresa,activo=True,rol__in=['TECNICO','TALLER']).values('perfil__user_id')
        self.fields['tecnico_id'].queryset=get_user_model().objects.filter(pk__in=ids,is_active=True)


class RevisionForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    fecha=forms.DateField(required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    motivo_sin_evidencia=forms.CharField(required=False,max_length=20000,widget=forms.Textarea,label='Motivo de ausencia de evidencia')
    observaciones=forms.CharField(required=False,max_length=20000,widget=forms.Textarea)

    def __init__(self,*args,tipo,revision,**kwargs):
        initial={f.name:getattr(revision,f.name) for f in revision._meta.fields if f.name not in {'id'}}
        initial['version']=revision.version_edicion
        super().__init__(*args,initial=initial,**kwargs)
        names={'estado_fisico':'Estado físico','condiciones_generales':'Condiciones generales','hallazgos':'Hallazgos y daños'} if tipo=='INSPECCION' else {
            'fallas':'Fallas identificadas','causas':'Posibles causas y limitaciones','componentes_afectados':'Componentes afectados','recomendaciones':'Recomendaciones','conclusiones':'Conclusiones técnicas'}
        for name,label in names.items():
            self.fields[name]=forms.CharField(required=False,max_length=20000,label=label,widget=forms.Textarea(attrs={'class':'form-control','rows':3}))
        if tipo=='DIAGNOSTICO':
            self.fields['repuestos']=forms.ChoiceField(choices=Diagnostico._meta.get_field('repuestos').choices,widget=forms.Select(attrs={'class':'form-select'}))


class EvidenciaForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    archivo=forms.FileField(required=False,label='Fotografía nueva',widget=forms.ClearableFileInput(attrs={'accept':'.jpg,.jpeg,.png,.webp'}))
    documento_id=forms.ModelChoiceField(queryset=Documento.objects.none(),required=False,label='O fotografía de este expediente')
    referencia_id=forms.ModelChoiceField(queryset=ReferenciaTecnica.objects.none(),required=False,label='O versión de plano/manual aplicable (clase REFERENCIA)')
    clase=forms.ChoiceField(choices=EvidenciaTecnica._meta.get_field('clase').choices)
    leyenda=forms.CharField(max_length=20000,widget=forms.Textarea)

    def __init__(self,*args,expediente,revision,usuario,empresa,**kwargs):
        from django.db.models import Q
        super().__init__(*args,initial={'version':revision.version_edicion},**kwargs)
        self.fields['documento_id'].queryset=Documento.objects.filter(Q(evidencia_recepcion__recepcion__expediente=expediente)|Q(evidencias_tecnicas__inspeccion__expediente=expediente)|Q(evidencias_tecnicas__diagnostico__expediente=expediente)).filter(tipo_mime__startswith='image/',publico=False).distinct()
        self.fields['referencia_id'].queryset=referencias_visibles(usuario,empresa).filter(estado='PUBLICADA',aplicabilidades__equipo=expediente.registro.equipo,aplicabilidades__activa=True).distinct()


class PiezaForm(BaseForm):
    version=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    origen=forms.ChoiceField(choices=[('MANUAL','Registro manual'),('BIBLIOTECA','Selección desde plano/listado')])
    parte_id=forms.ModelChoiceField(queryset=ParteReferenciaTecnica.objects.none(),required=False,label='Posición / parte del plano')
    descripcion=forms.CharField(required=False,max_length=20000,widget=forms.Textarea,label='Descripción manual (la biblioteca conserva su descripción)')
    numero_parte=forms.CharField(required=False,max_length=120,label='Número de parte; vacío si pendiente')
    cantidad=forms.DecimalField(max_digits=20,decimal_places=6,min_value=Decimal('0.000001'))
    unidad=forms.CharField(required=False,max_length=80)
    motivo_unidad_pendiente=forms.CharField(required=False,max_length=20000,widget=forms.Textarea)
    condicion=forms.CharField(max_length=20000,widget=forms.Textarea,label='Condición')
    accion=forms.CharField(max_length=20000,widget=forms.Textarea,label='Acción recomendada')
    motivo_manual=forms.CharField(required=False,max_length=20000,widget=forms.Textarea,label='Contexto / motivo de entrada manual')

    def __init__(self,*args,usuario,empresa,expediente,revision,**kwargs):
        super().__init__(*args,initial={'version':revision.version_edicion},**kwargs)
        self.fields['parte_id'].queryset=ParteReferenciaTecnica.objects.filter(referencia__in=referencias_visibles(usuario,empresa),
            referencia__estado='PUBLICADA',referencia__aplicabilidades__equipo=expediente.registro.equipo,referencia__aplicabilidades__activa=True).distinct()


class DecisionForm(BaseForm):
    decision=forms.ChoiceField(choices=[('APROBADO','Aprobar diagnóstico'),('OBSERVADO','Observar'),('RECHAZADO','Rechazar')])
    motivo=forms.CharField(required=False,max_length=20000,widget=forms.Textarea)


class ReferenciaForm(BaseForm):
    codigo=forms.CharField(max_length=80,label='Código interno')
    tipo=forms.ChoiceField(choices=[('PLANO','Plano'),('MANUAL','Manual')])
    titulo=forms.CharField(max_length=200)
    documento_id=forms.ModelChoiceField(queryset=Documento.objects.none(),required=False,label='Documento privado existente')
    proyecto_id=forms.ModelChoiceField(queryset=Proyecto.objects.none(),required=False,label='Proyecto real para archivo nuevo')
    archivo=forms.FileField(required=False,label='O archivo nuevo')
    marca=forms.CharField(max_length=120,required=False)
    modelo=forms.CharField(max_length=120,required=False)
    ratio=forms.CharField(max_length=80,required=False)
    version_fabricante=forms.CharField(max_length=100,required=False,label='Versión del fabricante, si consta')
    procedencia=forms.CharField(max_length=20000,widget=forms.Textarea)
    autorizacion_uso=forms.CharField(max_length=20000,widget=forms.Textarea,label='Fundamento de uso autorizado y congelación del documento privado')
    sustituye_id=forms.ModelChoiceField(queryset=ReferenciaTecnica.objects.none(),required=False,label='Versión fuente (para otra revisión)')

    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['proyecto_id'].queryset=Proyecto.objects.filter(empresa=empresa)
        self.fields['documento_id'].queryset=Documento.objects.filter(proyecto__empresa=empresa,publico=False,categoria__in=['PLANOS','MANUALES']).exclude(estado_sincronizacion='ARCHIVADO')
        self.fields['sustituye_id'].queryset=ReferenciaTecnica.objects.filter(empresa=empresa)


class ComponenteForm(BaseForm):
    codigo=forms.CharField(max_length=80)
    fabricante=forms.CharField(max_length=120,required=False)
    numero_parte=forms.CharField(max_length=120,required=False)
    descripcion=forms.CharField(max_length=20000,widget=forms.Textarea)
    unidad=forms.CharField(max_length=80,required=False)


class ParteForm(BaseForm):
    componente_id=forms.ModelChoiceField(queryset=ComponenteTecnico.objects.none(),required=False)
    posicion=forms.CharField(max_length=80,label='Posición en plano/listado')
    numero_parte=forms.CharField(max_length=120,required=False)
    descripcion=forms.CharField(max_length=20000,widget=forms.Textarea)
    unidad=forms.CharField(max_length=80,required=False)
    cantidad_referencia=forms.DecimalField(required=False,max_digits=20,decimal_places=6)
    pagina=forms.CharField(max_length=40,required=False)

    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['componente_id'].queryset=ComponenteTecnico.objects.filter(empresa=empresa)


class AplicabilidadForm(BaseForm):
    equipo_id=forms.ModelChoiceField(queryset=Equipo.objects.none(),label='Equipo aplicable')
    fundamento=forms.CharField(max_length=20000,widget=forms.Textarea)
    activa=forms.BooleanField(required=False,initial=True,label='Aplicabilidad activa')

    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['equipo_id'].queryset=Equipo.objects.filter(proyecto__empresa=empresa)

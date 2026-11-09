"""Only synthetic approvals and documents; no operational configuration."""
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, SimpleTestCase, override_settings
from django.template.loader import render_to_string
from . import operacion_services as o, calibracion_services as c
from .operacion_forms import OperacionForm
from .models import DecisionComercial, PropuestaComercial
from .test_operacion import FLAGS, QA_POLICY, presupuesto_qa, verificacion_qa
from django.utils import timezone
from .test_tecnica import pdf_plano
from . import test_operacion as qa


@override_settings(**FLAGS)
class PreparacionComercialTests(TestCase):
    setUp=qa.OperacionTests.setUp
    decision=qa.OperacionTests.decision
    propuesta=qa.OperacionTests.propuesta

    def incompleta(self, referencia=''):
        return o.proponer(usuario=self.gerencia, **self.base, diagnostico_id=self.d.pk,
            clave=uuid.uuid4(), datos={'alcance':'ALCANCE FICTICIO QA', 'presupuesto_referencia':referencia})

    def datos(self, **cambios):
        return dict(dict(decision='APROBADO',fecha_cliente=timezone.now(),representante_cliente='IDENTIDAD SOLO TEST',
            motivo='Prueba aislada sin validez operativa',**verificacion_qa()),**cambios)

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_identidad_canal_y_verificacion_no_omitibles(self):
        p=self.propuesta()
        for field,value in [('identidad',''),('medio_verificacion',''),('respaldo_verificacion',''),
                ('calidad','OTRO'),('canal','NO_VERIFICABLE'),('verificacion_confirmada','true'),('verificacion_confirmada',False)]:
            with transaction.atomic(), self.assertRaises(ValidationError):
                o.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,
                    clave=uuid.uuid4(),datos=self.datos(**{field:value}),archivo=pdf_plano())
        self.assertFalse(DecisionComercial.objects.exists())

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_representante_requiere_facultades_documentadas(self):
        p=self.propuesta(); data=self.datos(calidad='REPRESENTANTE',calidad_representante='Documento solo para test')
        with transaction.atomic(), self.assertRaises(ValidationError):
            o.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,
                clave=uuid.uuid4(),datos=data,archivo=pdf_plano())
        d=o.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,
            clave=uuid.uuid4(),datos=data,archivo=pdf_plano(),archivo_facultad=presupuesto_qa())
        self.assertTrue(o.autorizacion_valida(d))
        self.assertTrue(d.politica['verificacion_cliente']['facultad_hash'])
        self.assertEqual(o.exigir_autorizacion(self.exp).pk,d.pk)

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_parcial_crea_version_exacta_no_hereda_y_es_idempotente(self):
        p=self.propuesta();key=uuid.uuid4()
        data=self.datos(decision='PARCIAL',alcance_autorizado='SOLO ALCANCE REDUCIDO QA')
        from django.core.files.uploadedfile import SimpleUploadedFile
        binary=pdf_plano().read()
        def aceptar():
            return o.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,
                clave=key,datos=data,archivo=SimpleUploadedFile('respaldo.pdf',binary,content_type='application/pdf'))
        d=aceptar();self.assertEqual(d.pk,aceptar().pk)
        self.assertNotEqual(d.propuesta_id,p.pk)
        self.assertEqual(d.propuesta.version,2)
        self.assertEqual(d.propuesta.sustituye_id,p.pk)
        self.assertEqual(d.propuesta.alcance,data['alcance_autorizado'])
        self.assertEqual(d.politica['verificacion_cliente']['tipo'],'PARCIAL')
        self.assertFalse(DecisionComercial.objects.filter(propuesta=p).exists())
        self.assertTrue(o.autorizacion_valida(d))
        self.propuesta()
        self.assertFalse(o.autorizacion_valida(d))
        self.assertEqual(DecisionComercial.objects.get(pk=d.pk).propuesta.alcance,data['alcance_autorizado'])

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_parcial_fallo_documental_revierte_version_y_decision(self):
        p=self.propuesta();n=PropuestaComercial.objects.count()
        with patch.object(o,'_evidencia',side_effect=ValidationError('Fallo documental QA')):
            with transaction.atomic(), self.assertRaises(ValidationError):
                o.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,
                    clave=uuid.uuid4(),datos=self.datos(decision='PARCIAL',alcance_autorizado='REDUCIDO QA'),archivo=pdf_plano())
        self.assertEqual(PropuestaComercial.objects.count(),n)
        self.assertFalse(DecisionComercial.objects.exists())

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_presupuesto_referencia_y_documento_exigidos_en_servidor(self):
        for referencia in ('', 'QA v1'):
            p=self.incompleta(referencia)
            with transaction.atomic(), self.assertRaises(ValidationError):
                self.decision(p)
        self.assertFalse(DecisionComercial.objects.exists())

    def test_rechazo_documentado_no_requiere_aprobacion_ni_autoriza(self):
        d=self.decision(self.incompleta(), decision='RECHAZADO')
        self.assertFalse(o.autorizacion_valida(d))
        self.assertEqual(d.politica['verificacion_cliente']['canal'],'PRESENCIAL_FIRMADA')
        self.assertEqual(d.politica['verificacion_cliente']['tipo'],'RECHAZADO')

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_snapshot_alcance_responsable_fecha_y_reintento(self):
        key=uuid.uuid4()
        data={'alcance':'ALCANCE EXACTO QA','presupuesto_referencia':'QA version 2'}
        p=o.proponer(usuario=self.gerencia, **self.base, diagnostico_id=self.d.pk,
            clave=key, datos=data, archivo=presupuesto_qa())
        # Retry uses the exact same persisted bytes; no new proposal or budget.
        with p.evidencias.first().documento.archivo.open('rb') as archivo:
            q=o.proponer(usuario=self.gerencia, **self.base, diagnostico_id=self.d.pk,
                clave=key, datos=data, archivo=archivo)
        self.assertEqual(p.pk,q.pk)
        d=self.decision(p)
        self.assertEqual(d.propuesta.snapshot['presupuesto_referencia'],'QA version 2')
        self.assertEqual(d.propuesta.alcance,'ALCANCE EXACTO QA')
        self.assertEqual(d.autor,self.gerencia)
        self.assertTrue(d.fecha_cliente)
        self.assertEqual(d.evidencias.count(),1)

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_presupuesto_corrupto_no_permite_aceptacion(self):
        p=self.propuesta()
        with patch.object(o.t,'documento_valido',side_effect=ValidationError('QA copia corrupta')):
            with transaction.atomic(), self.assertRaises(ValidationError): self.decision(p)
        self.assertFalse(DecisionComercial.objects.exists())

    def test_formulario_y_vista_no_ofrecen_aceptacion_sin_politica(self):
        form=OperacionForm(op='decision',exp=self.exp)
        self.assertEqual(list(form.fields['decision'].choices),[('RECHAZADO','Rechazo documentado')])
        proposal=OperacionForm(op='propuesta',exp=self.exp)
        self.assertIn('presupuesto_referencia',proposal.fields)
        p=self.propuesta()
        html=render_to_string('masiscam/operacion_detalle.html',{'expediente':self.exp,'comercial':True,'propuestas':[p]})
        self.assertIn('QA v1',html)


class PreparacionIndustrialTests(SimpleTestCase):
    def test_publicacion_exige_referencias_y_parametros_completos(self):
        from .test_calibracion import definicion_qa
        for campo in ('unidad','fuente','tolerancia','criterio'):
            data=definicion_qa();data['parametros'][0][campo]=''
            with self.assertRaises(ValidationError): c.validar_definicion(data,True)
            self.assertEqual(c.validar_definicion(data),data)  # Draft remains possible.
        for campo in ('marca','modelo','version_equipo','procedimiento_version'):
            data=definicion_qa();data['aplicacion'][campo]=''
            with self.assertRaises(ValidationError): c.validar_definicion(data,True)
        data=definicion_qa();del data['parametros'][0]['obligatorio']
        with self.assertRaises(ValidationError): c.validar_definicion(data,True)

    def test_marca_modelo_no_se_aplican_por_equivalencia(self):
        p=SimpleNamespace(tipo_equipo='BOMBA',definicion={'aplicacion':{'marca':'Marca','modelo':'Modelo'}})
        self.assertTrue(c._equipo_compatible(p,SimpleNamespace(tipo_producto='BOMBA',marca='Marca',modelo='Modelo')))
        self.assertFalse(c._equipo_compatible(p,SimpleNamespace(tipo_producto='BOMBA',marca='Otra',modelo='Modelo')))
        self.assertFalse(c._equipo_compatible(p,SimpleNamespace(tipo_producto='REDUCTOR',marca='Marca',modelo='Modelo')))
    def test_pendientes_no_inventa_valores_ni_aprobaciones(self):
        data={'mediciones':[{'codigo':'PENDIENTE','nombre':'Referencia por validar'}], 'parametros':[]}
        p=SimpleNamespace(definicion=data, aplicabilidades=SimpleNamespace(exists=lambda:False))
        with patch.object(c,'aprobacion_plantilla',return_value=None): result=c.pendientes_protocolo(p)
        self.assertTrue(any('16 posiciones' in x for x in result))
        for field in ('unidad','fuente','criterio','tolerancias','propietario','aplicabilidad'):
            self.assertTrue(any(field in x for x in result),field)
        self.assertNotIn('minimo',data['mediciones'][0])

    def test_presentacion_incluye_cero_y_parametros_sin_evaluarlos(self):
        p=SimpleNamespace(pk=1,definicion={'mediciones':[{'codigo':'QA','minimo':'0','maximo':'1','tolerancia':'QA'}],
            'parametros':[{'codigo':'QA-P','unidad':'QA','criterio':'SIN VALIDEZ INDUSTRIAL'}]})
        html=render_to_string('masiscam/calibracion_plantilla.html',{'plantilla':p,'pendientes':['Pendiente QA']})
        self.assertIn('Mínimo: 0',html)
        self.assertIn('SIN VALIDEZ INDUSTRIAL',html)
        self.assertIn('Uso operativo bloqueado',html)


@override_settings(**FLAGS,MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
class ControlesOperativosFinalesTests(TestCase):
    setUp=qa.OperacionTests.setUp
    propuesta=qa.OperacionTests.propuesta

    def test_electronica_alcance_visible_sin_identidad_ni_presupuesto(self):
        from django.test import Client
        from django.urls import reverse
        p=self.propuesta()
        data=dict(decision='PARCIAL',fecha_cliente=timezone.now(),representante_cliente='Persona solo test',motivo='Test aislado',
            alcance_autorizado='TRABAJO REDUCIDO PARA PRUEBA',**verificacion_qa())
        data['canal']='ELECTRONICA_VERIFICABLE'
        d=o.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,
            clave=uuid.uuid4(),datos=data,archivo=pdf_plano())
        self.assertEqual(d.politica['verificacion_cliente']['canal'],'ELECTRONICA_VERIFICABLE')
        client=Client();client.force_login(self.tecnico)
        response=client.get(reverse('masiscam:operacion_detalle',args=[self.exp.pk]))
        self.assertContains(response,data['alcance_autorizado'])
        self.assertNotContains(response,data['identidad'])
        self.assertNotContains(response,p.snapshot['presupuesto_referencia'])
        form=client.get(reverse('masiscam:operacion_operar',args=[self.exp.pk,'actividad']))
        self.assertContains(form,data['alcance_autorizado'])

    @override_settings(MASISCAM_CALIBRACION_LOCAL=True)
    def test_aplicabilidad_no_admite_version_omitida_o_diferente(self):
        from .test_calibracion import definicion_qa,protocolo_pdf
        p=c.crear_plantilla(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),
            datos=dict(codigo='QA SOLO TEST',titulo='SIN VALIDEZ INDUSTRIAL',tipo_equipo=self.exp.registro.equipo.tipo_producto,
                definicion=definicion_qa(),proyecto_id=self.exp.registro.equipo.proyecto_id))
        for version in ('', 'VERSION DIFERENTE QA'):
            with transaction.atomic(), self.assertRaises(ValidationError):
                c.aplicar_plantilla(usuario=self.planta,empresa=self.empresa,plantilla_id=p.pk,
                    equipo_id=self.exp.registro.equipo_id,clave=uuid.uuid4(),fundamento='Prueba sin operacion real',version_equipo=version)
        self.assertFalse(p.aplicabilidades.exists())

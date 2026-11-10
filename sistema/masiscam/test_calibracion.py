"""Fictional protocols/approvals only in disposable QA; no industrial values."""
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection,connections,transaction,IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase,TransactionTestCase,override_settings,Client
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import calibracion_services as s
from . import operacion_services as o
from . import tecnica_services as t
from .test_tecnica import preparar,usuario,pdf_plano
from .test_recepcion import foto
from .test_operacion import aprobar_diagnostico,QA_POLICY,presupuesto_qa,verificacion_qa,FLAGS as PHASE4_FLAGS

FLAGS=dict(**PHASE4_FLAGS,MASISCAM_CALIBRACION_LOCAL=True,MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)


def definicion_qa(criterios=True):
    return dict(mediciones=[dict(codigo=f'QA-{n:02}',nombre=f'PUNTO FICTICIO QA {n}',unidad='unidad QA SIN USO INDUSTRIAL',
        fuente='Fuente FICTICIA QA, no protocolo real',minimo='-1',maximo='1',tolerancia='NO INDUSTRIAL - solo maquetación',
        criterio='Criterio FICTICIO QA para prueba manual, no usar industrialmente' if criterios else '') for n in range(1,17)],
        parametros=[dict(codigo='QA-P',nombre='Parámetro FICTICIO QA',unidad='unidad QA',fuente='Fuente ficticia QA',
            tolerancia='SIN VALIDEZ INDUSTRIAL',criterio='Referencia solo para test',obligatorio=True)],
        aplicacion=dict(marca='Marca',modelo='Modelo',version_equipo='QA SIN VALIDEZ INDUSTRIAL',procedimiento_version='QA v1 NO OPERATIVO'))


def protocolo_pdf():
    from reportlab.pdfgen.canvas import Canvas
    stream=BytesIO();c=Canvas(stream);c.drawString(40,750,'PROTOCOLO FICTICIO QA - SIN VALIDEZ INDUSTRIAL');c.save()
    return SimpleUploadedFile('protocolo-ficticio-qa.pdf',stream.getvalue(),content_type='application/pdf')


def preparar_calibracion(criterios=True):
    empresa,oficina,planta,tecnico,exp=preparar()
    gerente=usuario(empresa,'gerencia-cal-qa','GERENCIA')
    revisor=usuario(empresa,'planta-cal-independiente','PLANTA')
    diag=aprobar_diagnostico(empresa,oficina,planta,tecnico,exp)
    prop=o.proponer(usuario=gerente,empresa=empresa,expediente_id=exp.pk,diagnostico_id=diag.pk,clave=uuid.uuid4(),
        datos={'alcance':'Alcance FICTICIO QA, no autorización real','presupuesto_referencia':'QA v1'},archivo=presupuesto_qa())
    o.decidir_comercial(usuario=gerente,empresa=empresa,propuesta_id=prop.pk,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(
        decision='APROBADO',fecha_cliente=timezone.now(),representante_cliente='PERSONA FICTICIA QA',motivo='Fixture aislada, no decisión real',**verificacion_qa()))
    act=o.iniciar_actividad(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,clave=uuid.uuid4(),datos=dict(trabajo='Actividad FICTICIA QA',componentes='Componente QA',inicio=timezone.now()))
    res=o.terminar_actividad(usuario=tecnico,empresa=empresa,actividad_id=act.pk,clave=uuid.uuid4(),datos=dict(fin=timezone.now(),trabajo_realizado='Trabajo ficticio QA',resultado='Sin resultado industrial'))
    o.revisar_actividad(usuario=planta,empresa=empresa,resultado_id=res.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Solo prueba de software QA')
    p=s.crear_plantilla(usuario=planta,empresa=empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),datos=dict(
        codigo='PROTOCOLO-FICTICIO-QA',titulo='SIN VALIDEZ INDUSTRIAL',tipo_equipo=exp.registro.equipo.tipo_producto,
        definicion=definicion_qa(criterios),proyecto_id=exp.registro.equipo.proyecto_id))
    s.aplicar_plantilla(usuario=planta,empresa=empresa,plantilla_id=p.pk,equipo_id=exp.registro.equipo_id,clave=uuid.uuid4(),
        fundamento='Aplicabilidad solo sintética QA',version_equipo=p.definicion['aplicacion']['version_equipo'])
    return empresa,oficina,planta,tecnico,exp,revisor,p


def anchor_qa(p):
    return dict(huella=s.huella_plantilla(p),aprobacion_propietario='SIMULACIÓN UNITARIA QA, NO APROBACIÓN REAL')


def validar_qa(empresa,revisor,p):
    return s.validar_plantilla(usuario=revisor,empresa=empresa,plantilla_id=p.pk,clave=uuid.uuid4(),fundamento='Revisión sintética QA independiente')


def completar(empresa,tecnico,c):
    c=s.guardar(usuario=tecnico,empresa=empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),datos=dict(
        fecha=timezone.now(),instrumentos='INSTRUMENTO FICTICIO QA, no certificado ni instrumento real',resultados='Registro ficticio, sin resultado industrial',
        motivo_sin_evidencia='Prueba sin fotografías adicionales',parametros={'QA-P':'0'}))
    for point in c.plantilla.definicion['mediciones']:
        s.medir(usuario=tecnico,empresa=empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),punto=point['codigo'],valor='0',observaciones='Dato ficticio QA')
        c.refresh_from_db()
    return c


@override_settings(**FLAGS)
class CalibracionTests(TestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp,self.revisor,self.p=preparar_calibracion()
        self.anchor=anchor_qa(self.p)
        self.policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[self.anchor]);self.policy.enable();self.addCleanup(self.policy.disable)
        validar_qa(self.empresa,self.revisor,self.p)
        self.base=dict(empresa=self.empresa,expediente_id=self.exp.pk)

    def iniciar(self):
        return s.iniciar(usuario=self.tecnico,**self.base,plantilla_id=self.p.pk,clave=uuid.uuid4())
    def enviar(self):
        c=completar(self.empresa,self.tecnico,self.iniciar())
        return s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())
    def decidir(self,c,decision='APROBADA',evaluaciones=None):
        return s.decidir(usuario=self.planta,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),decision=decision,motivo='Decisión FICTICIA QA independiente',
            evaluaciones=evaluaciones if evaluaciones is not None else {r['codigo']:'CONFORME' for r in c.plantilla.definicion['mediciones']})

    def test_bloqueos_comercial_y_protocolo_intactos(self):
        with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
            with self.assertRaises(ValidationError): self.iniciar()
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[]):
            with self.assertRaises(ValidationError): self.iniciar()
        self.assertFalse(Calibracion.objects.exists())

    def test_reparacion_debe_estar_finalizada(self):
        o.iniciar_actividad(usuario=self.tecnico,**self.base,clave=uuid.uuid4(),datos=dict(trabajo='QA adicional',componentes='QA',inicio=timezone.now()))
        with self.assertRaises(ValidationError): self.iniciar()

    def test_plantilla_16_posiciones_y_referencias(self):
        data=definicion_qa();data['mediciones']=data['mediciones'][:15]
        with self.assertRaises(ValidationError): s.validar_definicion(data,True)
        data=definicion_qa();data['mediciones'][0]['unidad']=''
        with self.assertRaises(ValidationError): s.validar_definicion(data,True)
        data=definicion_qa();data['mediciones'][1]['codigo']=data['mediciones'][0]['codigo']
        with self.assertRaises(ValidationError): s.validar_definicion(data,True)
        data=definicion_qa();data['mediciones'][0].update(minimo='2',maximo='1')
        with self.assertRaises(ValidationError): s.validar_definicion(data,True)

    def test_no_auto_validacion_plantilla(self):
        q=s.crear_plantilla(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),datos=dict(
            codigo='OTRA-QA',titulo='QA',tipo_equipo=self.p.tipo_equipo,definicion=definicion_qa(),proyecto_id=self.exp.registro.equipo.proyecto_id))
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(q)]):
            with self.assertRaises(PermissionDenied): s.validar_plantilla(usuario=self.planta,empresa=self.empresa,plantilla_id=q.pk,clave=uuid.uuid4(),fundamento='Intento QA')

    def test_plantilla_inmutable_y_nueva_version_sin_validacion_heredada(self):
        with self.assertRaises(IntegrityError),transaction.atomic(): PlantillaCalibracion.objects.filter(pk=self.p.pk).update(definicion={})
        q=s.crear_plantilla(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),datos=dict(
            codigo=self.p.codigo,titulo='QA nueva',tipo_equipo=self.p.tipo_equipo,definicion=definicion_qa(),proyecto_id=self.exp.registro.equipo.proyecto_id,sustituye_id=self.p.pk))
        self.assertEqual(q.version,2);self.assertFalse(s.plantilla_validada(q))

    def test_decimal_cero_negativo_y_rechazo_redondeo(self):
        self.assertEqual(s.decimal_seguro('0'),Decimal('0'))
        self.assertEqual(s.decimal_seguro('-0.123456789012'),Decimal('-0.123456789012'))
        for n in ('NaN','Infinity','0.0000000000001','1000000000000000000','abc'):
            with self.assertRaises(ValidationError): s.decimal_seguro(n)
        c=self.iniciar()
        m=s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=uuid.uuid4(),punto='QA-01',valor='-0.123456789012')
        m.refresh_from_db();self.assertEqual(m.valor,Decimal('-0.123456789012'))

    def test_punto_no_definido_y_datos_faltantes(self):
        c=self.iniciar()
        with self.assertRaises(ValidationError): s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=uuid.uuid4(),punto='INVENTADO',valor='1')
        with self.assertRaises(ValidationError): s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=uuid.uuid4())
        self.assertFalse(c.mediciones.exists())

    def test_parametro_faltante_y_parametro_no_aplicable(self):
        c=completar(self.empresa,self.tecnico,self.iniciar())
        with self.assertRaises(ValidationError): s.guardar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),datos={'parametros':{'SHIMS_NO_DEFINIDO':'0'}})
        c=s.guardar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),datos={'parametros':{}})
        with self.assertRaises(ValidationError): s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())

    def test_medicion_idempotente_edicion_obsoleta(self):
        c=self.iniciar();key=uuid.uuid4()
        args=dict(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=key,punto='QA-01',valor='0')
        m=s.medir(**args);self.assertEqual(s.medir(**args).pk,m.pk)
        with self.assertRaises(ValidationError): s.medir(**{**args,'clave':uuid.uuid4()})
        with self.assertRaises(ValidationError): s.medir(**{**args,'valor':'1'})
        self.assertEqual(c.mediciones.count(),1)

    def test_envio_exige16_y_congela_sql_orm(self):
        c=self.enviar()
        self.assertEqual(c.estado,'PENDIENTE_REVISION');self.assertEqual(c.mediciones.count(),16)
        m=c.mediciones.first();m.valor=1
        with self.assertRaises(PermissionDenied): m.save()
        with self.assertRaises(IntegrityError),transaction.atomic(): c.mediciones.update(valor=1)
        with self.assertRaises((PermissionDenied,ProtectedError)),transaction.atomic(): c.delete()

    def test_revision_independiente_e_inmutabilidad(self):
        c=self.enviar();d=self.decidir(c)
        c.refresh_from_db();self.assertEqual(c.estado,'APROBADA')
        with self.assertRaises(IntegrityError),transaction.atomic(): DecisionCalibracion.objects.filter(pk=d.pk).update(motivo='Cambio')
        with self.assertRaises(IntegrityError),transaction.atomic(): Calibracion.objects.filter(pk=c.pk).update(instrumentos='Cambio')

    def test_autoaprobacion_cambio_rol_prohibida(self):
        c=self.enviar();RolMasiscam.objects.filter(perfil__user=self.tecnico).update(rol='PLANTA')
        with self.assertRaises(PermissionDenied): s.decidir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),decision='APROBADA',motivo='Intento',evaluaciones={})

    def test_observacion_correccion_rechazo_y_autoria_fuente(self):
        c=self.enviar();self.decidir(c,'OBSERVADA',{})
        q=s.iniciar(usuario=self.planta,**self.base,plantilla_id=self.p.pk,clave=uuid.uuid4(),motivo='Corrección QA')
        self.assertEqual(q.revision,2);self.assertEqual(q.sustituye_id,c.pk);self.assertEqual(q.mediciones.count(),0)
        c.refresh_from_db();self.assertEqual(c.estado,'OBSERVADA');self.assertFalse(s.vigente(c))
        q=completar(self.empresa,self.tecnico,q)
        q=s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=q.pk,version=q.version_edicion,clave=uuid.uuid4())
        self.decidir(q,'RECHAZADA',{})
        q.refresh_from_db();self.assertEqual(q.estado,'RECHAZADA')

    def test_criterios_faltantes_no_permiten_conformidad(self):
        data=definicion_qa(False)
        q=s.crear_plantilla(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),datos=dict(
            codigo='SIN-CRITERIO-QA',titulo='QA',tipo_equipo=self.p.tipo_equipo,definicion=data,proyecto_id=self.exp.registro.equipo.proyecto_id))
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(q)]):
            with self.assertRaises(ValidationError): validar_qa(self.empresa,self.revisor,q)
            self.assertFalse(s.plantilla_validada(q))
            with self.assertRaises(ValidationError):
                s.iniciar(usuario=self.tecnico,**self.base,plantilla_id=q.pk,clave=uuid.uuid4())

    def test_no_evaluacion_automatica_por_limites(self):
        c=completar(self.empresa,self.tecnico,self.iniciar())
        s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),punto='QA-01',valor='99')
        c.refresh_from_db();c=s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())
        self.assertTrue(all(r['evaluacion']=='PENDIENTE' for r in s.snapshot(c)['mediciones']))
        with self.assertRaises(ValidationError): self.decidir(c,evaluaciones={r['codigo']:'NO_CONFORME' for r in c.plantilla.definicion['mediciones']})

    def test_fuente_comercial_diagnostico_y_reparacion_sustituida(self):
        c=self.iniciar()
        t.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Fuente QA sustituida')
        with self.assertRaises(ValidationError): s.guardar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=uuid.uuid4(),datos={'observaciones':'Intento'})

    def test_roles_y_aislamiento_empresa(self):
        for user in (self.oficina,self.planta):
            with self.assertRaises(PermissionDenied): s.iniciar(usuario=user,**self.base,plantilla_id=self.p.pk,clave=uuid.uuid4())
        another=usuario(self.empresa,'no-asignado-cal','TECNICO')
        with self.assertRaises(PermissionDenied): s.expediente(another,self.empresa,self.exp.pk)
        from accounts.models import Empresa
        other=Empresa.objects.create(nombre='OTRA QA');actor=usuario(other,'otra-planta-cal','PLANTA')
        with self.assertRaises(PermissionDenied): s.expediente(actor,other,self.exp.pk)

    def test_pdf_persistente_versionado_y_qr(self):
        token=self.exp.registro.equipo.token_publico
        c=self.enviar();self.decidir(c)
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        doc=s.generar_informe(em.pk);self.assertEqual(s.generar_informe(em.pk),doc)
        em.refresh_from_db()
        with em.documento.archivo.open('rb') as f: binary=f.read()
        self.assertEqual(hashlib.sha256(binary).hexdigest(),em.hash_sha256)
        from pypdf import PdfReader
        text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(binary)).pages)
        self.assertIn('REGISTRO DE CALIBRACIÓN',text);self.assertIn('Instrumentos',text)
        self.assertEqual(self.exp.registro.equipo.token_publico,token)
        with self.assertRaises(IntegrityError),transaction.atomic(): Documento.objects.filter(pk=doc).update(publico=True)

    def test_pdf_pendiente_no_afirma_aprobacion(self):
        c=self.enviar();em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        s.generar_informe(em.pk);em.refresh_from_db()
        self.assertEqual(em.snapshot['estado'],'PENDIENTE_REVISION');self.assertIsNone(em.snapshot['decision'])
        self.decidir(c)
        with self.assertRaises(ValidationError): s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        new=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),nueva_version=True,motivo='Revisión QA posterior')
        self.assertEqual(new.version,2)

    def test_storage_caido_y_falso_exito_no_emiten(self):
        c=self.enviar();em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        with patch('django.core.files.storage.FileSystemStorage._save',side_effect=OSError('QA')):
            with self.assertRaises(OSError): s.generar_informe(em.pk)
        with patch('django.core.files.storage.FileSystemStorage._save',return_value='qa-no-existe.pdf'):
            with self.assertRaises(ValidationError): s.generar_informe(em.pk)
        em.refresh_from_db();self.assertIsNone(em.documento_id)
        self.assertTrue(s.generar_informe(em.pk))

    def test_snapshot_sustituido_durante_render(self):
        c=self.enviar();em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        from .calibracion_pdf import construir_informe
        def changed(*args,**kwargs):
            pdf=construir_informe(*args,**kwargs);self.decidir(c);return pdf
        with patch('masiscam.calibracion_pdf.construir_informe',side_effect=changed):
            with self.assertRaises(ValidationError): s.generar_informe(em.pk)
        em.refresh_from_db();self.assertIsNone(em.documento_id)

    def test_evidencias_y_drive_despues_commit(self):
        c=self.iniciar()
        with override_settings(GOOGLE_DRIVE_ENABLED=True),patch('masiscam.calibracion_services.t._encolar_documento') as queue,self.captureOnCommitCallbacks(execute=True):
            e=s.evidencia(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=uuid.uuid4(),clase='FOTO',leyenda='Imagen ficticia QA',archivo=foto())
            self.assertFalse(queue.called)
        queue.assert_called_once_with(e.documento_id)
        doc=e.documento
        with doc.archivo.open('wb') as f: f.write(b'QA CORRUPTO')
        c.refresh_from_db()
        with self.assertRaises(ValidationError): s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())

    def test_http_server_y_descargas_heredadas(self):
        c=self.iniciar();client=Client();client.force_login(self.tecnico)
        url=reverse('masiscam:calibracion_revision',args=[c.pk])
        response=client.post(url,dict(op='medir',clave=uuid.uuid4(),version=1,punto='ARBITRARIO',valor='1'))
        self.assertEqual(response.status_code,400);self.assertFalse(c.mediciones.exists())
        self.assertEqual(client.get(reverse('masiscam:calibracion_fuente',args=[self.p.pk])).status_code,200)
        self.assertIn(client.get(reverse('masiscam:documento_privado',args=[self.p.documento.proyecto_id,self.p.documento_id])).status_code,(403,404))
        with override_settings(MASISCAM_CALIBRACION_LOCAL=False): self.assertEqual(client.get(url).status_code,404)

    def test_una_medicion_vacia_no_se_convierte_en_cero(self):
        c=completar(self.empresa,self.tecnico,self.iniciar())
        s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),punto='QA-16',valor=None)
        c.refresh_from_db()
        with self.assertRaises(ValidationError): s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())
        self.assertIsNone(c.mediciones.get(punto='QA-16').valor)

    def test_retirar_ancla_bloquea_revision_sin_borrar(self):
        c=self.enviar()
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[]):
            with self.assertRaises(ValidationError): self.decidir(c)
            client=Client();client.force_login(self.planta)
            response=client.get(reverse('masiscam:calibracion_revision',args=[c.pk]))
            self.assertContains(response,'Dependencias no vigentes')
            self.assertNotContains(response,'name="op" value="decision"')
        c.refresh_from_db();self.assertEqual(c.estado,'PENDIENTE_REVISION')
        self.assertEqual(c.mediciones.count(),16)

    def test_autoria_antecesora_protegida_tras_reasignar_y_cambiar_rol(self):
        c=self.enviar();self.decidir(c,'OBSERVADA',{})
        other=usuario(self.empresa,'otro-tecnico-cal-qa','TECNICO')
        t.asignar_tecnico(usuario=self.planta,**self.base,tecnico_id=other.pk,motivo='Reasignación QA',clave=uuid.uuid4())
        q=s.iniciar(usuario=self.planta,**self.base,plantilla_id=self.p.pk,clave=uuid.uuid4(),motivo='Corrección QA con autor nuevo')
        q=completar(self.empresa,other,q);q=s.enviar(usuario=other,empresa=self.empresa,calibracion_id=q.pk,version=q.version_edicion,clave=uuid.uuid4())
        RolMasiscam.objects.filter(perfil__user=self.tecnico).update(rol='PLANTA')
        with self.assertRaises(PermissionDenied): s.decidir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=q.pk,clave=uuid.uuid4(),decision='OBSERVADA',motivo='Intento de revisar derivada',evaluaciones={})

    def test_task_fallo_y_reintento_sin_documento_duplicado(self):
        c=self.enviar();em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        from .calibracion_tasks import generar_informe_calibracion
        with patch('masiscam.calibracion_services.generar_informe',side_effect=OSError('QA')):
            with self.assertRaises(OSError): generar_informe_calibracion.run(em.pk)
        em.refresh_from_db();self.assertEqual(em.estado,'ERROR');self.assertIsNone(em.documento_id)
        doc=generar_informe_calibracion.run(em.pk)
        self.assertEqual(generar_informe_calibracion.run(em.pk),doc)

    def test_pdf_corrupto_bloquea_reintento_y_descarga(self):
        c=self.enviar();em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        s.generar_informe(em.pk);em.refresh_from_db()
        with em.documento.archivo.open('wb') as f: f.write(b'CORRUPTO QA')
        with self.assertRaises(ValidationError): s.generar_informe(em.pk)
        client=Client();client.force_login(self.oficina)
        self.assertEqual(client.get(reverse('masiscam:calibracion_documento',args=[self.exp.pk,em.documento_id])).status_code,403)

    def test_archivo_impostor_rechazado_sin_vinculos(self):
        c=self.iniciar()
        with self.assertRaises(ValidationError): s.evidencia(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=1,clave=uuid.uuid4(),clase='FOTO',leyenda='Falsa QA',archivo=SimpleUploadedFile('imagen.png',b'NOT AN IMAGE',content_type='image/png'))
        self.assertFalse(c.evidencias.exists())

    def test_sql_no_salta_estados_sin_envio_y_decision(self):
        c=self.iniciar()
        for state in ('PENDIENTE_REVISION','APROBADA','OBSERVADA','RECHAZADA'):
            with self.assertRaises(IntegrityError),transaction.atomic(): Calibracion.objects.filter(pk=c.pk).update(estado=state)
        c.refresh_from_db();self.assertEqual(c.estado,'NO_INICIADA')

    def test_precision_del_motor_sin_redondeo_silencioso(self):
        c=self.iniciar()
        for n,value in enumerate(('123456789012345678.123456789012','999999999999999999.999999999999'),1):
            args=dict(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),punto=f'QA-{n:02}',valor=value)
            if connection.vendor=='sqlite':
                with self.assertRaises(ValidationError): s.medir(**args)
                self.assertFalse(c.mediciones.exists())
                c.refresh_from_db();self.assertEqual(c.version_edicion,1)
            else:
                m=s.medir(**args);m.refresh_from_db()
                self.assertEqual(m.valor,Decimal(value));c.refresh_from_db()


@skipUnless(connection.vendor=='postgresql','Concurrencia requiere PostgreSQL 16 aislado')
@override_settings(**FLAGS)
class CalibracionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp,self.revisor,self.p=preparar_calibracion()
        self.policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(self.p)]);self.policy.enable();self.addCleanup(self.policy.disable)
        validar_qa(self.empresa,self.revisor,self.p)
        self.c=s.iniciar(usuario=self.tecnico,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=self.p.pk,clave=uuid.uuid4())
    def concurrent(self,fn):
        barrier=Barrier(2)
        def run():
            connections.close_all()
            try: barrier.wait(timeout=30);return fn()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(lambda _:run(),range(2)))
    def test_medicion_idempotente_concurrente(self):
        key=uuid.uuid4()
        rows=self.concurrent(lambda:s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=self.c.pk,version=1,clave=key,punto='QA-01',valor='0').pk)
        self.assertEqual(rows[0],rows[1]);self.assertEqual(self.c.mediciones.count(),1)
    def test_edicion_optimista_concurrente(self):
        def edit():
            try:
                s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=self.c.pk,version=1,clave=uuid.uuid4(),punto='QA-01',valor='0');return 'OK'
            except ValidationError: return 'CONFLICTO'
        self.assertCountEqual(self.concurrent(edit),['OK','CONFLICTO'])
    def test_emision_concurrente_un_documento(self):
        c=completar(self.empresa,self.tecnico,self.c);c=s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4())
        rows=self.concurrent(lambda:s.generar_informe(em.pk))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(Auditoria.objects.filter(accion='CAL_PDF_GENERADO').count(),1)
    def test_decision_idempotente_concurrente(self):
        c=completar(self.empresa,self.tecnico,self.c);c=s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())
        key=uuid.uuid4()
        rows=self.concurrent(lambda:s.decidir(usuario=self.planta,empresa=self.empresa,calibracion_id=c.pk,clave=key,decision='APROBADA',motivo='Decisión ficticia QA',
            evaluaciones={r['codigo']:'CONFORME' for r in self.p.definicion['mediciones']}).pk)
        self.assertEqual(rows[0],rows[1]);self.assertEqual(DecisionCalibracion.objects.count(),1)

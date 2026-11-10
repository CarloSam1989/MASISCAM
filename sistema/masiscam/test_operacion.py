"""Synthetic QA only. Approved policy fixture is NOT an operational policy."""
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, connections, transaction, IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import operacion_services as s
from . import tecnica_services as t
from .test_tecnica import preparar, usuario, inspeccion_enviada, diagnostico_enviado, pdf_plano
from .test_recepcion import foto

QA_POLICY=dict(version='QA-FICTICIA-NO-OPERATIVA',aprobacion_propietario='SIMULACION UNITARIA; NO ES APROBACION REAL',
    procedimiento='Fixture aislada para probar respaldo explícito a revisión exacta',vigente_desde='2020-01-01T00:00:00+00:00')
FLAGS=dict(MASISCAM_RECEPCION_LOCAL=True,MASISCAM_TECNICA_LOCAL=True,MASISCAM_OPERACION_LOCAL=True,GOOGLE_DRIVE_ENABLED=False)


def aprobar_diagnostico(empresa,oficina,planta,tecnico,exp):
    inspeccion_enviada(empresa,tecnico,exp)
    d=diagnostico_enviado(empresa,tecnico,exp)
    t.decidir_diagnostico(usuario=planta,empresa=empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
    em=t.reservar_informe(usuario=oficina,empresa=empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
    t.generar_informe(em.pk)
    return Diagnostico.objects.get(pk=d.pk)


def repuesto_datos():
    return dict(procedencia='Origen ficticio QA',entregado_por='Entrega QA',numero_parte='QA',descripcion='Pieza de demostración',
        cantidad='2',unidad='unidad declarada',fecha=timezone.now(),guia='QA-REM',discrepancia='Recepción no solicitada; documentada solo en QA')


def presupuesto_qa():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from reportlab.pdfgen.canvas import Canvas
    stream=BytesIO();c=Canvas(stream);c.drawString(40,750,'PRESUPUESTO FICTICIO QA - NO ES ACEPTACION');c.save()
    return SimpleUploadedFile('presupuesto-qa.pdf',stream.getvalue(),content_type='application/pdf')


def verificacion_qa():
    # Test database only; never an operational approval or real identity.
    return dict(identidad='IDENTIDAD SINTETICA QA',calidad='TITULAR',calidad_representante='',
        medio_verificacion='Simulacion unitaria, sin identidad real',canal='PRESENCIAL_FIRMADA',
        respaldo_verificacion='Archivo sintetico del test, no aceptacion comercial real',verificacion_confirmada=True)


@override_settings(**FLAGS)
class OperacionTests(TestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp=preparar()
        self.gerencia=usuario(self.empresa,'gerencia-qa','GERENCIA')
        self.d=aprobar_diagnostico(self.empresa,self.oficina,self.planta,self.tecnico,self.exp)
        self.base=dict(empresa=self.empresa,expediente_id=self.exp.pk)

    def propuesta(self,**kw):
        kw.setdefault('archivo', presupuesto_qa())
        return s.proponer(usuario=self.gerencia,**self.base,diagnostico_id=self.d.pk,clave=kw.pop('clave',uuid.uuid4()),
            datos=dict(alcance='Alcance ficticio QA sin especificaciones industriales',observaciones='QA',presupuesto_referencia='QA v1'),**kw)

    def decision(self,p=None,decision='APROBADO',key=None):
        p=p or self.propuesta()
        return s.decidir_comercial(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,clave=key or uuid.uuid4(),archivo=pdf_plano(),
            datos=dict(decision=decision,fecha_cliente=timezone.now(),representante_cliente='Representante FICTICIO QA',motivo='Respaldo ficticio de prueba, no aceptación real',**verificacion_qa()))

    def recibir(self,**kw):
        return s.recibir_repuesto(usuario=kw.pop('usuario',self.oficina),**self.base,clave=kw.pop('clave',uuid.uuid4()),datos=kw.pop('datos',repuesto_datos()),**kw)

    def actividad(self):
        return s.iniciar_actividad(usuario=self.tecnico,**self.base,clave=uuid.uuid4(),datos=dict(
            trabajo='Actividad QA sin procedimiento industrial',componentes='Componente QA',inicio=timezone.now(),observaciones=''))

    def resultado(self,a):
        return s.terminar_actividad(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,clave=uuid.uuid4(),
            datos=dict(fin=timezone.now(),trabajo_realizado='Trabajo QA ficticio',resultado='Resultado de prueba',observaciones=''))

    def revisar(self,r,decision='APROBADO'):
        return s.revisar_actividad(usuario=self.planta,empresa=self.empresa,resultado_id=r.pk,clave=uuid.uuid4(),decision=decision,motivo='Revisión QA independiente')

    def test_default_bloquea_aprobacion_y_reparacion(self):
        self.assertIsNone(s.politica())
        p=self.propuesta()
        with self.assertRaises(ValidationError): self.decision(p)
        with self.assertRaises(ValidationError): self.actividad()
        self.assertFalse(DecisionComercial.objects.exists())
        self.assertFalse(ActividadReparacion.objects.exists())

    def test_rechazo_no_autoriza_y_nueva_propuesta_conserva_historia(self):
        p=self.propuesta()
        d=self.decision(p,decision='RECHAZADO')
        self.assertEqual(s.estado(self.exp),'RECHAZADO')
        with self.assertRaises(ValidationError): self.actividad()
        q=self.propuesta()
        self.assertEqual(q.sustituye_id,p.pk)
        self.assertEqual(q.version,2)
        self.assertTrue(DecisionComercial.objects.filter(pk=d.pk).exists())
        self.assertEqual(s.estado(self.exp),'PENDIENTE_AUTORIZACION')

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_autorizacion_valida_exacta_y_revocada_por_nueva_propuesta(self):
        d=self.decision()
        self.assertTrue(s.autorizacion_valida(d))
        self.assertEqual(s.estado(self.exp),'AUTORIZADO')
        self.propuesta()
        self.assertFalse(s.autorizacion_valida(d))
        with self.assertRaises(ValidationError): self.actividad()

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_diagnostico_sustituido_bloquea_sin_borrar(self):
        d=self.decision()
        a=self.actividad()
        t.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Corrección QA')
        self.assertFalse(s.autorizacion_valida(d))
        self.assertEqual(s.estado(self.exp),'OBSERVADO')
        with self.assertRaises(ValidationError): self.resultado(a)
        self.assertTrue(ActividadReparacion.objects.filter(pk=a.pk).exists())

    def test_permisos_comerciales_sin_bypass_admin(self):
        for code in ('ADMIN','CONSULTA','CLIENTE','TECNICO','PLANTA'):
            if code=='CLIENTE':
                user=usuario(self.empresa,'rol-cliente','CONSULTA')
                RolMasiscam.objects.filter(perfil__user=user).update(rol='CLIENTE',cliente=self.exp.cliente_contractual)
            else:
                user=usuario(self.empresa,'rol-'+code,code)
            with self.assertRaises(PermissionDenied):
                s.proponer(usuario=user,**self.base,diagnostico_id=self.d.pk,clave=uuid.uuid4(),datos={'alcance':'No autorizado'})

    def test_aislamiento_empresa_y_tecnico_no_asignado(self):
        from accounts.models import Empresa
        otra=Empresa.objects.create(nombre='Empresa ajena QA')
        g=usuario(otra,'otra-gerencia','GERENCIA')
        with self.assertRaises(PermissionDenied): s.expediente(g,otra,self.exp.pk)
        tech=usuario(self.empresa,'tecnico-no-asignado','TECNICO')
        with self.assertRaises(PermissionDenied): s.expediente(tech,self.empresa,self.exp.pk)

    def test_repuestos_sin_autorizacion_no_habilitan_reparacion(self):
        r=self.recibir(fotografia=foto(),archivo=pdf_plano())
        self.assertEqual(r.evidencias.count(),2)
        self.assertEqual(s.estado(self.exp),'PENDIENTE_AUTORIZACION')
        with self.assertRaises(ValidationError): self.actividad()

    def test_cantidades_y_discrepancias(self):
        for value in ('0','-1','NaN','Infinity','0.0000001','100000000000000'):
            data=repuesto_datos();data['cantidad']=value
            with self.assertRaises(ValidationError): self.recibir(datos=data)
        data=repuesto_datos();data['discrepancia']=''
        with self.assertRaises(ValidationError): self.recibir(datos=data)
        self.assertEqual(self.recibir().cantidad,Decimal('2'))

    def test_recepcion_idempotente_conflicto_contenido(self):
        key=uuid.uuid4();data=repuesto_datos()
        r=self.recibir(clave=key,datos=data)
        self.assertEqual(self.recibir(clave=key,datos=data).pk,r.pk)
        data['cantidad']='3'
        with self.assertRaises(ValidationError): self.recibir(clave=key,datos=data)
        self.assertEqual(RecepcionRepuesto.objects.count(),1)

    def test_repuestos_requeridos_y_acumulacion_parcial(self):
        d=t.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Definir piezas QA')
        d=t.guardar_revision(usuario=self.tecnico,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=d.version_edicion,clave=uuid.uuid4(),datos={'repuestos':'REQUERIDOS'})
        p=t.agregar_pieza(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,version=d.version_edicion,clave=uuid.uuid4(),datos=dict(
            origen='MANUAL',descripcion='Parte QA',numero_parte='QA',cantidad='2',unidad='unidad declarada',condicion='QA',accion='QA',motivo_manual='QA sin biblioteca'))
        d.refresh_from_db()
        t.enviar_revision(usuario=self.tecnico,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=d.version_edicion,clave=uuid.uuid4())
        t.decidir_diagnostico(usuario=self.planta,empresa=self.empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
        data=repuesto_datos();data.update(pieza_id=p.pk,cantidad='1',discrepancia='Entrega parcial QA')
        self.recibir(datos=data)
        data['discrepancia']=''
        self.recibir(datos=data)
        with self.assertRaises(ValidationError): self.recibir(datos=data)

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_reparacion_completa_versiones_fotos_usos_y_pdf(self):
        qr=self.exp.registro.equipo.token_publico
        self.decision()
        a=self.actividad();r=self.recibir(fotografia=foto())
        s.usar_repuesto(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,recepcion_id=r.pk,cantidad='1',observaciones='QA',clave=uuid.uuid4())
        s.evidencia_actividad(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,clave=uuid.uuid4(),clase='ANTES',leyenda='Foto QA',archivo=foto())
        x=self.resultado(a)
        self.revisar(x,decision='OBSERVADO')
        y=self.resultado(a)
        self.assertEqual(y.version,2)
        self.revisar(y)
        self.assertEqual(s.estado(self.exp),'REPARACION_FINALIZADA')
        key=uuid.uuid4()
        em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPARACION',clave=key)
        doc=s.generar_documento(em.pk)
        self.assertEqual(s.generar_documento(em.pk),doc)
        em.refresh_from_db()
        with em.documento.archivo.open('rb') as f: binary=f.read()
        self.assertEqual(hashlib.sha256(binary).hexdigest(),em.hash_sha256)
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(binary))
        self.assertIn('REPORTE INTERNO',reader.pages[0].extract_text())
        self.assertFalse(reader.get_fields())
        self.assertEqual(self.exp.registro.equipo.token_publico,qr)
        self.assertEqual(EmisionOperativa.objects.count(),1)

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_uso_limite_cantidad_y_congelacion(self):
        self.decision();a=self.actividad();r=self.recibir()
        args=dict(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,recepcion_id=r.pk,observaciones='QA')
        s.usar_repuesto(**args,cantidad='2',clave=uuid.uuid4())
        with self.assertRaises(ValidationError): s.usar_repuesto(**args,cantidad='0.1',clave=uuid.uuid4())
        self.resultado(a)
        with self.assertRaises(ValidationError): s.evidencia_actividad(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,clave=uuid.uuid4(),clase='ANTES',leyenda='QA',archivo=foto())

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_auto_revision_aun_cambiando_rol(self):
        self.decision();a=self.actividad();r=self.resultado(a)
        RolMasiscam.objects.filter(perfil__user=self.tecnico).update(rol='PLANTA')
        with self.assertRaises(PermissionDenied):
            s.revisar_actividad(usuario=self.tecnico,empresa=self.empresa,resultado_id=r.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Intento')

    def test_documento_versionado_misma_reserva_y_nueva_version(self):
        self.recibir()
        args=dict(usuario=self.oficina,**self.base,tipo='REPUESTOS')
        a=s.reservar_documento(**args,clave=uuid.uuid4())
        b=s.reservar_documento(**args,clave=uuid.uuid4())
        self.assertEqual(a.pk,b.pk)
        s.generar_documento(a.pk)
        c=s.reservar_documento(**args,clave=uuid.uuid4(),nueva_version=True,motivo='Nueva entrega QA')
        self.assertEqual(c.version,2)
        self.assertNotEqual(s.generar_documento(c.pk),a.documento_id)

    def test_storage_falla_y_reserva_recuperable(self):
        self.recibir()
        em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPUESTOS',clave=uuid.uuid4())
        with patch('django.core.files.storage.FileSystemStorage._save',side_effect=OSError('QA storage down')):
            with self.assertRaises(OSError): s.generar_documento(em.pk)
        em.refresh_from_db()
        self.assertIsNone(em.documento_id)
        self.assertIsNotNone(s.generar_documento(em.pk))

    def test_storage_falso_exito_no_documento_emitido(self):
        self.recibir();em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPUESTOS',clave=uuid.uuid4())
        with patch('django.core.files.storage.FileSystemStorage._save',return_value='no-existe-qa.pdf'):
            with self.assertRaises(ValidationError): s.generar_documento(em.pk)
        em.refresh_from_db();self.assertIsNone(em.documento_id)

    def test_corrupcion_bloquea_reintento_y_descarga(self):
        self.recibir();em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPUESTOS',clave=uuid.uuid4())
        s.generar_documento(em.pk);em.refresh_from_db()
        with em.documento.archivo.open('wb') as f: f.write(b'CORRUPTO-QA')
        with self.assertRaises(ValidationError): s.generar_documento(em.pk)
        c=Client();c.force_login(self.oficina)
        self.assertEqual(c.get(reverse('masiscam:operacion_documento',args=[self.exp.pk,em.documento_id])).status_code,404)

    def test_historial_orm_y_sql_protegidos(self):
        r=self.recibir()
        r.descripcion='Intento'
        with self.assertRaises(PermissionDenied): r.save()
        with self.assertRaises((PermissionDenied,ProtectedError)),transaction.atomic(): r.delete()
        with self.assertRaises(IntegrityError),transaction.atomic(): RecepcionRepuesto.objects.filter(pk=r.pk).update(cantidad=99)

    def test_documento_privado_inmutable_legacy_bloqueado(self):
        p=self.propuesta(archivo=pdf_plano());e=p.evidencias.get()
        c=Client();c.force_login(self.tecnico)
        self.assertEqual(c.get(reverse('masiscam:operacion_documento',args=[self.exp.pk,e.documento_id])).status_code,404)
        self.assertIn(c.get(reverse('masiscam:documento_privado',args=[e.documento.proyecto_id,e.documento_id])).status_code,(403,404))
        with self.assertRaises(IntegrityError),transaction.atomic(): Documento.objects.filter(pk=e.documento_id).update(publico=True)
        with self.assertRaises(IntegrityError),transaction.atomic(): Documento.objects.filter(pk=e.documento_id).update(archivo='otro.pdf')

    def test_oficina_adjunta_respaldo_sin_aprobar(self):
        p=self.propuesta()
        e=s.respaldo_propuesta(usuario=self.oficina,empresa=self.empresa,propuesta_id=p.pk,clave=uuid.uuid4(),leyenda='Respaldo QA',archivo=pdf_plano())
        self.assertEqual(e.propuesta_id,p.pk)
        self.assertEqual(s.estado(self.exp),'PENDIENTE_AUTORIZACION')

    def test_http_manipulado_aceptacion_y_estados(self):
        p=self.propuesta();c=Client();c.force_login(self.gerencia)
        url=reverse('masiscam:operacion_operar',args=[self.exp.pk,'decision'])
        response=c.post(url,dict(clave=uuid.uuid4(),propuesta_id=p.pk,decision='APROBADO',fecha_cliente='2026-11-06T10:00',representante_cliente='QA',motivo='QA',archivo=pdf_plano()))
        self.assertEqual(response.status_code,400)
        self.assertFalse(DecisionComercial.objects.exists())
        with self.assertRaises(ValidationError): s.recibir_repuesto(usuario=self.oficina,**self.base,clave=uuid.uuid4(),datos={**repuesto_datos(),'estado':'AUTORIZADO'})

    def test_http_roles_y_local_desactivado(self):
        c=Client();c.force_login(self.oficina)
        self.assertEqual(c.get(reverse('masiscam:operacion_detalle',args=[self.exp.pk])).status_code,200)
        self.assertEqual(c.get(reverse('masiscam:operacion_operar',args=[self.exp.pk,'propuesta'])).status_code,200)
        with override_settings(MASISCAM_OPERACION_LOCAL=False):
            self.assertEqual(c.get(reverse('masiscam:operacion_detalle',args=[self.exp.pk])).status_code,404)
            with self.assertRaises(PermissionDenied): self.recibir()

    def test_drive_simulado_posterior_commit(self):
        with override_settings(GOOGLE_DRIVE_ENABLED=True),patch('masiscam.operacion_services.t._encolar_documento') as queue,self.captureOnCommitCallbacks(execute=True):
            r=self.recibir(fotografia=foto())
            self.assertFalse(queue.called)
        queue.assert_called_once_with(r.evidencias.get().documento_id)

    def test_politica_incompleta_o_futura_no_habilita(self):
        for pol in (True,{'version':'X'},{**QA_POLICY,'vigente_desde':'2099-01-01T00:00:00+00:00'}):
            with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=pol):
                self.assertIsNone(s.politica())

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_decision_idempotente_y_respaldo_inmutable(self):
        p=self.propuesta();key=uuid.uuid4()
        from django.core.files.uploadedfile import SimpleUploadedFile
        binary=pdf_plano().read()
        data=dict(decision='APROBADO',fecha_cliente=timezone.now(),representante_cliente='QA FICTICIO',motivo='QA',**verificacion_qa())
        args=dict(usuario=self.gerencia,empresa=self.empresa,propuesta_id=p.pk,clave=key,datos=data)
        d=s.decidir_comercial(**args,archivo=SimpleUploadedFile('respaldo-qa.pdf',binary,content_type='application/pdf'))
        self.assertEqual(s.decidir_comercial(**args,archivo=SimpleUploadedFile('respaldo-qa.pdf',binary,content_type='application/pdf')).pk,d.pk)
        with self.assertRaises(ValidationError):
            s.respaldo_propuesta(usuario=self.oficina,empresa=self.empresa,propuesta_id=p.pk,clave=uuid.uuid4(),leyenda='Posterior',archivo=pdf_plano())
        self.assertEqual(DecisionComercial.objects.count(),1)

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_retencion_y_cambio_politica_invalidan_sin_alterar_decision(self):
        d=self.decision()
        with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
            self.assertFalse(s.autorizacion_valida(d))
            with self.assertRaises(ValidationError): self.actividad()
        self.assertEqual(DecisionComercial.objects.get(pk=d.pk).decision,'APROBADO')

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_fechas_incoherentes_y_otro_tecnico_no_edita(self):
        self.decision();a=self.actividad()
        with self.assertRaises(ValidationError):
            s.terminar_actividad(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,clave=uuid.uuid4(),datos=dict(
                fin=a.inicio-timezone.timedelta(days=1),trabajo_realizado='QA',resultado='QA'))
        otro=usuario(self.empresa,'otro-tecnico-op','TECNICO')
        t.asignar_tecnico(usuario=self.planta,**self.base,tecnico_id=otro.pk,motivo='Reasignación QA',clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            s.terminar_actividad(usuario=otro,empresa=self.empresa,actividad_id=a.pk,clave=uuid.uuid4(),datos=dict(fin=timezone.now(),trabajo_realizado='QA',resultado='QA'))

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_fuente_sustituida_durante_pdf_no_publica(self):
        self.decision();a=self.actividad();self.revisar(self.resultado(a))
        em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPARACION',clave=uuid.uuid4())
        from .operacion_pdf import construir_documento
        def changed(*args,**kw):
            pdf=construir_documento(*args,**kw)
            t.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Cambio durante render QA')
            return pdf
        with patch('masiscam.operacion_pdf.construir_documento',side_effect=changed):
            with self.assertRaises(ValidationError): s.generar_documento(em.pk)
        em.refresh_from_db();self.assertIsNone(em.documento_id)

    def test_task_error_y_reintento_reserva_original(self):
        self.recibir();em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPUESTOS',clave=uuid.uuid4())
        from .operacion_tasks import generar_documento_operativo
        with patch('masiscam.operacion_services.generar_documento',side_effect=OSError('QA')):
            with self.assertRaises(OSError): generar_documento_operativo.run(em.pk)
        em.refresh_from_db();self.assertEqual(em.estado,'ERROR');self.assertIsNone(em.documento_id)
        generar_documento_operativo.run(em.pk)
        em.refresh_from_db();self.assertEqual(em.estado,'GENERADA')

    def test_archivos_falsos_rechazados_sin_recepcion_parcial(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad=SimpleUploadedFile('foto.png',b'not a photograph',content_type='image/png')
        with self.assertRaises(ValidationError): self.recibir(fotografia=bad)
        self.assertFalse(RecepcionRepuesto.objects.exists())

    def test_tecnico_no_recibe_y_gerencia_no_registra_trabajo(self):
        with self.assertRaises(PermissionDenied): self.recibir(usuario=self.tecnico)
        with self.assertRaises(PermissionDenied):
            s.iniciar_actividad(usuario=self.gerencia,**self.base,clave=uuid.uuid4(),datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))

    def test_multiples_fotografias_repuesto_y_snapshot_previo(self):
        r=self.recibir()
        em=s.reservar_documento(usuario=self.oficina,**self.base,tipo='REPUESTOS',clave=uuid.uuid4())
        key=uuid.uuid4();binary=foto().read()
        from django.core.files.uploadedfile import SimpleUploadedFile
        args=dict(usuario=self.oficina,empresa=self.empresa,recepcion_id=r.pk,clave=key,leyenda='Vista adicional QA')
        e=s.evidencia_repuesto(**args,archivo=SimpleUploadedFile('qa.png',binary,content_type='image/png'))
        self.assertEqual(s.evidencia_repuesto(**args,archivo=SimpleUploadedFile('qa.png',binary,content_type='image/png')).pk,e.pk)
        s.evidencia_repuesto(usuario=self.oficina,empresa=self.empresa,recepcion_id=r.pk,clave=uuid.uuid4(),leyenda='Otra vista QA',archivo=foto())
        self.assertEqual(r.evidencias.count(),2)
        em.refresh_from_db();self.assertEqual(em.snapshot['evidencias'],[])
        with self.assertRaises(PermissionDenied):
            s.evidencia_repuesto(usuario=self.tecnico,empresa=self.empresa,recepcion_id=r.pk,clave=uuid.uuid4(),leyenda='QA',archivo=foto())

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_respaldo_comercial_corrupto_no_permite_inicio(self):
        d=self.decision();doc=d.evidencias.get().documento
        with doc.archivo.open('wb') as f: f.write(b'QA CORRUPTO')
        with self.assertRaises(ValidationError): self.actividad()
        self.assertFalse(ActividadReparacion.objects.exists())

    @override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
    def test_reporte_previo_no_se_presenta_como_actual_tras_nueva_actividad(self):
        self.decision();a=self.actividad();self.revisar(self.resultado(a))
        args=dict(usuario=self.oficina,**self.base,tipo='REPARACION')
        em=s.reservar_documento(**args,clave=uuid.uuid4())
        doc=s.generar_documento(em.pk)
        b=self.actividad()
        with self.assertRaises(ValidationError): s.reservar_documento(**args,clave=uuid.uuid4())
        self.revisar(self.resultado(b))
        with self.assertRaises(ValidationError): s.reservar_documento(**args,clave=uuid.uuid4())
        new=s.reservar_documento(**args,clave=uuid.uuid4(),nueva_version=True,motivo='Actividad adicional dentro del escenario QA')
        self.assertEqual(new.version,2)
        self.assertEqual(EmisionOperativa.objects.get(pk=em.pk).documento_id,doc)

    def test_listado_heredado_no_filtra_metadatos_comerciales(self):
        p=self.propuesta(archivo=pdf_plano());doc=p.evidencias.get().documento
        c=Client();c.force_login(self.tecnico)
        response=c.get(reverse('masiscam:proyecto_detalle',args=[doc.proyecto_id]))
        if response.status_code==200:
            self.assertNotIn(doc,list(response.context['proyecto'].documentos.all()))
        else:
            self.assertIn(response.status_code,(403,404))


@skipUnless(connection.vendor=='postgresql','Concurrencia requiere PostgreSQL real aislado')
@override_settings(**FLAGS,MASISCAM_POLITICA_COMERCIAL_LOCAL=QA_POLICY)
class OperacionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp=preparar()

    def concurrent(self,fn):
        barrier=Barrier(2)
        def run():
            connections.close_all()
            try:
                barrier.wait(timeout=30)
                return fn()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda _:run(),range(2)))

    def test_recepcion_idempotente_concurrente(self):
        key=uuid.uuid4();data=repuesto_datos()
        results=self.concurrent(lambda:s.recibir_repuesto(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=key,datos=data).pk)
        self.assertEqual(results[0],results[1]);self.assertEqual(RecepcionRepuesto.objects.count(),1)

    def test_emision_concurrente_un_documento(self):
        s.recibir_repuesto(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos=repuesto_datos())
        em=s.reservar_documento(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),tipo='REPUESTOS')
        results=self.concurrent(lambda:s.generar_documento(em.pk))
        self.assertEqual(results[0],results[1])
        self.assertEqual(Auditoria.objects.filter(accion='OPERACION_DOCUMENTO_GENERADO').count(),1)

    def test_propuesta_concurrente_version_e_idempotencia(self):
        d=aprobar_diagnostico(self.empresa,self.oficina,self.planta,self.tecnico,self.exp)
        g=usuario(self.empresa,'gerencia-concurrente','GERENCIA');key=uuid.uuid4()
        results=self.concurrent(lambda:s.proponer(usuario=g,empresa=self.empresa,expediente_id=self.exp.pk,
            diagnostico_id=d.pk,clave=key,datos={'alcance':'Propuesta QA concurrente'}).pk)
        self.assertEqual(results[0],results[1]);self.assertEqual(PropuestaComercial.objects.count(),1)

    def test_parcial_concurrente_unica_version_y_decision(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        d=aprobar_diagnostico(self.empresa,self.oficina,self.planta,self.tecnico,self.exp)
        g=usuario(self.empresa,'gerencia-parcial-qa','GERENCIA')
        p=s.proponer(usuario=g,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=d.pk,
            clave=uuid.uuid4(),datos={'alcance':'Alcance completo QA','presupuesto_referencia':'QA v1'},archivo=presupuesto_qa())
        key=uuid.uuid4();binary=pdf_plano().read()
        data=dict(decision='PARCIAL',fecha_cliente=timezone.now(),representante_cliente='QA SIN VALIDEZ REAL',
            motivo='Solo test concurrente',alcance_autorizado='Alcance reducido QA',**verificacion_qa())
        results=self.concurrent(lambda:s.decidir_comercial(usuario=g,empresa=self.empresa,propuesta_id=p.pk,
            clave=key,datos=data,archivo=SimpleUploadedFile('test.pdf',binary,content_type='application/pdf')).pk)
        self.assertEqual(results[0],results[1])
        self.assertEqual(PropuestaComercial.objects.count(),2)
        self.assertEqual(DecisionComercial.objects.count(),1)
        self.assertEqual(DecisionComercial.objects.get().propuesta.version,2)

    def test_uso_concurrente_no_supera_recibido(self):
        d=aprobar_diagnostico(self.empresa,self.oficina,self.planta,self.tecnico,self.exp)
        g=usuario(self.empresa,'gerencia-uso','GERENCIA')
        p=s.proponer(usuario=g,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=d.pk,clave=uuid.uuid4(),
            datos={'alcance':'QA','presupuesto_referencia':'QA v1'},archivo=presupuesto_qa())
        s.decidir_comercial(usuario=g,empresa=self.empresa,propuesta_id=p.pk,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(
            decision='APROBADO',fecha_cliente=timezone.now(),representante_cliente='FICTICIO QA',motivo='Prueba aislada',**verificacion_qa()))
        a=s.iniciar_actividad(usuario=self.tecnico,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        r=s.recibir_repuesto(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos=repuesto_datos())
        def use():
            try:
                s.usar_repuesto(usuario=self.tecnico,empresa=self.empresa,actividad_id=a.pk,recepcion_id=r.pk,cantidad='2',observaciones='QA',clave=uuid.uuid4())
                return 'OK'
            except ValidationError:
                return 'BLOQUEADO'
        self.assertCountEqual(self.concurrent(use),['OK','BLOQUEADO'])
        self.assertEqual(UsoRepuesto.objects.count(),1)

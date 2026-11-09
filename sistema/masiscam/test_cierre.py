"""Synthetic QA only: no commercial/industrial approvals are installed."""
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from io import BytesIO
from unittest.mock import patch
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction,connection,connections
from django.db.models.deletion import ProtectedError
from django.test import TestCase,TransactionTestCase,override_settings,Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from .models import *
from . import cierre_services as s,calibracion_services as c,tecnica_services as t
from .test_calibracion import FLAGS as CAL_FLAGS,preparar_calibracion,anchor_qa,validar_qa,completar
from .test_tecnica import usuario
from .test_utils import consumir_respuesta

FLAGS=dict(**CAL_FLAGS,MASISCAM_CIERRE_LOCAL=True)


def preparar_final():
    empresa,oficina,planta,tecnico,exp,revisor,p=preparar_calibracion()
    policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(p)])
    policy.enable()
    try:
        validar_qa(empresa,revisor,p)
        cal=c.iniciar(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,plantilla_id=p.pk,clave=uuid.uuid4())
        cal=completar(empresa,tecnico,cal)
        c.enviar(usuario=tecnico,empresa=empresa,calibracion_id=cal.pk,version=cal.version_edicion,clave=uuid.uuid4())
        c.decidir(usuario=planta,empresa=empresa,calibracion_id=cal.pk,clave=uuid.uuid4(),decision='APROBADA',motivo='SIMULACIÓN QA SIN VALIDEZ INDUSTRIAL',evaluaciones={r['codigo']:'CONFORME' for r in p.definicion['mediciones']})
    finally: policy.disable()
    return empresa,oficina,planta,tecnico,exp,p


@override_settings(**FLAGS)
class CierreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa,cls.oficina,cls.planta,cls.tecnico,cls.exp,cls.p=preparar_final()

    def setUp(self):
        self.policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(self.p)])
        self.policy.enable();self.addCleanup(self.policy.disable)
        self.base=dict(empresa=self.empresa,expediente_id=self.exp.pk)
        self.gerente=usuario(self.empresa,'gerente-final-qa','GERENCIA')

    def reservar(self,**kwargs):
        return s.reservar(usuario=self.oficina,**self.base,clave=uuid.uuid4(),**kwargs)
    def emitido(self):
        em=self.reservar();s.generar(em.pk);em.refresh_from_db();return em
    def aprobado(self):
        em=self.emitido()
        s.revisar(usuario=self.planta,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Revisión FINAL FICTICIA QA')
        em.refresh_from_db();return em
    def cerrar(self,em,**kwargs):
        return s.cerrar(usuario=self.gerente,empresa=self.empresa,informe_id=em.pk,clave=kwargs.pop('clave',uuid.uuid4()),motivo='Cierre QA, sin validez real',**kwargs)

    def test_flujo_completo_pdf_cierre_qr_historia(self):
        token=self.exp.registro.equipo.token_publico
        repair=s.o.reservar_documento(usuario=self.oficina,**self.base,tipo='REPARACION',clave=uuid.uuid4())
        repair_doc=s.o.generar_documento(repair.pk)
        em=self.aprobado();closed=self.cerrar(em)
        self.assertIn(repair_doc,[item['documento'] for item in em.snapshot['anexos']])
        self.assertEqual(closed.informe_id,em.pk)
        em.refresh_from_db();self.assertEqual(em.estado,'FINALIZADO')
        self.exp.registro.equipo.refresh_from_db();self.assertEqual(token,self.exp.registro.equipo.token_publico)
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='EXPEDIENTE_CERRADO').exists())
        from pypdf import PdfReader
        with em.documento.archivo.open('rb') as f: data=f.read()
        reader=PdfReader(BytesIO(data));text='\n'.join(p.extract_text() for p in reader.pages)
        for word in ('INFORME FINAL','Diagnóstico','Repuestos','Calibración','QA-16','Anexos'): self.assertIn(word,text)
        self.assertEqual(hashlib.sha256(data).hexdigest(),em.hash_sha256)
        self.assertFalse(reader.get_fields())
        self.assertTrue(all(abs(float(p.mediabox.width)-595.2756)<1 for p in reader.pages))

    def test_reserva_generacion_y_cierre_idempotentes(self):
        key=uuid.uuid4();kw=dict(usuario=self.oficina,**self.base,clave=key)
        em=s.reservar(**kw);self.assertEqual(em.pk,s.reservar(**kw).pk)
        doc=s.generar(em.pk);self.assertEqual(doc,s.generar(em.pk));self.assertEqual(InformeFinal.objects.count(),1)
        s.revisar(usuario=self.planta,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='QA')
        closekey=uuid.uuid4();closed=self.cerrar(em,clave=closekey)
        self.assertEqual(closed.pk,self.cerrar(em,clave=closekey).pk)
        self.assertEqual(closed.pk,self.cerrar(em).pk);self.assertEqual(CierreExpediente.objects.count(),1)

    def test_clave_con_otro_contenido_no_duplica(self):
        key=uuid.uuid4();s.reservar(usuario=self.oficina,**self.base,clave=key)
        with self.assertRaises(ValidationError): s.reservar(usuario=self.oficina,**self.base,clave=key,motivo='Distinto')
        self.assertEqual(InformeFinal.objects.count(),1)

    def test_comercial_y_protocolo_pendientes_bloquean(self):
        with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
            with self.assertRaises(ValidationError): self.reservar()
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[]):
            with self.assertRaises(ValidationError): self.reservar()
        self.assertFalse(InformeFinal.objects.exists())

    def test_retirar_aprobaciones_impide_revisar_y_cerrar(self):
        em=self.aprobado()
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[]):
            with self.assertRaises(ValidationError): self.cerrar(em)
        with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
            with self.assertRaises(ValidationError): self.cerrar(em)
        self.assertFalse(CierreExpediente.objects.exists())
        c.iniciar(usuario=self.planta,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=self.p.pk,clave=uuid.uuid4(),motivo='Nueva revisión FICTICIA QA')
        with self.assertRaises(ValidationError): self.cerrar(em)
        from .cierre_dashboard import resumen
        self.assertEqual(resumen(self.oficina,self.empresa)['expedientes'][0].etapa_actual,'CALIBRACION_NO_INICIADA')

    def test_cierre_sin_revision_prohibido(self):
        em=self.emitido()
        with self.assertRaises(ValidationError): self.cerrar(em)
        self.assertFalse(CierreExpediente.objects.exists())

    def test_observacion_y_correccion_nueva_version(self):
        em=self.emitido();hash_old=em.hash_sha256
        s.revisar(usuario=self.planta,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='OBSERVADO',motivo='Corrección QA documentada')
        with self.assertRaises(ValidationError): self.cerrar(em)
        em2=self.reservar(nueva_version=True,motivo='Corrección de presentación QA')
        s.generar(em2.pk)
        self.assertEqual(em2.version,2);self.assertEqual(em2.sustituye_id,em.pk)
        em.refresh_from_db();self.assertEqual(em.hash_sha256,hash_old)
        with self.assertRaises(ValidationError): s.vigente(em)

    def test_autoaprobacion_tras_cambio_rol(self):
        em=self.emitido()
        RolMasiscam.objects.filter(perfil__user=self.tecnico,perfil__empresa=self.empresa).update(rol='PLANTA')
        with self.assertRaises(PermissionDenied): s.revisar(usuario=self.tecnico,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Intento QA')

    def test_autor_oficina_no_aprueba_cambiando_rol(self):
        em=self.emitido()
        RolMasiscam.objects.filter(perfil__user=self.oficina,perfil__empresa=self.empresa).update(rol='PLANTA')
        with self.assertRaises(PermissionDenied): s.revisar(usuario=self.oficina,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Intento QA')

    def test_roles_y_aislamiento(self):
        em=self.emitido()
        with self.assertRaises(PermissionDenied): s.reservar(usuario=self.planta,**self.base,clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied): s.cerrar(usuario=self.oficina,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),motivo='QA')
        from accounts.models import Empresa
        other=Empresa.objects.create(nombre='OTRA QA');u=usuario(other,'otro-final-qa','PLANTA')
        with self.assertRaises(PermissionDenied): s.expediente(u,other,self.exp.pk)

    def test_storage_fallido_no_envia_ni_cierra(self):
        em=self.reservar()
        with patch('masiscam.cierre_services.t.guardar_archivo',side_effect=OSError('simulado')):
            with self.assertRaises(OSError): s.generar(em.pk)
        em.refresh_from_db();self.assertEqual(em.estado,'BORRADOR');self.assertFalse(em.documento_id)
        self.assertFalse(CierreExpediente.objects.exists())
        s.generar(em.pk);em.refresh_from_db();self.assertEqual(em.estado,'PENDIENTE_REVISION')

    def test_storage_falso_exito_no_confirma(self):
        em=self.reservar()
        with patch('django.core.files.storage.FileSystemStorage._save',return_value='qa-final-no-existe.pdf'):
            with self.assertRaises(ValidationError): s.generar(em.pk)
        em.refresh_from_db();self.assertFalse(em.documento_id);self.assertEqual(em.estado,'BORRADOR')
        self.assertFalse(CierreExpediente.objects.exists())

    def test_pdf_corrupto_bloquea_cierre_y_descarga(self):
        em=self.aprobado()
        with em.documento.archivo.open('rb') as f: original=f.read()
        try:
            with em.documento.archivo.storage.open(em.documento.archivo.name,'wb') as f: f.write(b'corrupto QA')
            with self.assertRaises(ValidationError): self.cerrar(em)
            client=Client();client.force_login(self.oficina)
            self.assertEqual(client.get(reverse('masiscam:cierre_documento',args=[self.exp.pk,em.pk])).status_code,404)
        finally:
            with em.documento.archivo.storage.open(em.documento.archivo.name,'wb') as f: f.write(original)

    def test_cierre_congela_servicios_y_sql(self):
        em=self.aprobado();self.cerrar(em)
        with self.assertRaises(ValidationError): t.asignar_tecnico(usuario=self.planta,**self.base,tecnico_id=self.tecnico.pk,motivo='QA',clave=uuid.uuid4())
        with self.assertRaises(ValidationError): self.reservar(nueva_version=True,motivo='QA')
        with self.assertRaises(IntegrityError),transaction.atomic():
            Calibracion.objects.filter(pk=em.calibracion_id).update(estado='OBSERVADA')
        with self.assertRaises(IntegrityError),transaction.atomic():
            AsignacionTecnica.objects.filter(expediente=self.exp).update(motivo='alterado')
        with self.assertRaises(IntegrityError),transaction.atomic():
            CierreExpediente.objects.filter(expediente=self.exp).update(motivo='alterado')

    def test_proteccion_documental_orm_sql_legacy(self):
        em=self.emitido()
        with self.assertRaises(IntegrityError),transaction.atomic(): Documento.objects.filter(pk=em.documento_id).update(publico=True)
        with self.assertRaises(IntegrityError),transaction.atomic(): InformeFinal.objects.filter(pk=em.pk).update(snapshot={})
        with self.assertRaises((PermissionDenied,ProtectedError)),transaction.atomic(): em.documento.delete()
        client=Client();client.force_login(self.oficina)
        r=client.get(reverse('masiscam:cierre_documento',args=[self.exp.pk,em.pk]));self.assertEqual(r.status_code,200);consumir_respuesta(r)
        r=client.get(reverse('masiscam:documento_privado',args=[self.exp.registro.equipo.proyecto_id,em.documento_id]));self.assertIn(r.status_code,(403,404))

    def test_sql_no_salta_estado_ni_autorevision(self):
        em=self.reservar()
        with self.assertRaises(IntegrityError),transaction.atomic(): InformeFinal.objects.filter(pk=em.pk).update(estado='APROBADO')
        s.generar(em.pk)
        with self.assertRaises(IntegrityError),transaction.atomic(): RevisionInformeFinal.objects.bulk_create([RevisionInformeFinal(informe=em,decision='APROBADO',motivo='QA',responsable=self.tecnico)])

    def test_cambio_fuente_durante_render_bloquea(self):
        em=self.reservar()
        from .cierre_pdf import construir_informe
        def render(*a,**kw):
            self.reservar(nueva_version=True,motivo='Nueva versión durante render QA')
            return construir_informe(*a,**kw)
        with patch('masiscam.cierre_pdf.construir_informe',side_effect=render):
            with self.assertRaises(ValidationError): s.generar(em.pk)
        em.refresh_from_db();self.assertFalse(em.documento_id)

    def test_dashboard_filtros_historial_sin_nmas1(self):
        from .cierre_dashboard import resumen
        em=self.aprobado();self.cerrar(em)
        with CaptureQueriesContext(connection) as queries:
            data=resumen(self.oficina,self.empresa,{'cliente':str(self.exp.cliente_contractual_id),'estado':'FINALIZADO'})
            list(data['actividades'])
        self.assertEqual(data['finalizados'],1);self.assertLess(len(queries),15)
        client=Client();client.force_login(self.oficina)
        for name,args in [('cierre_dashboard',[]),('cierre_detalle',[self.exp.pk]),('cierre_historial',[self.exp.registro.equipo_id])]:
            r=client.get(reverse('masiscam:'+name,args=args));self.assertEqual(r.status_code,200)
        self.assertEqual(client.get(reverse('masiscam:cierre_dashboard')+'?desde=mal').status_code,400)

    def test_flags_y_cliente_no_publican_informe(self):
        client=Client();client.force_login(self.oficina)
        with override_settings(MASISCAM_CIERRE_LOCAL=False): self.assertEqual(client.get(reverse('masiscam:cierre_dashboard')).status_code,404)
        u=usuario(self.empresa,'cliente-final-qa','CONSULTA')
        RolMasiscam.objects.filter(perfil__user=u,perfil__empresa=self.empresa).update(rol='CLIENTE',cliente=self.exp.cliente_contractual)
        client.force_login(u)
        self.assertEqual(client.get(reverse('masiscam:cierre_historial',args=[self.exp.registro.equipo_id])).status_code,403)

    def test_tarea_reintenta_error_sin_nueva_version(self):
        from .cierre_tasks import generar_informe_final
        em=self.reservar()
        with patch('masiscam.cierre_services.generar',side_effect=OSError('simulado')):
            with self.assertRaises(OSError): generar_informe_final(em.pk)
        em.refresh_from_db();self.assertTrue(em.error)
        generar_informe_final(em.pk);em.refresh_from_db();self.assertEqual(em.estado,'PENDIENTE_REVISION');self.assertEqual(InformeFinal.objects.count(),1)

    def test_drive_encola_despues_commit(self):
        em=self.reservar()
        with override_settings(GOOGLE_DRIVE_ENABLED=True),patch('masiscam.cierre_services.t._encolar_documento') as queue:
            with self.captureOnCommitCallbacks(execute=True):
                doc=s.generar(em.pk);self.assertFalse(queue.called)
            queue.assert_called_once_with(doc)

    def test_worker_descubre_tareas_sin_importar_vistas(self):
        from config.celery import app
        app.autodiscover_tasks(['masiscam'],force=True)
        for module,name in [('cierre','generar_informe_final'),('calibracion','generar_informe_calibracion'),('tecnica','generar_informe_inicial'),('operacion','generar_documento_operativo'),('recepcion','generar_guia_ingreso')]:
            self.assertIn(f'masiscam.{module}_tasks.{name}',app.tasks)

    def test_post_manipulado_no_aprueba_ni_cierra(self):
        em=self.emitido();client=Client();client.force_login(self.oficina)
        url=reverse('masiscam:cierre_detalle',args=[self.exp.pk])
        for op in ('revisar','cerrar'):
            response=client.post(url,dict(op=op,informe=em.pk,clave=str(uuid.uuid4()),decision='APROBADO',motivo='Intento QA',estado='FINALIZADO'))
            self.assertEqual(response.status_code,403)
        self.assertFalse(RevisionInformeFinal.objects.exists());self.assertFalse(CierreExpediente.objects.exists())
        client.force_login(self.planta)
        response=client.post(url,dict(op='revisar',informe='no-es-un-id',clave=str(uuid.uuid4()),decision='APROBADO',motivo='Intento QA'))
        self.assertEqual(response.status_code,400)

    def test_filtro_empresa_no_escalable_y_tecnico_no_asignado(self):
        from .cierre_dashboard import resumen
        from accounts.models import Empresa
        other=Empresa.objects.create(nombre='OTRA EMPRESA QA')
        with self.assertRaises(PermissionDenied): resumen(self.oficina,self.empresa,{'empresa':str(other.pk)})
        tech=usuario(self.empresa,'sin-asignacion-final','TECNICO')
        self.assertEqual(resumen(tech,self.empresa)['total'],0)
        with self.assertRaises(PermissionDenied): s.expediente(tech,self.empresa,self.exp.pk)
        client=Client();client.force_login(tech)
        self.assertEqual(client.get(reverse('masiscam:cierre_historial',args=[self.exp.registro.equipo_id])).status_code,403)

    def test_dashboard_consultas_constantes_al_crecer(self):
        from .cierre_dashboard import resumen
        from .recepcion_services import abrir_recepcion
        from .test_recepcion import argumentos
        with CaptureQueriesContext(connection) as first:
            data=resumen(self.oficina,self.empresa);list(data['actividades'])
        for _ in range(5):
            abrir_recepcion(**argumentos(self.empresa,self.oficina,self.exp.cliente_contractual,self.exp.registro.equipo))
        with CaptureQueriesContext(connection) as many:
            data=resumen(self.oficina,self.empresa);list(data['actividades'])
        self.assertEqual(data['total'],6)
        self.assertEqual(len(first),len(many))


@skipUnless(connection.vendor=='postgresql','Concurrencia requiere PostgreSQL real.')
@override_settings(**FLAGS)
class CierreConcurrenciaTests(TransactionTestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp,self.p=preparar_final()
        self.policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(self.p)])
        self.policy.enable();self.addCleanup(self.policy.disable)

    def paralelo(self,fn):
        barrier=Barrier(2)
        def worker():
            connections.close_all()
            try: barrier.wait(timeout=30);return fn()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker) for _ in range(2)]
            return [f.result(timeout=120) for f in futures]

    def test_reservas_y_generacion_concurrentes(self):
        key=uuid.uuid4()
        ids=self.paralelo(lambda:s.reservar(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=key).pk)
        self.assertEqual(ids[0],ids[1]);self.assertEqual(InformeFinal.objects.count(),1)
        docs=self.paralelo(lambda:s.generar(ids[0]));self.assertEqual(docs[0],docs[1])
        self.assertEqual(Documento.objects.filter(informe_final__isnull=False).count(),1)

    def test_cierre_concurrente_una_sola_decision(self):
        em=s.reservar(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4())
        s.generar(em.pk)
        s.revisar(usuario=self.planta,empresa=self.empresa,informe_id=em.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='QA')
        gerente=usuario(self.empresa,'gerencia-cierre-concurrente','GERENCIA');key=uuid.uuid4()
        ids=self.paralelo(lambda:s.cerrar(usuario=gerente,empresa=self.empresa,informe_id=em.pk,clave=key,motivo='QA').pk)
        self.assertEqual(ids[0],ids[1]);self.assertEqual(CierreExpediente.objects.count(),1)

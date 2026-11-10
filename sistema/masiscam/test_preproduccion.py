"""Regression checks for concrete findings; no real DB, Drive or approvals."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import secrets
import tempfile
import uuid
from unittest.mock import MagicMock,patch
import environ
from django.core.checks import run_checks,Tags
from django.core.exceptions import PermissionDenied,ImproperlyConfigured
from django.db import connection,transaction,IntegrityError
from django.test import TestCase,SimpleTestCase,override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from config.expedientes_config import configurar
from . import cierre_services as s,tecnica_services as t,operacion_services as o
from .models import ActividadReparacion,ResultadoReparacion
from .test_cierre import preparar_final,FLAGS
from .test_calibracion import anchor_qa
from .test_tecnica import usuario


@override_settings(**FLAGS)
class IntegridadProduccionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa,cls.oficina,cls.planta,cls.tecnico,cls.exp,cls.plantilla=preparar_final()

    def setUp(self):
        policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(self.plantilla)])
        policy.enable();self.addCleanup(policy.disable)

    def test_filtro_tecnico_no_incluye_asignacion_historica(self):
        from .cierre_dashboard import resumen
        nuevo=usuario(self.empresa,'tecnico-preproduccion-qa','TECNICO')
        t.asignar_tecnico(usuario=self.planta,empresa=self.empresa,expediente_id=self.exp.pk,tecnico_id=nuevo.pk,motivo='Reasignación QA',clave=uuid.uuid4())
        self.assertEqual(resumen(self.oficina,self.empresa,{'tecnico':str(self.tecnico.pk)})['total'],0)
        self.assertEqual(resumen(self.oficina,self.empresa,{'tecnico':str(nuevo.pk)})['total'],1)

    def test_resultado_aprobado_no_admite_otro_por_orm_ni_sql(self):
        act=ActividadReparacion.objects.get(expediente=self.exp)
        def row(): return ResultadoReparacion(expediente=self.exp,actividad=act,autor=self.tecnico,version=2,
            fin=timezone.now(),trabajo_realizado='Intento QA',resultado='No válido',snapshot={'QA':True})
        with self.assertRaises(PermissionDenied),transaction.atomic(): row().save()
        with self.assertRaises(IntegrityError),transaction.atomic(): ResultadoReparacion.objects.bulk_create([row()])
        self.assertEqual(act.resultados.count(),1)

    def test_estado_reparacion_consultas_constantes(self):
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(o.estado(self.exp),'REPARACION_FINALIZADA')
        for _ in range(5):
            o.iniciar_actividad(usuario=self.tecnico,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos=dict(
                trabajo='Actividad adicional QA',componentes='QA',inicio=timezone.now()))
        with CaptureQueriesContext(connection) as after:
            self.assertEqual(o.estado(self.exp),'EN_REPARACION')
        self.assertEqual(len(before),len(after))

    def test_drive_confirma_bytes_remotos_y_no_sobrescribe(self):
        from .services import GoogleDriveService
        em=s.reservar(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4())
        s.generar(em.pk);em.refresh_from_db();doc=em.documento
        with doc.archivo.open('rb') as stream: content=stream.read()
        doc.drive_upload_id='REMOTE-FICTICIO-QA'
        service=GoogleDriveService.__new__(GoogleDriveService);service.drive=MagicMock()
        valid=dict(id=doc.drive_upload_id,size=str(len(content)),md5Checksum=hashlib.md5(content,usedforsecurity=False).hexdigest())
        for invalid in (dict(valid,md5Checksum='0'*32),dict(valid,size='0'),dict(valid,trashed=True),{'id':doc.drive_upload_id}):
            service.drive.files().get.return_value.execute.return_value=invalid
            with self.assertRaises(ValueError): service.subir_documento(doc)
        service.drive.files().get.return_value.execute.return_value=valid
        self.assertEqual(service.subir_documento(doc),valid)
        service.drive.files().create.assert_not_called();service.drive.files().update.assert_not_called();service.drive.files().delete.assert_not_called()
        self.assertEqual(hashlib.sha256(content).hexdigest(),doc.hash_sha256)


class ConfiguracionProduccionTests(SimpleTestCase):
    def config(self,values=None,debug=False):
        with patch.dict(os.environ,values or {},clear=True): return configurar(environ.Env(),debug)

    def test_produccion_sin_debug_y_sin_aprobaciones_automaticas(self):
        config=self.config({'MASISCAM_EXPEDIENTES_ENABLED':'true'})
        for phase in ('RECEPCION','TECNICA','OPERACION','CALIBRACION','CIERRE'): self.assertTrue(config[f'MASISCAM_{phase}_LOCAL'])
        self.assertIsNone(config['MASISCAM_POLITICA_COMERCIAL_LOCAL']);self.assertEqual(config['MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL'],[])

    def test_por_defecto_deshabilitado_compatibilidad_local(self):
        self.assertFalse(self.config()['MASISCAM_RECEPCION_LOCAL'])
        self.assertTrue(self.config({'MASISCAM_RECEPCION_LOCAL':'true'},debug=True)['MASISCAM_RECEPCION_LOCAL'])
        self.assertFalse(self.config({'MASISCAM_RECEPCION_LOCAL':'true'})['MASISCAM_RECEPCION_LOCAL'])

    def test_fase_tecnica_puede_habilitarse_sin_activar_reparaciones(self):
        config=self.config({'MASISCAM_TECNICA_ENABLED':'true'})
        self.assertTrue(config['MASISCAM_TECNICA_LOCAL'])
        for phase in ('RECEPCION','OPERACION','CALIBRACION','CIERRE'):
            self.assertFalse(config[f'MASISCAM_{phase}_LOCAL'])

    def test_fuentes_explicitas_y_configuracion_invalida_falla_cerrada(self):
        from .test_operacion import QA_POLICY
        with tempfile.TemporaryDirectory(prefix='masiscam-config-qa-') as temp:
            policy=Path(temp)/'politica-qa.json';protocol=Path(temp)/'protocolo-qa.json'
            policy.write_text(json.dumps(QA_POLICY),encoding='utf-8')
            protocol.write_text(json.dumps([{'huella':'a'*64,'aprobacion_propietario':'FICTICIO QA SIN VALIDEZ'}]),encoding='utf-8')
            env={'MASISCAM_POLITICA_COMERCIAL_FILE':str(policy),'MASISCAM_PROTOCOLOS_CALIBRACION_FILE':str(protocol)}
            self.assertEqual(self.config(env)['MASISCAM_POLITICA_COMERCIAL_LOCAL'],QA_POLICY)
            for invalid in ('[{"huella":"sin-huella","aprobacion_propietario":""}]','[{"huella":"a","huella":"b"}]','{}'):
                protocol.write_text(invalid,encoding='utf-8')
                with self.assertRaises(ImproperlyConfigured): self.config(env)
            protocol.write_text('[]',encoding='utf-8');policy.write_text('{}',encoding='utf-8')
            with self.assertRaises(ImproperlyConfigured): self.config(env)

    def test_settings_reales_en_modo_produccion_sin_conexion_externa(self):
        values=dict(DEBUG='false',SECRET_KEY=secrets.token_urlsafe(64),DATABASE_URL='postgres://qa:qa@127.0.0.1:1/masiscam_qa_sin_conexion',
            ALLOWED_HOSTS='qa.invalid',MASISCAM_PUBLIC_BASE_URL='https://qa.invalid',MASISCAM_EXPEDIENTES_ENABLED='true',
            SECURE_HSTS_SECONDS='31536000',SECURE_HSTS_INCLUDE_SUBDOMAINS='true',SECURE_HSTS_PRELOAD='true',GOOGLE_DRIVE_ENABLED='false')
        with patch.dict(os.environ,values,clear=True),patch('environ.Env.read_env'):
            config=runpy.run_module('config.settings',run_name='__masiscam_qa_settings__')
        self.assertFalse(config['DEBUG']);self.assertTrue(config['MASISCAM_CIERRE_LOCAL'])
        keys=('DEBUG','SECRET_KEY','ALLOWED_HOSTS','SECURE_SSL_REDIRECT','SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE',
            'SECURE_HSTS_SECONDS','SECURE_HSTS_INCLUDE_SUBDOMAINS','SECURE_HSTS_PRELOAD','X_FRAME_OPTIONS')
        with override_settings(**{k:config[k] for k in keys}):
            self.assertEqual(run_checks(tags=[Tags.security],include_deployment_checks=True),[])

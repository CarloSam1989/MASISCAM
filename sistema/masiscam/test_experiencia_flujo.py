"""Operational navigation and reception against synthetic, isolated records."""
import uuid
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import test_taller, test_tecnica as qa, flujo_services as flujo
from .models import AsignacionTecnica, EmisionGuiaIngreso, ConfirmacionRecepcion, Documento
from .recepcion_services import abrir_recepcion, reservar_guia, generar_guia
from .recepcion_confirmacion import confirmar_recepcion
from .test_utils import consumir_respuesta


@override_settings(GOOGLE_DRIVE_ENABLED=False, MASISCAM_RECEPCION_LOCAL=True,
    MASISCAM_TECNICA_LOCAL=True, MASISCAM_OPERACION_LOCAL=True,
    MASISCAM_CALIBRACION_LOCAL=True, MASISCAM_CIERRE_LOCAL=True)
class ExperienciaTests(TestCase):
    setUp=test_taller.TallerTests.setUp
    datos=test_taller.TallerTests.datos
    crear=test_taller.TallerTests.crear
    recepcion=test_taller.TallerTests.recepcion

    def ingreso(self, fotos=True, confirmar=False):
        self.equipo=self.crear('UX')
        self.exp=abrir_recepcion(usuario=self.taller,empresa=self.empresa,equipo_id=self.equipo.pk,
            cliente_id=self.cliente.pk,clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),
            condicion_ingreso='Evidencia ficticia QA',fotografias=[test_taller.foto()] if fotos else [])
        if confirmar:
            self.confirmacion=confirmar_recepcion(usuario=self.taller,empresa=self.empresa,
                expediente_id=self.exp.pk,clave=uuid.uuid4(),placa='SIN_PLACA',motivo_sin_placa='QA',
                motivo_sin_guia='QA',general_id=self.exp.recepcion.evidencias.get().pk)
        self.url=reverse('masiscam:expediente_detalle',args=[self.exp.pk])

    def test_recepcion_sin_fotos_requisito_carga_directa_y_validacion(self):
        self.ingreso(fotos=False)
        response=self.client.get(self.url)
        self.assertContains(response,'Para confirmar la recepción, adjunta una fotografía general del equipo')
        self.assertContains(response,'href="#id_foto_general"')
        self.assertContains(response,'name="foto_general"')
        response=self.client.post(reverse('masiscam:recepcion_finalizar',args=[self.exp.pk]),
            dict(clave=uuid.uuid4(),placa='SIN_PLACA',motivo_sin_placa='QA',motivo_sin_guia='QA'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=self.exp.recepcion).exists())
        response=self.client.post(reverse('masiscam:recepcion_finalizar',args=[self.exp.pk]),
            dict(clave=uuid.uuid4(),placa='CON_PLACA',foto_general=test_taller.foto(),motivo_sin_guia='QA'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.exp.guias.exists())
        response=self.client.post(reverse('masiscam:recepcion_finalizar',args=[self.exp.pk]),
            dict(clave=uuid.uuid4(),placa='SIN_PLACA',motivo_sin_placa='Ausencia verificada QA',
                motivo_sin_guia='Sin guía QA',foto_general=test_taller.foto()))
        self.assertEqual(response.status_code,302)
        self.assertTrue(ConfirmacionRecepcion.objects.filter(recepcion=self.exp.recepcion).exists())

    @override_settings(MASISCAM_OPERACION_LOCAL=False)
    def test_fase_comercial_no_bloquea_inspeccion_piezas_ni_envio_solicitud(self):
        self.ingreso(confirmar=True)
        ficha=reverse('masiscam:taller_equipo',args=[self.equipo.pk])
        self.assertContains(self.client.get(ficha),'Iniciar inspección')
        qa.inspeccion_enviada(self.empresa,self.taller,self.exp)
        self.assertContains(self.client.get(ficha),'Seleccionar piezas')
        d=qa.diagnostico_enviado(self.empresa,self.taller,self.exp)
        sol=flujo.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=d.pk,
            version=d.version_edicion,clave=uuid.uuid4())
        self.assertContains(self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])),
            'Solicitud registrada.')
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            flujo.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=sol.pk,
                clave=uuid.uuid4(),referencia='No autorizado')
        self.assertEqual(self.client.post(reverse('masiscam:operacion_operar',args=[self.exp.pk,'actividad']),
            dict(clave=uuid.uuid4())).status_code,404)

    @override_settings(MASISCAM_TECNICA_LOCAL=False, MASISCAM_OPERACION_LOCAL=False)
    def test_recepcion_incompleta_sigue_accesible_sin_modulo_tecnico(self):
        self.ingreso(fotos=False)
        response=self.client.get(reverse('masiscam:taller_equipo',args=[self.equipo.pk]))
        self.assertContains(response,'Completar recepción')
        self.assertContains(response,self.url+'#confirmar-recepcion')

    def test_asignacion_pendiente_no_oculta_recepcion_ni_permite_trabajo(self):
        self.ingreso(confirmar=True)
        AsignacionTecnica.objects.filter(expediente=self.exp).update(fin=timezone.now())
        self.assertContains(self.client.get(self.url),'PLANTA debe asignar el responsable')
        self.assertNotContains(self.client.get(self.url),'Continuar al mantenimiento')
        self.assertEqual(self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).status_code,403)

    def test_versiones_y_drive_error_no_impiden_descarga_local_ni_exponen_detalles(self):
        self.ingreso()
        primera=reservar_guia(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4())
        generar_guia(primera.pk)
        self.confirmacion=confirmar_recepcion(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
            clave=uuid.uuid4(),placa='SIN_PLACA',motivo_sin_placa='QA',motivo_sin_guia='QA',
            general_id=self.exp.recepcion.evidencias.get().pk)
        guia=self.confirmacion.guia
        Documento.objects.filter(pk=guia.documento_id).update(estado_sincronizacion='ERROR',error_sincronizacion='secreto técnico QA')
        before=list(self.exp.guias.values_list('pk','version','hash_sha256'))
        for _ in range(2):
            response=self.client.get(self.url)
            self.assertContains(response,'Documento guardado. Sincronización pendiente')
            self.assertContains(response,'Continuar al mantenimiento')
            for texto in ('SHA-256','Reintentar generación','Solicitar nueva versión','secreto técnico QA','Etapas técnicas deshabilitadas'):
                self.assertNotContains(response,texto)
        for g in self.exp.guias.all():
            response=self.client.get(reverse('masiscam:recepcion_documento',args=[self.exp.pk,g.documento_id]))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Content-Type'],'application/pdf')
            self.assertTrue(consumir_respuesta(response).startswith(b'%PDF'))
        self.assertEqual(before,list(self.exp.guias.values_list('pk','version','hash_sha256')))
        for name,args in [('guia_emitir',[self.exp.pk]),('guia_reintentar',[self.exp.pk,guia.pk])]:
            self.assertEqual(self.client.post(reverse('masiscam:'+name,args=args),dict(clave=uuid.uuid4())).status_code,403)
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(self.url),'Estado técnico del documento')

    def test_breadcrumbs_por_rol_enlace_previo_siempre_autorizado(self):
        from .templatetags.masiscam_navigation import breadcrumbs
        self.ingreso(confirmar=True)
        for role in ('TALLER','OFICINA','PLANTA','GERENCIA'):
            self.client.force_login(self.usuarios[role])
            for name,args in [('dashboard',[]),('expedientes',[]),('expediente_detalle',[self.exp.pk]),
                    ('flujo_detalle',[self.exp.pk]),('biblioteca',[]),('accounts:seleccionar_empresa',[])]:
                url=reverse(name if ':' in name else 'masiscam:'+name,args=args)
                response=self.client.get(url,follow=True)
                with self.subTest(role=role,url=url):
                    self.assertEqual(response.status_code,200)
                    self.assertContains(response,'aria-label="Migas de pan"')
                    ctx={'request':response.wsgi_request,'expediente':response.context.get('expediente'),
                         'equipo':response.context.get('equipo')}
                    ctx['request']=response.wsgi_request
                    for item in breadcrumbs(ctx):
                        if item['url']:
                            self.assertEqual(self.client.get(item['url'],follow=True).status_code,200,item)
                            if role=='TALLER':
                                self.assertNotIn('/clientes/',item['url'])
                                self.assertNotIn('/proyectos/',item['url'])

    def test_documentos_y_formularios_ajenos_denegados(self):
        self.ingreso(confirmar=True)
        self.client.force_login(self.otro)
        for url in (self.url,reverse('masiscam:flujo_detalle',args=[self.exp.pk]),
                reverse('masiscam:recepcion_documento',args=[self.exp.pk,self.confirmacion.guia.documento_id])):
            self.assertIn(self.client.get(url).status_code,(403,404))
        self.assertEqual(self.client.post(reverse('masiscam:recepcion_finalizar',args=[self.exp.pk]),{}).status_code,404)

    def test_diagnostico_local_drive_no_usa_red_ni_expone_secretos(self):
        output=StringIO()
        with patch('masiscam.services.GoogleDriveService') as drive:
            call_command('verificar_drive',local=True,stdout=output)
            drive.assert_not_called()
        self.assertIn('TECNICA: True',output.getvalue())
        self.assertNotIn('private_key',output.getvalue())

    def test_administracion_conserva_breadcrumbs_y_taller_no_accede(self):
        self.client.force_login(self.root)
        self.assertContains(self.client.get(reverse('admin:index')),'aria-label="Migas de pan"')
        self.client.force_login(self.taller)
        response=self.client.get(reverse('admin:index'))
        self.assertIn(response.status_code,(302,403))

    @override_settings(GOOGLE_DRIVE_ENABLED=True, GOOGLE_DRIVE_CREDENTIALS_FILE='',GOOGLE_DRIVE_ROOT_FOLDER_ID='')
    def test_drive_falla_local_pdf_inmutable_y_log_sanitizado(self):
        self.ingreso(confirmar=True)
        doc=self.confirmacion.guia.documento
        before=doc.hash_sha256
        from .tasks import sincronizar_documento, DriveSyncError
        with self.assertLogs('masiscam.tasks',level='WARNING') as logs:
            with self.assertRaises(DriveSyncError): sincronizar_documento(doc.pk)
        self.assertIn('CONFIGURACION_INCOMPLETA',' '.join(logs.output))
        doc.refresh_from_db()
        self.assertEqual(doc.estado_sincronizacion,'ERROR')
        self.assertEqual(doc.hash_sha256,before)
        self.assertContains(self.client.get(self.url),'Documento guardado. Sincronización pendiente')

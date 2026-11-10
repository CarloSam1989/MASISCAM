"""Operational navigation and reception against synthetic, isolated records."""
import uuid
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import test_taller, test_tecnica as qa, flujo_services as flujo, tecnica_services as tecnica
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
        self.ingreso(fotos=False)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=self.exp.recepcion).exists())
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

    def test_inicio_mantenimiento_redirige_a_inspeccion_sin_fotografias_ni_confirmacion(self):
        equipo=self.crear('INICIO-SIMPLE')
        detalle_url=reverse('masiscam:taller_equipo',args=[equipo.pk])
        self.assertEqual(self.client.get(detalle_url).status_code,200)
        self.assertEqual(self.client.post(detalle_url,{}).status_code,405)
        inicio=self.client.get(reverse('masiscam:taller_inicio'))
        self.assertContains(inicio,f'href="{detalle_url}"')
        response=self.client.post(reverse('masiscam:recepcion_crear'),dict(
            clave=uuid.uuid4(),equipo=equipo.pk,cliente=self.cliente.pk,tipo='MANTENIMIENTO',
            fecha=timezone.localtime().strftime('%Y-%m-%dT%H:%M'),condicion_ingreso='Pendiente de inspección QA'))
        from .models import ExpedienteMantenimiento
        exp=ExpedienteMantenimiento.objects.get(registro__equipo=equipo)
        self.assertRedirects(response,detalle_url+f'?expediente={exp.pk}')
        self.assertEqual(self.client.get(response.url).status_code,200)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=exp.recepcion).exists())
        self.assertContains(self.client.get(response.url),'Iniciar inspección')

    def test_iniciar_inspeccion_crea_borradores_y_bloquea_envio_prematuro(self):
        self.ingreso(fotos=False)
        url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        clave=uuid.uuid4()
        response=self.client.post(url,dict(op='inspeccion',clave=clave))
        self.assertRedirects(response,url)
        etapa_inspeccion=self.exp.etapas_tecnicas.get(codigo='INSPECCION')
        etapa_diagnostico=self.exp.etapas_tecnicas.get(codigo='DIAGNOSTICO')
        inspeccion=etapa_inspeccion.inspeccion
        diagnostico=etapa_diagnostico.diagnostico
        self.assertIsNotNone(inspeccion)
        self.assertIsNotNone(diagnostico)
        self.assertEqual(diagnostico.inspeccion_id,inspeccion.pk)
        self.assertFalse(inspeccion.enviado_en)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=self.exp.recepcion).exists())
        self.assertRedirects(self.client.post(url,dict(op='inspeccion',clave=clave)),url)
        self.assertEqual(self.exp.etapas_tecnicas.get(codigo='INSPECCION').inspeccion_id,inspeccion.pk)
        self.assertEqual(self.exp.etapas_tecnicas.get(codigo='DIAGNOSTICO').diagnostico_id,diagnostico.pk)
        self.client.force_login(self.usuarios['PLANTA'])
        planta=self.client.get(url)
        self.assertContains(planta,'Gestionar planos técnicos')
        self.assertContains(planta,reverse('masiscam:biblioteca'))
        diagnostico=tecnica.guardar_revision(usuario=self.taller,empresa=self.empresa,tipo='DIAGNOSTICO',
            revision_id=diagnostico.pk,version=diagnostico.version_edicion,clave=uuid.uuid4(),datos=dict(
                fecha=timezone.localdate(),fallas='Desgaste QA',causas='Pendiente QA',
                componentes_afectados='Componente QA',recomendaciones='Cotizar pieza QA',
                conclusiones='Revisión independiente pendiente',observaciones='Solicitud QA',
                motivo_sin_evidencia='Evidencia QA asociada a pieza',repuestos='SIN_REPUESTOS'))
        with self.assertRaisesMessage(ValidationError,'Complete y envíe los detalles técnicos de inspección'):
            tecnica.enviar_revision(usuario=self.taller,empresa=self.empresa,tipo='DIAGNOSTICO',
                revision_id=diagnostico.pk,version=diagnostico.version_edicion,clave=uuid.uuid4())

    @override_settings(MASISCAM_TECNICA_LOCAL=False, MASISCAM_OPERACION_LOCAL=False)
    def test_recepcion_incompleta_sigue_accesible_sin_modulo_tecnico(self):
        self.ingreso(fotos=False)
        response=self.client.get(reverse('masiscam:taller_equipo',args=[self.equipo.pk]))
        self.assertContains(response,'MASISCAM_TECNICA_ENABLED')
        self.assertNotContains(response,'#confirmar-recepcion')

    @override_settings(MASISCAM_RECEPCION_LOCAL=False, MASISCAM_OPERACION_LOCAL=False)
    def test_taller_inspecciona_selecciona_en_plano_y_envia_a_oficina_sin_confirmar(self):
        from . import plano_services as planos, tecnica_services as tecnica, operacion_services as operacion
        self.ingreso(fotos=False)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=self.exp.recepcion).exists())
        self.assertContains(self.client.get(reverse('masiscam:taller_equipo',args=[self.equipo.pk])),'Iniciar inspección')
        qa.inspeccion_enviada(self.empresa,self.taller,self.exp)
        self.planta=self.usuarios['PLANTA']
        self.diag=tecnica.crear_revision(usuario=self.taller,empresa=self.empresa,
            expediente_id=self.exp.pk,tipo='DIAGNOSTICO',clave=uuid.uuid4())
        flujo_url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        faltantes=self.client.get(flujo_url)
        self.assertContains(faltantes,'PLANTA debe publicar un plano y asignar su aplicabilidad al equipo')
        self.assertContains(faltantes,reverse('masiscam:biblioteca'))

        referencia,parte=qa.TecnicaTests.referencia(self)
        mapa=planos.guardar_mapa(usuario=self.planta,empresa=self.empresa,referencia_id=referencia.pk,
            clave=uuid.uuid4(),base_version=0,
            posiciones=[dict(parte=parte.pk,pagina=1,x='.5',y='.4')])
        plano_url=reverse('masiscam:plano',args=[referencia.pk])
        self.assertEqual(self.client.get(f'{plano_url}?diagnostico={self.diag.pk}&flujo=1').status_code,200)
        datos=self.client.get(reverse('masiscam:plano_datos',args=[referencia.pk]),
            {'diagnostico':self.diag.pk}).json()
        self.assertTrue(datos['seleccionable'])
        self.assertEqual(datos['puntos'][0]['id'],mapa.posiciones.get().pk)
        seleccion=self.client.post(reverse('masiscam:plano_pieza',args=[self.diag.pk]),dict(
            version=self.diag.version_edicion,parte_id=parte.pk,posicion_plano_id=mapa.posiciones.get().pk,
            cantidad='1',unidad='unidad',condicion='Desgaste observado QA',accion='EVALUAR',
            observaciones='Verificar con OFICINA',clave=uuid.uuid4()))
        self.assertEqual(seleccion.status_code,200,seleccion.content)
        self.assertEqual(self.diag.piezas.count(),1)

        self.diag.refresh_from_db()
        solicitud=flujo.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4(),datos=dict(
                fecha=timezone.localdate(),fallas='Desgaste por verificar QA',causas='Pendiente de análisis QA',
                componentes_afectados='Componente QA',recomendaciones='Cotizar la pieza seleccionada',
                conclusiones='Revisión independiente pendiente',observaciones='Solicitud sintética QA',
                motivo_sin_evidencia='Sin fotografías adicionales QA',repuestos='REQUERIDOS'))
        self.assertEqual(solicitud.diagnostico_id,self.diag.pk)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=self.exp.recepcion).exists())
        self.assertFalse(tecnica._aprobado(self.diag))
        self.client.force_login(self.oficina)
        oficina=self.client.get(flujo_url)
        self.assertContains(oficina,'Componente QA')
        with override_settings(MASISCAM_OPERACION_LOCAL=True):
            with self.assertRaises(ValidationError):
                operacion.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
                    clave=uuid.uuid4(),datos=dict(trabajo='Reparación QA',componentes='Componente QA',
                        inicio=timezone.now(),observaciones='Sin autorización comercial'))

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
                if role=='OFICINA' and name=='biblioteca':
                    self.assertEqual(self.client.get(url).status_code,403)
                    url=reverse('masiscam:oficina_seccion',args=['documentos'])
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

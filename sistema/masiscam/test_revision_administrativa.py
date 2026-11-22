"""Revisión conjunta sobre los fixtures existentes, sin servicios externos."""
import uuid
from django.test import TestCase, override_settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from . import test_oficina_gestion as qa
from . import flujo_services as s, operacion_services as o, tecnica_services as t
from .models import RevisionAdministrativa, PropuestaComercial, DecisionTecnica, Diagnostico, Auditoria


@override_settings(**qa.FLAGS)
class RevisionAdministrativaTests(qa.OficinaFixture,TestCase):
    def preparar(self):super().preparar(aprobar_tecnico=False)

    test_cotizacion_sin_fase4_respeta_empresa = override_settings(
        DEBUG=False, MASISCAM_OPERACION_LOCAL=False)(qa.OficinaGestionTests.test_multiempresa)

    @override_settings(DEBUG=False, MASISCAM_OPERACION_LOCAL=False,
        MASISCAM_CALIBRACION_LOCAL=False, MASISCAM_CIERRE_LOCAL=False)
    def test_oficina_post_cotizacion_redireccion_y_revision_admin(self):
        self.preparar_preliminar()
        self._verificar_post_cotizacion_oficina()

    @override_settings(DEBUG=False, MASISCAM_OPERACION_LOCAL=False,
        MASISCAM_CALIBRACION_LOCAL=False, MASISCAM_CIERRE_LOCAL=False)
    def test_oficina_post_cotizacion_con_diagnostico_aprobado(self):
        qa.OficinaFixture.preparar(self)
        self._verificar_post_cotizacion_oficina()

    def _verificar_post_cotizacion_oficina(self):
        payload = dict(op='cotizar', clave=uuid.uuid4(), referencia='COT-POST-QA',
            **{f'precio_{self.pieza.pk}': '12.35'})
        response = self.client.post(self.url, payload, follow=True)
        self.assertEqual(response.redirect_chain, [(reverse('masiscam:oficina_inicio'), 302)])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cotización enviada correctamente. Pendiente de aprobación del ADMINISTRADOR')
        self.assertContains(response, 'Pendiente de aprobación')
        duplicate=self.client.post(self.url,payload,follow=True)
        self.assertEqual(duplicate.status_code,200)
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)
        prop = PropuestaComercial.objects.get(expediente=self.exp)
        self.assertEqual(prop.autor_id, self.oficina.pk)
        self.assertTrue(prop.snapshot['revision_conjunta_requerida'])
        self.assertEqual(prop.evidencias.count(), 1)
        self.assertFalse(RevisionAdministrativa.objects.filter(expediente=self.exp).exists())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), 'Cotización de OFICINA')
        # El envío no concede acceso a TALLER ni atribuciones de aprobación.
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(reverse('masiscam:taller_inicio')).status_code, 403)
        self.assertEqual(self.client.post(self.url, dict(op='aprobar_expediente', clave=uuid.uuid4(),
            diagnostico_id=self.diag.pk, propuesta_id=prop.pk)).status_code, 403)
        # Cotizar no habilita las operaciones de la fase desactivada.
        if not o.settings.MASISCAM_OPERACION_LOCAL:
            with self.assertRaisesMessage(PermissionDenied, 'Fase 4 deshabilitada'):
                o.autorizar(self.oficina, self.empresa, {'OFICINA'})
            with self.assertRaisesMessage(PermissionDenied, 'Fase 4 deshabilitada'):
                s.adquirir(usuario=self.oficina, empresa=self.empresa, solicitud_id=self.sol.pk,
                    pieza_id=self.pieza.pk, cantidad='1', proveedor='QA', referencia='QA',
                    fecha=timezone.now(), clave=uuid.uuid4())
            with self.assertRaisesMessage(PermissionDenied, 'Fase 4 deshabilitada'):
                o.iniciar_actividad(usuario=self.taller, empresa=self.empresa, expediente_id=self.exp.pk,
                    clave=uuid.uuid4(), datos={})

    def preparar_preliminar(self):
        self.preparar_solicitud(enviar=False)
        self.pieza=self.diag.piezas.get()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4(),preliminar=True)
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.oficina)

    def test_cotizar_preliminar_formalizar_revisar_aceptar_y_recibir(self):
        self.preparar_preliminar()
        response=self.client.get(self.url)
        self.assertTrue(response.context['cotizacion_habilitada'])
        self.assertContains(response,'data-diagnostico-pendiente')
        self.assertContains(response,'Guardar cotización y enviar a ADMINISTRADOR')
        self.assertNotContains(response,'Aprobar y enviar a TALLER')
        self.assertNotContains(response,'id="oficina-enviar"')
        key=uuid.uuid4()
        prop=self.cotizar(clave=key,archivo=None)
        self.assertEqual(self.cotizar(clave=key,archivo=None).pk,prop.pk)
        self.assertEqual(prop.evidencias.count(),1)
        import os
        from pathlib import Path
        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
            with prop.evidencias.get().documento.archivo.open('rb') as archivo:
                (Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'cotizacion-preliminar-qa.pdf').write_bytes(archivo.read())
        self.assertTrue(prop.snapshot['revision_conjunta_requerida'])
        with self.assertRaises(ValidationError):self.revisar(prop)
        with self.assertRaises(PermissionDenied):self.revisar(prop,usuario=self.oficina)
        with self.assertRaises(ValidationError):self.aceptar(prop)
        with self.assertRaises(ValidationError):self.envio()
        self.diag=t.enviar_revision(usuario=self.taller,empresa=self.empresa,tipo='DIAGNOSTICO',
            revision_id=self.diag.pk,version=self.diag.version_edicion,clave=uuid.uuid4())
        prop.refresh_from_db()
        self.revisar(prop)
        self.assertTrue(s.aprobacion_conjunta_vigente(prop))
        self.aceptar(prop);_,envio=self.envio()
        with self.assertRaises(ValidationError):
            o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
                clave=uuid.uuid4(),datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.recibir(envio)
        actividad=o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
            clave=uuid.uuid4(),datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.assertEqual(actividad.autorizacion.propuesta_id,prop.pk)

    @override_settings(DEBUG=False)
    def test_flujo_validar_antes_de_enviar_a_oficina(self):
        self.preparar_solicitud(enviar=False)
        self.pieza=self.diag.piezas.get()
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.taller)
        page=self.client.get(self.url)
        self.assertContains(page,'Validar diagn\u00f3stico con ADMINISTRADOR')
        self.assertContains(page,'id="informacion-tecnica"')
        payload=dict(op='enviar_piezas',clave=uuid.uuid4(),version=self.diag.version_edicion)
        self.assertEqual(self.client.post(self.url,payload).status_code,400)
        self.assertFalse(self.exp.solicitudrepuestos_set.exists())
        response=self.client.post(reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',self.diag.pk])+'?flujo=1',
            dict(op='enviar',clave=uuid.uuid4(),version=self.diag.version_edicion))
        self.assertEqual(response.status_code,302)
        self.diag.refresh_from_db()
        self.assertTrue(self.diag.enviado_en)
        self.assertEqual(self.diag.estado,'PENDIENTE_APROBACION')
        self.assertEqual(self.client.post(self.url,dict(op='enviar_piezas',clave=uuid.uuid4(),
            version=self.diag.version_edicion)).status_code,302)
        self.sol=self.exp.solicitudrepuestos_set.get()
        self.assertEqual(self.sol.diagnostico_id,self.diag.pk)
        self.client.force_login(self.oficina)
        prop=self.cotizar(archivo=None)
        self.assertEqual(self.exp.solicitudrepuestos_set.count(),1)
        self.assertEqual(prop.evidencias.count(),1)
        self.revisar(prop)
        self.assertTrue(s.aprobacion_conjunta_vigente(prop))
        with self.assertRaises(ValidationError): self.envio()
        self.aceptar(prop)
        _,envio=self.envio(); self.recibir(envio)
        actividad=o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
            clave=uuid.uuid4(),datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.assertEqual(actividad.autorizacion.propuesta_id,prop.pk)
        self.assertEqual(RevisionAdministrativa.objects.filter(expediente=self.exp,decision='APROBADO').count(),1)
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='REVISION_ENVIADA').exists())
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='EXPEDIENTE_REVISADO').exists())

    def test_cotizacion_borrador_exige_solicitud_congelada_en_servicio_y_bd(self):
        self.preparar_solicitud(enviar=False)
        with self.assertRaises(ValidationError):
            o.proponer(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,
                diagnostico_id=self.diag.pk,clave=uuid.uuid4(),datos=dict(alcance='QA',cotizacion={}))
        with transaction.atomic(),self.assertRaises(IntegrityError):
            PropuestaComercial.objects.create(expediente=self.exp,autor=self.oficina,diagnostico=self.diag,
                clave=uuid.uuid4(),version=1,alcance='QA',snapshot=dict(revision_conjunta_requerida=True,cotizacion=dict(moneda='USD')))

    def test_devolucion_preliminar_conserva_borrador_y_versiona_cotizacion(self):
        self.preparar_preliminar();prop=self.cotizar(archivo=None)
        fotos=set(self.pieza.fotografias.values_list('documento_id',flat=True))
        self.revisar(prop,decision='DEVUELTO',destino='AMBOS',motivo='Completar diagnóstico y precios')
        self.assertEqual(Diagnostico.objects.filter(expediente=self.exp).count(),1)
        nueva=self.cotizar(cotizacion=self.detalle('14.00'),archivo=None)
        self.assertEqual(nueva.version,2)
        self.assertEqual(nueva.sustituye_id,prop.pk)
        self.assertEqual(set(self.pieza.fotografias.values_list('documento_id',flat=True)),fotos)
        self.assertEqual(nueva.evidencias.count(),1)
        self.assertEqual(prop.evidencias.count(),1)

    def revisar(self,propuesta,**changes):
        data=dict(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,
            diagnostico_id=propuesta.diagnostico_id,propuesta_id=propuesta.pk,decision='APROBADO',clave=uuid.uuid4())
        data.update(changes)
        return s.revisar_expediente(**data)

    def test_recorrido_sin_aprobacion_tecnica_previa_ni_autorizacion_implicita(self):
        self.preparar();prop=self.cotizar(archivo=None)
        self.assertIsNone(prop.informe_id)
        self.assertFalse(DecisionTecnica.objects.exists())
        with self.assertRaises(ValidationError):self.envio()
        revision=self.revisar(prop)
        self.assertTrue(s.aprobacion_conjunta_vigente(prop))
        self.assertEqual(revision.autor,self.admin)
        self.assertEqual(revision.snapshot['revision'],self.diag.revision)
        self.assertEqual(revision.snapshot['version'],prop.version)
        self.assertEqual(revision.informe.estado,'GENERADA')
        self.assertTrue(DecisionTecnica.objects.filter(diagnostico=self.diag,decision='APROBADO').exists())
        with self.assertRaises(ValidationError):self.envio()
        self.aceptar(prop);compra,envio=self.envio()
        with self.assertRaises(ValidationError):
            o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
                datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.recibir(envio)
        actividad=o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.assertEqual(actividad.autorizacion.propuesta_id,prop.pk)

    @override_settings(DEBUG=False)
    def test_requisitos_visibles_y_aprobacion_sin_aceptacion_cliente(self):
        self.preparar_preliminar()
        prop=self.cotizar(archivo=None)
        self.client.force_login(self.admin)
        page=self.client.get(self.url)
        self.assertContains(page, 'TALLER debe completar el diagnóstico vigente y pulsar «Validar diagnóstico para ADMINISTRADOR» para enviarlo a revisión.')
        self.assertTrue(page.context['revision_faltantes'])
        denied=self.client.post(self.url,dict(op='aprobar_expediente',clave=uuid.uuid4(),
            diagnostico_id=self.diag.pk,propuesta_id=prop.pk))
        self.assertEqual(denied.status_code,400)
        self.assertContains(denied, 'TALLER debe completar el diagnóstico vigente y pulsar «Validar diagnóstico para ADMINISTRADOR» para enviarlo a revisión.',status_code=400)
        self.assertContains(denied, 'data-masiscam-message="error"',status_code=400)
        self.assertNotContains(denied, 'TALLER debe completar y enviar el diagnóstico para revisión.',status_code=400)
        self.assertFalse(RevisionAdministrativa.objects.filter(expediente=self.exp).exists())
        self.assertFalse(Auditoria.objects.filter(expediente=self.exp,accion='EXPEDIENTE_REVISADO').exists())
        self.client.force_login(self.taller)
        sent=self.client.post(reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',self.diag.pk])+'?flujo=1',
            dict(op='enviar',clave=uuid.uuid4(),version=self.diag.version_edicion))
        self.assertEqual(sent.status_code,302)
        self.diag.refresh_from_db()
        self.assertTrue(self.diag.enviado_en)
        self.assertEqual(self.diag.estado,'PENDIENTE_APROBACION')
        self.assertTrue(t.vigente(self.diag))
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='REVISION_ENVIADA').exists())
        self.client.force_login(self.admin)
        page=self.client.get(self.url)
        self.assertEqual(page.context['revision_faltantes'], [])
        response=self.client.post(self.url,dict(op='aprobar_expediente',clave=uuid.uuid4(),
            diagnostico_id=self.diag.pk,propuesta_id=prop.pk))
        self.assertEqual(response.status_code,302)
        prop.refresh_from_db()
        revision=RevisionAdministrativa.objects.get(expediente=self.exp)
        self.assertEqual((revision.decision, revision.diagnostico_id, revision.propuesta_id),
            ('APROBADO', self.diag.pk, prop.pk))
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='EXPEDIENTE_REVISADO').exists())
        self.assertTrue(s.aprobacion_conjunta_vigente(prop))
        self.assertFalse(hasattr(prop,'decision'))
        self.revisar(prop,decision='DEVUELTO',destino='OFICINA',motivo='Corregir condiciones')
        self.assertFalse(s.aprobacion_conjunta_vigente(prop))

    def test_faltantes_versiones_desactualizadas_y_permisos(self):
        self.preparar()
        with self.assertRaises(ValidationError):
            s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,
                diagnostico_id=self.diag.pk,propuesta_id=0,decision='APROBADO',clave=uuid.uuid4())
        sin_pdf=self.cotizar(archivo=None,cotizacion=None)
        with self.assertRaises(ValidationError):self.revisar(sin_pdf)
        self.assertFalse(DecisionTecnica.objects.exists())
        prop=self.cotizar(archivo=None)
        for user in (self.taller,self.oficina,self.otro):
            with self.assertRaises(PermissionDenied):self.revisar(prop,usuario=user)
        newer=self.cotizar(archivo=None,cotizacion=self.detalle('13.00'))
        with self.assertRaises(ValidationError):self.revisar(prop)
        self.assertFalse(RevisionAdministrativa.objects.exists())
        self.revisar(newer)

    def test_cambio_cotizacion_invalida_sin_sobrescribir_revision_pdf_o_cliente(self):
        self.preparar();prop=self.cotizar(archivo=None);revision=self.revisar(prop)
        auth=self.aceptar(prop)
        self.assertTrue(o.autorizacion_valida(auth))
        old_hash=prop.evidencias.get().documento.hash_sha256
        nueva=self.cotizar(archivo=None,cotizacion=self.detalle('14.00'))
        self.assertFalse(s.aprobacion_conjunta_vigente(prop))
        self.assertFalse(s.aprobacion_conjunta_vigente(nueva))
        self.assertFalse(o.autorizacion_valida(auth))
        revision.refresh_from_db();self.assertEqual(revision.propuesta_id,prop.pk)
        self.assertEqual(prop.evidencias.get().documento.hash_sha256,old_hash)
        self.revisar(nueva)
        self.assertFalse(o.autorizacion_valida(auth))
        t.crear_revision(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,tipo='DIAGNOSTICO',
            motivo='Nueva información técnica QA',clave=uuid.uuid4())
        self.assertFalse(s.aprobacion_conjunta_vigente(nueva))

    def test_devolucion_taller_copia_revision_reutiliza_fotos_y_notifica(self):
        self.preparar();prop=self.cotizar(archivo=None);self.revisar(prop)
        with self.assertRaises(ValidationError):self.revisar(prop,decision='DEVUELTO',motivo='  ')
        key=uuid.uuid4()
        devolucion=self.revisar(prop,decision='DEVUELTO',motivo='Corregir daño registrado',destino='TALLER',clave=key)
        self.assertEqual(self.revisar(prop,decision='DEVUELTO',motivo='Corregir daño registrado',destino='TALLER',clave=key).pk,devolucion.pk)
        self.assertFalse(s.aprobacion_conjunta_vigente(prop))
        nueva=Diagnostico.objects.filter(expediente=self.exp).latest('revision')
        self.assertEqual(nueva.sustituye_id,self.diag.pk)
        self.assertFalse(nueva.enviado_en)
        self.assertEqual(set(nueva.piezas.get().fotografias.values_list('documento_id',flat=True)),
            set(self.pieza.fotografias.values_list('documento_id',flat=True)))
        self.client.force_login(self.taller)
        self.assertContains(self.client.get(self.url),'data-devolucion-expediente')
        self.client.force_login(self.oficina)
        self.assertNotContains(self.client.get(self.url),'data-devolucion-expediente')

    @override_settings(DEBUG=False)
    def test_devolucion_y_reenvio_diagnostico_vigente(self):
        self.preparar(); prop=self.cotizar(archivo=None); self.revisar(prop)
        self.revisar(prop,decision='DEVUELTO',destino='TALLER',motivo='Corregir diagn\u00f3stico')
        self.diag=Diagnostico.objects.filter(expediente=self.exp).latest('revision')
        self.pieza=self.diag.piezas.get()
        self.client.force_login(self.taller)
        page=self.client.get(self.url)
        self.assertContains(page,'Validar diagn\u00f3stico con ADMINISTRADOR')
        self.assertEqual(self.client.post(reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',self.diag.pk])+'?flujo=1',
            dict(op='enviar',clave=uuid.uuid4(),version=self.diag.version_edicion)).status_code,302)
        self.diag.refresh_from_db()
        self.assertEqual(self.client.post(self.url,dict(op='enviar_piezas',clave=uuid.uuid4(),
            version=self.diag.version_edicion)).status_code,302)
        self.sol=self.exp.solicitudrepuestos_set.get(diagnostico=self.diag)
        nueva=self.cotizar(archivo=None)
        self.assertEqual(nueva.diagnostico_id,self.diag.pk)
        self.revisar(nueva)
        self.assertTrue(s.aprobacion_conjunta_vigente(nueva))
        self.assertEqual(Diagnostico.objects.filter(expediente=self.exp).count(),2)
        self.assertEqual(RevisionAdministrativa.objects.filter(expediente=self.exp).count(),3)

    def test_devolucion_oficina_no_crea_otro_diagnostico(self):
        self.preparar();prop=self.cotizar(archivo=None);self.revisar(prop)
        self.revisar(prop,decision='DEVUELTO',motivo='Corregir condiciones',destino='OFICINA')
        self.assertEqual(Diagnostico.objects.filter(expediente=self.exp).count(),1)
        self.assertContains(self.client.get(self.url),'Corregir condiciones')
        self.assertFalse(s.aprobacion_conjunta_vigente(prop))

    def test_interfaz_consolidada_pdf_advanced_y_servidor(self):
        self.preparar();prop=self.cotizar(archivo=None)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
        self.assertRedirects(response,self.url)
        page=self.client.get(self.url)
        for text in ('Diagnóstico de TALLER','Cotización de OFICINA','Aprobar expediente','Devolver para corrección'):
            self.assertContains(page,text)
        self.assertNotContains(page,'Registrar asignación')
        self.assertContains(self.client.get(reverse('masiscam:tecnica_expediente',args=[self.exp.pk])+'?avanzado=1'),'Registrar asignación')
        url=reverse('masiscam:operacion_documento',args=[self.exp.pk,prop.evidencias.get().documento_id])+'?ver=1'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        self.client.force_login(self.taller)
        self.assertIn(self.client.get(url).status_code,(403,404))
        self.assertEqual(self.client.post(self.url,dict(op='aprobar_expediente',clave=uuid.uuid4(),
            diagnostico_id=self.diag.pk,propuesta_id=prop.pk)).status_code,403)

    def test_inmutabilidad_y_reintentos(self):
        self.preparar();prop=self.cotizar(archivo=None);key=uuid.uuid4()
        revision=self.revisar(prop,clave=key)
        self.assertEqual(self.revisar(prop,clave=key).pk,revision.pk)
        self.assertEqual(self.revisar(prop).pk,revision.pk)
        self.assertEqual(RevisionAdministrativa.objects.count(),1)
        for cambio in (dict(motivo='Alteración'),dict(snapshot={})):
            with transaction.atomic(),self.assertRaises(IntegrityError):
                RevisionAdministrativa.objects.filter(pk=revision.pk).update(**cambio)
        with transaction.atomic(),self.assertRaises(IntegrityError):RevisionAdministrativa.objects.filter(pk=revision.pk).delete()
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='EXPEDIENTE_REVISADO').exists())

    def test_empresa_ajena_y_autoaprobacion_prohibidas(self):
        from accounts.models import Empresa
        from .test_tecnica import usuario
        from .models import RolMasiscam
        self.preparar();prop=self.cotizar(archivo=None)
        ajena=Empresa.objects.create(nombre='Otra empresa revisión QA')
        externo=usuario(ajena,'admin-revision-ajena','ADMIN')
        with self.assertRaises(PermissionDenied):self.revisar(prop,usuario=externo,empresa=ajena)
        self.client.force_login(externo)
        self.assertEqual(self.client.get(self.url).status_code,403)
        RolMasiscam.objects.filter(perfil__user=self.taller).update(rol='ADMIN')
        with self.assertRaises(PermissionDenied):self.revisar(prop,usuario=self.taller)
        RolMasiscam.objects.filter(perfil__user=self.taller).update(rol='TALLER')
        RolMasiscam.objects.filter(perfil__user=self.oficina).update(rol='ADMIN')
        with self.assertRaises(PermissionDenied):self.revisar(prop,usuario=self.oficina)

    def test_informe_final_conserva_aprobaciones_independientes(self):
        from unittest.mock import patch
        crear_usuario=qa.qa_flujo.qa.usuario
        def usuario_vigente(empresa,nombre,rol):return crear_usuario(empresa,nombre,'ADMIN' if rol=='PLANTA' else rol)
        def comercial_conjunta():
            self.pieza=self.diag.piezas.get()
            prop=self.cotizar(archivo=None)
            self.assertIsNone(prop.informe_id)
            self.revisar(prop)
            return prop,self.aceptar(prop)
        with patch.object(self,'comercial',comercial_conjunta),patch.object(qa.qa_flujo.qa,'usuario',usuario_vigente):
            qa.qa_flujo.FlujoTests.test_recorrido_completo_plano_oficina_reparacion_calibracion_pdf(self)

    def test_autorizacion_historica_no_se_convierte_ni_se_reescribe(self):
        self.preparar()
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,
            decision='APROBADO',motivo='Fuente histórica ficticia QA',clave=uuid.uuid4())
        informe=t.reservar_informe(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,clave=uuid.uuid4())
        t.generar_informe(informe.pk);informe.refresh_from_db()
        snapshot=dict(numero=self.exp.numero,cliente=self.exp.recepcion.snapshot,diagnostico=self.diag.pk,
            revision=self.diag.revision,informe=informe.pk,hash=informe.hash_sha256,presupuesto_referencia='HISTORICA-QA')
        historica=t._save(PropuestaComercial(expediente=self.exp,autor=self.oficina,clave=uuid.uuid4(),
            diagnostico=self.diag,informe=informe,version=1,alcance='Alcance histórico ficticio QA',snapshot=snapshot))
        o._evidencia(historica,self.oficina,qa.presupuesto_qa(),'DOCUMENTO','Presupuesto histórico QA',uuid.uuid4())
        auth=self.aceptar(historica)
        self.assertTrue(o.autorizacion_valida(auth))
        self.assertFalse(RevisionAdministrativa.objects.exists())
        historica.refresh_from_db();self.assertEqual(historica.snapshot,snapshot)

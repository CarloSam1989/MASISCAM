"""Fictional end-to-end office/workshop handoff; never an operational approval."""
import uuid
from io import BytesIO
from pathlib import Path
import os
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_taller, test_tecnica as qa, test_calibracion as cq
from . import flujo_services as s, tecnica_services as t, operacion_services as o, calibracion_services as c, cierre_services as cierre
from .models import *
from .test_operacion import presupuesto_qa,verificacion_qa
from .test_plano import foto_pieza
from .recepcion_confirmacion import confirmar_recepcion
from .test_utils import consumir_respuesta

FLAGS=dict(cq.FLAGS,MASISCAM_CIERRE_LOCAL=True)


@override_settings(**FLAGS)
class FlujoTests(TestCase):
    setUp=test_taller.TallerTests.setUp
    datos=test_taller.TallerTests.datos
    crear=test_taller.TallerTests.crear
    recepcion=test_taller.TallerTests.recepcion

    def preparar_solicitud(self,plano=False,enviar=True,con_piezas=True):
        equipo=self.crear('RELEVO-'+str(Equipo.objects.count()))
        self.assertFalse(ExpedienteMantenimiento.objects.filter(registro__equipo=equipo).exists())
        exp=self.recepcion(equipo)
        confirmar_recepcion(usuario=self.taller,empresa=self.empresa,expediente_id=exp.pk,clave=uuid.uuid4(),
            placa='SIN_PLACA',motivo_sin_placa='QA',motivo_sin_guia='QA',general_id=exp.recepcion.evidencias.get().pk)
        qa.inspeccion_enviada(self.empresa,self.taller,exp)
        diag=t.crear_revision(usuario=self.taller,empresa=self.empresa,expediente_id=exp.pk,tipo='DIAGNOSTICO',clave=uuid.uuid4())
        diag=t.guardar_revision(usuario=self.taller,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=diag.pk,
            version=diag.version_edicion,clave=uuid.uuid4(),datos=dict(fecha=timezone.localdate(),fallas='Desgaste ficticio QA',
            causas='Hipótesis QA',componentes_afectados='Componente QA',recomendaciones='Reemplazar según revisión QA',
            conclusiones='Alcance ficticio QA',repuestos='REQUERIDOS' if con_piezas else 'SIN_REPUESTOS',motivo_sin_evidencia='Sin evidencia adicional QA'))
        datos=dict(origen='MANUAL',descripcion='Componente solicitado QA',numero_parte='QA-REP',cantidad='2',unidad='unidad QA',
            condicion='Desgaste QA',accion='REEMPLAZAR',motivo_manual='Identificación manual ficticia QA',observaciones='Observación QA')
        if plano:
            self.exp,self.planta=exp,self.usuarios['PLANTA']
            self.ref,self.parte=qa.TecnicaTests.referencia(self)
            from . import plano_services as ps
            mapa=ps.guardar_mapa(usuario=self.planta,empresa=self.empresa,referencia_id=self.ref.pk,clave=uuid.uuid4(),base_version=0,
                posiciones=[dict(parte=self.parte.pk,pagina=1,x='.5',y='.4')])
            datos.update(origen='BIBLIOTECA',parte_id=self.parte.pk,posicion_plano_id=mapa.posiciones.get().pk)
        from . import plano_services as ps
        if con_piezas:
            ps.guardar_seleccion(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,
                clave=uuid.uuid4(),datos=datos,fotografias=[foto_pieza()])
        diag.refresh_from_db()
        sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4()) if enviar else None
        self.equipo,self.exp,self.diag,self.sol=equipo,exp,diag,sol
        return sol

    def comercial(self):
        t.decidir_diagnostico(usuario=self.usuarios['PLANTA'],empresa=self.empresa,diagnostico_id=self.diag.pk,
            decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        prop=s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),
            referencia='PRESUPUESTO FICTICIO QA v1',archivo=presupuesto_qa())
        auth=o.decidir_comercial(usuario=self.oficina,empresa=self.empresa,propuesta_id=prop.pk,clave=uuid.uuid4(),
            archivo=qa.pdf_plano(),datos=dict(decision='APROBADO',fecha_cliente=timezone.now(),representante_cliente='PERSONA FICTICIA QA',
                motivo='Simulación de aceptación, sin valor real',**verificacion_qa()))
        return prop,auth

    def envio(self):
        pieza=self.diag.piezas.get()
        compra=s.adquirir(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,pieza_id=pieza.pk,
            cantidad='2',proveedor='PROVEEDOR FICTICIO QA',referencia='COMPRA QA',fecha=timezone.now(),clave=uuid.uuid4())
        envio=s.enviar_repuestos(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,numero_guia='GUIA QA 001',
            fecha=timezone.now(),lineas=[dict(adquisicion=compra.pk,cantidad='2')],clave=uuid.uuid4())
        return compra,envio

    def recibir(self,envio,cantidad='2',discrepancia='',clave=None):
        return s.confirmar_envio(usuario=self.taller,empresa=self.empresa,envio_id=envio.pk,fecha=timezone.now(),
            lineas=[dict(detalle=envio.lineas.get().pk,cantidad=cantidad,discrepancia=discrepancia)],clave=clave or uuid.uuid4())

    def test_recorrido_completo_plano_oficina_reparacion_calibracion_pdf(self):
        self.preparar_solicitud(plano=True)
        self.assertEqual(len(self.sol.snapshot['piezas']),1)
        self.assertEqual(Diagnostico.objects.filter(expediente=self.exp).count(),1)
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(reverse('masiscam:flujo_bandeja')),self.exp.numero)
        prop,auth=self.comercial()
        self.assertIn(self.diag.piezas.get().descripcion,prop.alcance)
        self.assertEqual(s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,
            clave=uuid.uuid4(),referencia='No duplicar'),prop)
        self.assertEqual(PropuestaComercial.objects.count(),1)
        compra,envio=self.envio()
        self.assertFalse(RecepcionRepuesto.objects.exists())
        self.recibir(envio)
        self.assertEqual(RecepcionRepuesto.objects.count(),1)
        self.assertEqual(RecepcionLineaEnvio.objects.get().recepcion_id,RecepcionRepuesto.objects.get().pk)
        common=dict(usuario=self.taller,empresa=self.empresa)
        act=o.iniciar_actividad(**common,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos=dict(trabajo='Montaje ficticio QA',componentes='Pieza QA',inicio=timezone.now()))
        o.usar_repuesto(**common,actividad_id=act.pk,recepcion_id=RecepcionRepuesto.objects.get().pk,cantidad='2',observaciones='QA',clave=uuid.uuid4())
        o.evidencia_actividad(**common,actividad_id=act.pk,clave=uuid.uuid4(),clase='DURANTE',leyenda='Trabajo ficticio QA',archivo=foto_pieza())
        resultado=o.terminar_actividad(**common,actividad_id=act.pk,clave=uuid.uuid4(),datos=dict(fin=timezone.now(),trabajo_realizado='Trabajo QA',resultado='Resultado QA'))
        o.revisar_actividad(usuario=self.usuarios['PLANTA'],empresa=self.empresa,resultado_id=resultado.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Revisión independiente QA')
        definicion=cq.definicion_qa();definicion['aplicacion'].update(marca=self.equipo.marca,modelo=self.equipo.modelo)
        protocolo=c.crear_plantilla(usuario=self.usuarios['PLANTA'],empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='FLUJO-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.equipo.tipo_producto,definicion=definicion,proyecto_id=self.equipo.proyecto_id))
        c.aplicar_plantilla(usuario=self.usuarios['PLANTA'],empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.equipo.pk,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        revisor=qa.usuario(self.empresa,'validador-flujo-qa','PLANTA')
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            cq.validar_qa(self.empresa,revisor,protocolo)
            cal=c.iniciar(**common,expediente_id=self.exp.pk,plantilla_id=protocolo.pk,clave=uuid.uuid4())
            cal=cq.completar(self.empresa,self.taller,cal)
            cal=c.enviar(**common,calibracion_id=cal.pk,version=cal.version_edicion,clave=uuid.uuid4())
            with self.captureOnCommitCallbacks(execute=True):
                c.decidir(usuario=self.usuarios['PLANTA'],empresa=self.empresa,calibracion_id=cal.pk,clave=uuid.uuid4(),decision='APROBADA',
                    motivo='Revisión independiente QA',evaluaciones={m['codigo']:'CONFORME' for m in definicion['mediciones']})
            informe=InformeFinal.objects.get(expediente=self.exp)
            self.assertTrue(informe.documento_id)
            self.assertEqual(informe.estado,'PENDIENTE_REVISION')
            evento_auto=Auditoria.objects.get(expediente=self.exp,accion='FINAL_GENERACION_AUTOMATICA')
            self.assertEqual(evento_auto.usuario_id,self.usuarios['PLANTA'].pk)
            self.assertEqual(evento_auto.detalle['responsable_datos'],self.taller.pk)
            self.assertIn('flujo_operativo',informe.snapshot)
            self.assertEqual(informe.snapshot['flujo_operativo'][0]['envios'][0]['guia'],'GUIA QA 001')
            self.assertEqual(informe.snapshot['cotizacion']['responsable'],self.oficina.username)
            with informe.documento.archivo.open('rb') as stream: data=stream.read()
            from pypdf import PdfReader
            texto='\n'.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
            self.assertIn('GUIA QA 001',texto);self.assertIn('PROVEEDOR FICTICIO QA',texto)
            output=os.environ.get('MASISCAM_FLOW_PDF_EVIDENCE')
            if output:
                path=Path(output);path.mkdir(parents=True,exist_ok=True)
                (path/'informe-final-ficticio-qa.pdf').write_bytes(data)
                with envio.documento.archivo.open('rb') as stream: (path/'guia-repuestos-ficticia-qa.pdf').write_bytes(stream.read())
            with self.assertRaises(PermissionDenied): cierre.revisar(**common,informe_id=informe.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Intento QA')
            cierre.revisar(usuario=self.usuarios['PLANTA'],empresa=self.empresa,informe_id=informe.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Revisión final QA')
            cierre.cerrar(usuario=self.usuarios['GERENCIA'],empresa=self.empresa,informe_id=informe.pk,clave=uuid.uuid4(),motivo='Cierre independiente QA')
            self.assertTrue(CierreExpediente.objects.filter(expediente=self.exp).exists())
            self.assertEqual(s.resumen(self.exp,self.taller)['accion']['texto'],'Registrar nuevo mantenimiento')
            from PIL import Image
            from django.core.files.uploadedfile import SimpleUploadedFile
            stream=BytesIO();Image.new('RGB',(45,32),'yellow').save(stream,'PNG')
            from .recepcion_services import abrir_recepcion
            siguiente=abrir_recepcion(**common,equipo_id=self.equipo.pk,cliente_id=self.equipo.cliente_id,
                clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),condicion_ingreso='Nuevo mantenimiento ficticio QA',
                fotografias=[SimpleUploadedFile('nuevo-ingreso-qa.png',stream.getvalue(),content_type='image/png')])
            self.assertNotEqual(siguiente.pk,self.exp.pk)
            self.assertEqual(siguiente.registro.equipo_id,self.equipo.pk)
            self.assertTrue(CierreExpediente.objects.filter(expediente=self.exp).exists())

    def test_solicitud_congelada_idempotencia_y_revision_independiente(self):
        self.preparar_solicitud()
        self.diag.refresh_from_db()
        otra=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4())
        self.assertEqual(otra,self.sol)
        self.assertEqual(SolicitudRepuestos.objects.count(),1)
        with self.assertRaises(IntegrityError),transaction.atomic(): SolicitudRepuestos.objects.filter(pk=self.sol.pk).update(snapshot={})
        with self.assertRaises(IntegrityError),transaction.atomic(): self.sol.delete()
        with self.assertRaises(PermissionDenied): t.decidir_diagnostico(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        with self.assertRaises(ValidationError): s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='QA')
        self.assertFalse(PropuestaComercial.objects.exists())

    def test_compras_y_reparaciones_no_se_crean_sin_autorizacion(self):
        self.preparar_solicitud()
        with self.assertRaises(ValidationError): self.envio()
        self.assertFalse(AdquisicionRepuesto.objects.exists())
        with self.assertRaises(ValidationError): o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos={})
        self.comercial()
        with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
            with self.assertRaises(ValidationError): self.envio()
        self.assertFalse(AdquisicionRepuesto.objects.exists())

    def test_cantidades_envio_recepcion_diferencias_y_reintentos(self):
        self.preparar_solicitud();self.comercial();compra,envio=self.envio()
        for qty in ['0','-1','NaN','3']:
            with self.assertRaises(ValidationError):
                s.enviar_repuestos(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,numero_guia='OTRA',fecha=timezone.now(),lineas=[dict(adquisicion=compra.pk,cantidad=qty)],clave=uuid.uuid4())
        with self.assertRaises(ValidationError): self.recibir(envio,'1')
        self.assertFalse(RecepcionRepuesto.objects.exists())
        result=self.recibir(envio,'1','Falta una unidad, documentado QA')
        self.assertEqual(result.lineas.get().cantidad,1)
        self.assertEqual(RecepcionRepuesto.objects.get().cantidad,1)
        with self.assertRaises(ValidationError): self.recibir(envio)
        with self.assertRaises(IntegrityError),transaction.atomic(): EnvioRepuestos.objects.filter(pk=envio.pk).update(numero_guia='OTRA')
        with self.assertRaises(IntegrityError),transaction.atomic(): RecepcionLineaEnvio.objects.filter(confirmacion=result).update(cantidad=2)

    def test_permisos_urls_ajax_descargas_y_empresa(self):
        self.preparar_solicitud();self.comercial();compra,envio=self.envio()
        url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        doc=reverse('masiscam:flujo_documento',args=[self.exp.pk,envio.documento_id])
        self.client.force_login(self.taller)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertTrue(consumir_respuesta(self.client.get(doc)).startswith(b'%PDF-'))
        self.assertContains(self.client.get(reverse('masiscam:taller_inicio')),'Repuestos enviados')
        from django.test import Client
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.taller)
        self.assertEqual(csrf.post(url,{'op':'solicitud','clave':str(uuid.uuid4())}).status_code,403)
        foto=self.sol.snapshot['piezas'][0]['fotos'][0]['documento']
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(reverse('masiscam:dashboard')),'Revisar solicitudes de taller')
        response=self.client.get(reverse('masiscam:flujo_documento',args=[self.exp.pk,foto]),{'ver':'1'})
        self.assertEqual(response['Content-Type'],'image/png')
        self.assertTrue(response['Content-Disposition'].startswith('inline'))
        self.assertTrue(consumir_respuesta(response).startswith(b'\x89PNG'))
        self.client.force_login(self.taller)
        self.assertEqual(self.client.get(reverse('masiscam:flujo_bandeja')).status_code,403)
        for codigo in ['TECNICO','ADMIN','CONSULTA','CLIENTE']:
            self.client.force_login(self.usuarios[codigo])
            self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.get(doc).status_code,403)
        with self.assertRaises(PermissionDenied): s.adquirir(usuario=self.taller,empresa=self.empresa,solicitud_id=self.sol.pk,pieza_id=self.diag.piezas.get().pk,cantidad=1,proveedor='QA',referencia='QA',fecha=timezone.now(),clave=uuid.uuid4())
        otra=qa.Empresa.objects.create(nombre='Otra empresa QA')
        oficina=qa.usuario(otra,'oficina-ajena-flujo','OFICINA')
        self.client.force_login(oficina)
        self.assertEqual(self.client.get(url).status_code,403)

    def test_asistente_una_accion_principal_y_formulario_manual(self):
        self.preparar_solicitud()
        self.client.force_login(self.taller)
        response=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.context['pasos']),5)
        self.assertIsNone(response.context['accion'])
        self.client.force_login(self.oficina)
        self.comercial()
        response=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk]))
        self.assertEqual(response.context['accion']['op'],'adquirir')

    def test_reintentos_http_no_duplican_solicitud_envio_ni_recepcion(self):
        self.preparar_solicitud(enviar=False)
        url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        datos={field:str(getattr(self.diag,field) or '') for field in t.DIA_FIELDS}
        datos.update(op='solicitud',clave=str(uuid.uuid4()),version=str(self.diag.version_edicion))
        for _ in range(2): self.assertEqual(self.client.post(url,datos).status_code,302)
        self.sol=SolicitudRepuestos.objects.get(expediente=self.exp)
        self.assertEqual(self.client.post(url,dict(datos,fallas='Intento de cambiar contenido congelado')).status_code,400)
        self.assertEqual(SolicitudRepuestos.objects.count(),1)
        self.comercial();compra,envio=self.envio()
        self.client.force_login(self.oficina)
        envio_data=dict(op='enviar',clave=str(envio.clave),fecha=envio.fecha.isoformat(),numero_guia=envio.numero_guia)
        envio_data[f'cantidad_{compra.pk}']='2'
        self.assertEqual(self.client.post(url,envio_data).status_code,302)
        self.assertEqual(EnvioRepuestos.objects.count(),1)
        confirmado=self.recibir(envio)
        self.client.force_login(self.taller)
        recibido=dict(op='recibir',clave=str(confirmado.clave),envio=str(envio.pk),fecha=confirmado.fecha.isoformat(),observaciones='')
        recibido[f'cantidad_{envio.lineas.get().pk}']='2';recibido[f'diferencia_{envio.lineas.get().pk}']=''
        self.assertEqual(self.client.post(url,recibido).status_code,302)
        self.assertEqual(RecepcionRepuesto.objects.count(),1)

    def test_recepcion_fisica_con_politica_retirada_no_autoriza_reparar(self):
        self.preparar_solicitud();self.comercial();_,envio=self.envio()
        with override_settings(MASISCAM_POLITICA_COMERCIAL_LOCAL=None):
            self.assertEqual(s.resumen(self.exp,self.taller)['accion']['op'],'recibir')
            self.recibir(envio)
            with self.assertRaises(ValidationError):
                o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,
                    clave=uuid.uuid4(),datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.assertEqual(RecepcionRepuesto.objects.count(),1)

    def test_sin_repuestos_no_obliga_a_inventar_piezas_compras_o_envios(self):
        self.preparar_solicitud(con_piezas=False)
        self.assertEqual(self.sol.snapshot['piezas'],[])
        propuesta,_=self.comercial()
        self.assertIn(self.diag.recomendaciones,propuesta.alcance)
        self.assertFalse(PiezaRequerida.objects.exists())
        self.assertFalse(AdquisicionRepuesto.objects.exists())
        self.assertFalse(EnvioRepuestos.objects.exists())
        estado=s.resumen(self.exp,self.taller)
        self.assertEqual(estado['paso'],4)
        self.assertEqual(estado['accion']['texto'],'Iniciar reparación')

    def test_aceptacion_parcial_no_autoriza_comprar_el_listado_completo(self):
        self.preparar_solicitud()
        t.decidir_diagnostico(usuario=self.usuarios['PLANTA'],empresa=self.empresa,diagnostico_id=self.diag.pk,
            decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        prop=s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),
            referencia='PRESUPUESTO PARCIAL FICTICIO QA',archivo=presupuesto_qa())
        auth=o.decidir_comercial(usuario=self.oficina,empresa=self.empresa,propuesta_id=prop.pk,clave=uuid.uuid4(),
            archivo=qa.pdf_plano(),datos=dict(decision='PARCIAL',fecha_cliente=timezone.now(),representante_cliente='FICTICIO QA',
                motivo='Solo prueba QA',alcance_autorizado='Solo evaluación QA, sin adquisición de la lista completa',**verificacion_qa()))
        self.assertTrue(o.autorizacion_valida(auth))
        with self.assertRaises(ValidationError): self.envio()
        self.assertFalse(AdquisicionRepuesto.objects.exists())
        self.assertTrue(s.resumen(self.exp,self.oficina)['bloqueo'])

import os
import re
import uuid
from pathlib import Path
from decimal import Decimal
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import flujo_services as s, operacion_services as o, tecnica_services as t
from .test_tres_roles import TresRolesFixture
from . import test_flujo as qa_flujo
from .test_operacion import presupuesto_qa, verificacion_qa
from .test_tecnica import pdf_plano
from .models import PropuestaComercial, EnvioRepuestos, RecepcionRepuesto, Auditoria, AdquisicionRepuesto, Diagnostico

FLAGS=qa_flujo.FLAGS


class OficinaFixture(TresRolesFixture):
    def preparar(self,aprobar_tecnico=True):
        self.preparar_solicitud()
        if aprobar_tecnico:
            t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,
                decision='APROBADO',motivo='Prueba aislada',clave=uuid.uuid4())
        self.pieza=self.diag.piezas.get()
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.oficina)

    def detalle(self,precio='12.35'):
        return dict(moneda='USD',lineas=[dict(tipo='SERVICIO',descripcion='Reparación del reductor',cantidad='2',unidad='servicio',precio_unitario=precio,justificacion='Servicio acordado con el cliente')],
            factura=dict(numero='FACTURA-REFERENCIA-QA',fecha='2026-11-14'))

    def cotizar(self,**changes):
        from django.core.files.uploadedfile import SimpleUploadedFile
        if not hasattr(self,'presupuesto_bytes'):self.presupuesto_bytes=presupuesto_qa().read()
        data=dict(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,
            clave=uuid.uuid4(),referencia='COTIZACION-QA',cotizacion=self.detalle(),
            archivo=SimpleUploadedFile('presupuesto-qa.pdf',self.presupuesto_bytes,content_type='application/pdf'))
        data.update(changes)
        return s.cotizar(**data)

    def aceptar(self,propuesta):
        if not s.aprobacion_conjunta_vigente(propuesta):
            s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,
                diagnostico_id=propuesta.diagnostico_id,propuesta_id=propuesta.pk,decision='APROBADO',clave=uuid.uuid4())
        return o.decidir_comercial(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk,
            clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(decision='APROBADO',fecha_cliente=timezone.now(),
                representante_cliente='Cliente ficticio QA',motivo='Aceptación simulada aislada',**verificacion_qa()))


@override_settings(**FLAGS)
class OficinaGestionTests(OficinaFixture,TestCase):
    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_pdf_privado_ambos_roles_sin_fase4_y_archivo_ausente(self):
        from django.test import Client
        from accounts.models import Empresa
        from .test_tecnica import usuario
        self.preparar()
        propuesta=self.cotizar(archivo=None)
        doc=propuesta.evidencias.get().documento
        with doc.archivo.open('rb') as archivo: contenido=archivo.read()
        urls=[reverse('masiscam:'+name,args=[self.exp.pk,doc.pk])
            for name in ('flujo_documento','operacion_documento')]
        for user in (self.oficina,self.admin):
            self.client.force_login(user)
            for url in urls:
                for suffix,disposition in (('?ver=1','inline'),('','attachment')):
                    response=self.client.get(url+suffix)
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response['Content-Type'],'application/pdf')
                    self.assertTrue(response['Content-Disposition'].startswith(disposition))
                    self.assertEqual(response['Cache-Control'],'private, no-store')
                    self.assertEqual(response['X-Frame-Options'],'SAMEORIGIN')
                    self.assertEqual(response['X-Content-Type-Options'],'nosniff')
                    self.assertEqual(b''.join(response.streaming_content),contenido)
        self.assertEqual(Client().get(urls[0]).status_code,302)
        ajena=usuario(Empresa.objects.create(nombre='Ajena PDF QA'),'ajena-documentos-pdf','OFICINA')
        for user in (self.taller,ajena):
            self.client.force_login(user)
            for url in urls:self.assertIn(self.client.get(url).status_code,(403,404))
        self.client.force_login(self.oficina)
        doc.archivo.storage.delete(doc.archivo.name)
        try:
            for url in urls:
                response=self.client.get(url+'?ver=1')
                self.assertEqual(response.status_code,503)
                self.assertContains(response,'El archivo no está disponible',status_code=503)
                self.assertEqual(response['Cache-Control'],'private, no-store')
        finally:
            with doc.archivo.storage.open(doc.archivo.name,'wb') as archivo:archivo.write(contenido)
        self.assertEqual(propuesta.evidencias.count(),1)

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_repuestos_son_informativos_y_no_entran_en_total_ni_inventario(self):
        from io import BytesIO
        from pypdf import PdfReader
        from .flujo_models import AdquisicionRepuesto, EnvioRepuestos
        from .models import Auditoria
        self.preparar()
        with self.assertRaises(ValidationError):   # no se acepta una línea por repuesto
            self.cotizar(cotizacion=dict(self.detalle(),lineas=[dict(pieza=self.pieza.pk,precio_unitario='5.00')]))
        detalle=dict(self.detalle('10.00'),lineas=[
            dict(tipo='SERVICIO',descripcion='Reparación del reductor',cantidad='2',unidad='servicio',precio_unitario='10.00',justificacion='Acordado QA'),
            dict(tipo='CONSUMIBLE',descripcion='Aceite ISO VG 220',cantidad='3.5',unidad='L',precio_unitario='4.00',justificacion='Acordado QA')])
        propuesta=self.cotizar(archivo=None,cotizacion=detalle)
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'34.00')   # 20.00 + 14.00; el repuesto no suma
        self.assertTrue(all('pieza' not in r for r in propuesta.snapshot['cotizacion']['lineas']))
        ev=s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk)
        with ev.documento.archivo.open('rb') as f:text=chr(10).join(pg.extract_text() for pg in PdfReader(BytesIO(f.read())).pages)
        for esperado in ('SERVICIOS Y CONSUMIBLES COTIZADOS','Aceite ISO VG 220','34.00','REPUESTOS REQUERIDOS','no incluidos en el valor',self.pieza.descripcion):
            self.assertIn(esperado,text)
        self.assertLess(text.index('SERVICIOS Y CONSUMIBLES COTIZADOS'),text.index('REPUESTOS REQUERIDOS'))
        self.assertEqual(AdquisicionRepuesto.objects.count(),0)
        self.assertEqual(EnvioRepuestos.objects.count(),0)
        response=self.client.get(self.url)
        self.assertContains(response,'Repuestos requeridos (no incluidos en el valor de la cotización)')
        self.assertNotContains(response,'Precio unitario de ')
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='OFICINA_COTIZACION').exists())

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_cotizacion_nace_del_diagnostico_de_taller(self):
        import re
        from io import BytesIO
        from pypdf import PdfReader
        self.preparar_solicitud(enviar=False)
        diag=self.diag
        base=dict(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk)
        trabajos=[dict(tipo='MANTENIMIENTO',descripcion='Mantenimiento preventivo del reductor',cantidad='1',unidad='servicio'),
            dict(tipo='REPARACION',descripcion='Reparación de eje y rodamientos',cantidad='2',unidad='hora'),
            dict(tipo='CONSUMIBLE',descripcion='Aceite ISO VG 220',cantidad='5',unidad='L')]
        for incompleto in (dict(trabajos[0],unidad=''),dict(trabajos[0],descripcion=''),dict(trabajos[0],cantidad='0'),dict(trabajos[0],tipo='VENTA')):
            with self.assertRaises(ValidationError):   # no se inventan cantidades, unidades ni conceptos
                t.agregar_concepto(**base,version=diag.version_edicion,clave=uuid.uuid4(),datos=incompleto)
        clave=uuid.uuid4()
        t.agregar_concepto(**base,version=diag.version_edicion,clave=clave,datos=trabajos[0])
        t.agregar_concepto(**base,version=diag.version_edicion,clave=clave,datos=trabajos[0])   # reintento idempotente
        with self.assertRaises(ValidationError):   # duplicado con otra clave
            t.agregar_concepto(**base,version=diag.version_edicion,clave=uuid.uuid4(),datos=trabajos[0])
        for trabajo in trabajos[1:]:
            t.agregar_concepto(**base,version=diag.version_edicion,clave=uuid.uuid4(),datos=trabajo)
        self.assertEqual(diag.conceptos.count(),3)
        diag.refresh_from_db()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4())
        self.assertEqual([c['descripcion'] for c in self.sol.snapshot['conceptos']],[x['descripcion'] for x in trabajos])
        with self.assertRaises((ValidationError,PermissionDenied)):   # congelado con la solicitud
            t.agregar_concepto(**base,version=diag.version_edicion,clave=uuid.uuid4(),datos=dict(trabajos[0],descripcion='Otro'))
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        self.pieza=diag.piezas.get(); self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.oficina)
        response=self.client.get(self.url)
        for texto in ('Mantenimiento preventivo del reductor','Reparación de eje y rodamientos','Aceite ISO VG 220','Componente solicitado QA'):
            self.assertContains(response,texto)
        self.assertEqual(len(response.context['forms']['cotizar'].filas),3)   # ya no hay filas vacías
        self.assertNotContains(response,'Precio unitario de Componente solicitado QA')   # el repuesto no tiene precio
        ids=[c['id'] for c in self.sol.snapshot['conceptos']]
        def lineas(precios,**extra):
            return dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,precios)]+extra.get('mas',[]))
        for malo in (lineas(['100.00','250.00']),lineas(['100.00','250.00','4.00'],mas=[dict(concepto=ids[0],precio_unitario='1')]),
                dict(moneda='USD',lineas=[dict(concepto=999999,precio_unitario='1')]),
                lineas(['100.00','250.00','4.00'],mas=[dict(tipo='SERVICIO',descripcion='Adicional',cantidad='1',unidad='serv',precio_unitario='5.00')])):
            with self.assertRaises(ValidationError):   # faltan conceptos, duplicado, ajeno o adicional sin justificación
                s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='COT-DIAG-QA',cotizacion=malo)
        buena=lineas(['100.00','250.00','4.00'])
        propuesta=s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='COT-DIAG-QA',cotizacion=buena)
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'620.00')   # 100 + 2*250 + 5*4; los repuestos no suman
        self.assertEqual([l['origen'] for l in propuesta.snapshot['cotizacion']['lineas']],['TALLER']*3)
        self.assertEqual([l['concepto'] for l in propuesta.snapshot['cotizacion']['lineas']],ids)
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)
        repetida=s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='COT-DIAG-QA',cotizacion=buena)
        self.assertEqual(repetida.pk,propuesta.pk)   # sin duplicar al repetir
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)
        ev=s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk)
        with ev.documento.archivo.open('rb') as f:texto=chr(10).join(pg.extract_text() for pg in PdfReader(BytesIO(f.read())).pages)
        for esperado in ('DIAGNÓSTICO TÉCNICO','Desgaste ficticio QA','SERVICIOS Y CONSUMIBLES COTIZADOS','Mantenimiento preventivo del reductor',
                'Aceite ISO VG 220','620.00','REPUESTOS REQUERIDOS','Componente solicitado QA'):
            self.assertIn(esperado,texto)
        self.assertEqual(AdquisicionRepuesto.objects.count(),0); self.assertEqual(EnvioRepuestos.objects.count(),0)
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='CONCEPTO_COTIZABLE_AGREGADO').count(),3)
        # La cotización queda ligada a su revisión; una nueva solicitud no la sobrescribe.
        self.assertEqual(propuesta.diagnostico_id,diag.pk)

    def _con_conceptos(self):
        self.preparar_solicitud(enviar=False)
        diag=self.diag
        trabajos=[dict(tipo='MANTENIMIENTO',descripcion='Mantenimiento preventivo del reductor',cantidad='1',unidad='servicio'),
            dict(tipo='REPARACION',descripcion='Reparación de eje y rodamientos',cantidad='2',unidad='hora'),
            dict(tipo='CONSUMIBLE',descripcion='Aceite ISO VG 220',cantidad='5',unidad='L')]
        for trabajo in trabajos:
            t.agregar_concepto(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4(),datos=trabajo)
        diag.refresh_from_db()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4())
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        return trabajos

    def test_transicion_admin_cliente_repuestos_y_reparacion_en_taller_sin_nuevo_expediente(self):
        from .models import ExpedienteMantenimiento, RevisionAdministrativa, DecisionComercial
        from .test_plano import foto_pieza
        self.preparar(aprobar_tecnico=False)
        prop=self.cotizar(archivo=None)
        def taller(): return s.resumen(self.exp,self.taller)
        def lista():
            self.client.force_login(self.taller)
            return self.client.get(reverse('masiscam:taller_estado')).content.decode()
        def detalle_taller():
            self.client.force_login(self.taller)
            return self.client.get(reverse('masiscam:taller_equipo',args=[self.exp.registro.equipo_id])+f'?expediente={self.exp.pk}')
        # 1) Cotizada: TALLER ve que falta la aprobación de ADMINISTRADOR (no la del cliente).
        self.client.force_login(self.admin); previo=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).content.decode()
        for marca in ('class="mf-steps"','class="mf-next"','mf-main-action','Revisar expediente'): self.assertIn(marca,previo,marca)
        self.assertIn('revisar conjuntamente',taller()['mensaje']); self.assertIn('Pendiente de revisión de ADMINISTRADOR',lista())
        # 2) Aprobación administrativa persistida; TALLER la ve, y NO se confunde con la aceptación del cliente.
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        self.assertEqual(RevisionAdministrativa.objects.get(expediente=self.exp).decision,'APROBADO')
        self.assertTrue(s.aprobacion_conjunta_vigente(prop)); self.assertEqual(Diagnostico.objects.get(pk=self.diag.pk).estado,'FINALIZADO')
        # La aprobación de ADMINISTRADOR es la única condición comercial: no se pide aceptación del cliente ni política JSON.
        r=taller(); self.assertEqual(r['accion']['texto'],'Registrar repuestos del cliente'); self.assertNotIn('aceptación del cliente',r['mensaje'].lower())
        self.assertIn('Aprobado por ADMINISTRADOR',lista()); self.assertNotContains(detalle_taller(),'Pendiente de aceptación del cliente')
        # Una sola fuente de estado: TALLER, OFICINA y ADMINISTRADOR ven el mismo stepper (paso 3 aprobado, paso 4 accesible).
        def estados(usuario): return [(p['nombre'],p['estado'],p['clase']) for p in s.resumen(self.exp,usuario)['pasos']]
        self.assertEqual(estados(self.taller),estados(self.oficina)); self.assertEqual(estados(self.taller),estados(self.admin))
        pasos=s.resumen(self.exp,self.taller)['pasos']
        self.assertEqual([p['estado'] for p in pasos],['Listo','Listo','Aprobado','En proceso','Pendiente'])
        self.assertEqual([p['clase'] for p in pasos],['ok','ok','ok','proceso','pendiente']); self.assertEqual(s.resumen(self.exp,self.taller)['paso'],4)
        # ADMINISTRADOR usa los mismos componentes visuales que TALLER (stepper, chips, bloque de aprobación y «Siguiente paso»).
        self.client.force_login(self.admin)
        html_admin=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).content.decode()
        self.client.force_login(self.taller)
        html_taller=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).content.decode()
        for marca in ('class="mf-steps"','mf-step is-ok','mf-step is-proceso','mf-chip','data-aprobado-admin','class="mf-next"','id="siguiente"'):
            self.assertIn(marca,html_admin,marca); self.assertIn(marca,html_taller,marca)
        self.assertIn('mf-main-action',html_taller)   # TALLER: botón principal «Registrar repuestos del cliente»
        self.assertNotIn('no sustituye la aceptación',html_admin); self.assertNotIn('Pendiente de aceptación del cliente',html_admin)
        self.assertNotIn('Registrar respuesta del cliente',str(s.resumen(self.exp,self.oficina)['accion']))
        self.assertFalse(DecisionComercial.objects.filter(expediente=self.exp).exists())   # sin aprobaciones inventadas hasta usarla
        with self.assertRaises(ValidationError):    # sin repuestos del cliente registrados no se repara
            o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
                datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.assertFalse(DecisionComercial.objects.filter(expediente=self.exp).exists())
        # 4) Repuestos del cliente registrados: TALLER puede iniciar la reparación en el mismo expediente.
        rec=o.recibir_repuesto(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(pieza_id=self.pieza.pk,procedencia='Cliente',entregado_por='Cliente QA',numero_parte=self.pieza.numero_parte,
                descripcion=self.pieza.descripcion,cantidad=str(self.pieza.cantidad),unidad=self.pieza.unidad,fecha=timezone.now(),guia='',discrepancia=''))
        r=taller(); self.assertEqual((r['paso'],r['accion']['texto']),(4,'Iniciar reparación')); self.assertIn('Registra la reparación',r['mensaje'])
        act=o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(trabajo='Mantenimiento del reductor',componentes='Pieza QA',inicio=timezone.now()))
        o.usar_repuesto(usuario=self.taller,empresa=self.empresa,actividad_id=act.pk,recepcion_id=rec.pk,cantidad=str(self.pieza.cantidad),observaciones='QA',clave=uuid.uuid4())
        o.evidencia_actividad(usuario=self.taller,empresa=self.empresa,actividad_id=act.pk,clave=uuid.uuid4(),clase='DURANTE',leyenda='Trabajo QA',archivo=foto_pieza())
        resultado=o.terminar_actividad(usuario=self.taller,empresa=self.empresa,actividad_id=act.pk,clave=uuid.uuid4(),
            datos=dict(fin=timezone.now(),trabajo_realizado='Mantenimiento del reductor',resultado='Resultado QA'))
        self.assertIn('ADMINISTRADOR debe revisar',taller()['mensaje'])
        o.revisar_actividad(usuario=self.admin,empresa=self.empresa,resultado_id=resultado.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Revisión independiente QA')
        dec=DecisionComercial.objects.get(expediente=self.exp)   # autorización derivada de la revisión de ADMINISTRADOR
        self.assertEqual((dec.politica['tipo'],dec.politica['revision']),('ADMINISTRATIVA',RevisionAdministrativa.objects.get(expediente=self.exp).pk))
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='AUTORIZACION_ADMINISTRATIVA').count(),1)
        self.assertIn('Reparación y mantenimiento',lista())
        r=taller()
        self.assertTrue(r['aprobado_admin']); self.assertEqual([p['estado'] for p in r['pasos']][2],'Aprobado'); self.assertEqual(r['pasos'][2]['clase'],'ok')
        self.assertIn('clase',r['pasos'][0]); self.assertNotIn('Pendiente de aceptación del cliente',r['mensaje'])
        # Calibración: sin protocolo aprobado se bloquea con un mensaje claro; con protocolo validado TALLER genera el informe.
        self.assertEqual(r['mensaje'],'Falta protocolo de calibración aprobado para continuar.'); self.assertTrue(r['bloqueo']); self.assertIsNone(r['accion'])
        from . import calibracion_services as c, test_calibracion as cq
        definicion=cq.definicion_qa(); definicion['aplicacion'].update(marca=self.exp.registro.equipo.marca,modelo=self.exp.registro.equipo.modelo)
        protocolo=c.crear_plantilla(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='OG-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.exp.registro.equipo.tipo_producto,definicion=definicion,proyecto_id=self.exp.registro.equipo.proyecto_id))
        c.aplicar_plantilla(usuario=self.admin,empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.exp.registro.equipo_id,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            from .test_tecnica import usuario as _usuario
            cq.validar_qa(self.empresa,_usuario(self.empresa,'validador-og-qa','ADMIN'),protocolo)
            r=taller(); self.assertEqual(r['accion']['texto'],'Generar informe de calibración'); self.assertFalse(r['bloqueo'])
            self.client.force_login(self.taller)
            html=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).content.decode()
            self.assertIn('Generar informe de calibración',html); self.assertIn('data-aprobado-admin',html); self.assertIn('is-ok',html)
            self.assertNotIn('Registrar respuesta del cliente',html); self.assertNotIn('MASISCAM_POLITICA_COMERCIAL',html)
            self.client.force_login(self.oficina)
            self.assertNotEqual(s.resumen(self.exp,self.oficina)['accion'] and s.resumen(self.exp,self.oficina)['accion']['texto'],'Generar informe de calibración')
        self.assertEqual(ExpedienteMantenimiento.objects.filter(registro__equipo=self.exp.registro.equipo).count(),1)
        self.assertEqual((EnvioRepuestos.objects.count(),AdquisicionRepuesto.objects.count()),(0,0))

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_idempotencia_guardar_avance_y_validar_sin_conflictos(self):
        import re
        self.preparar_solicitud(enviar=False)
        self.client.force_login(self.taller); url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        def leer(html):
            form=html[html.index('id="mf-diagnostico"'):]; form=form[:form.index('</form>')]
            clave=re.search(r'name="clave"[^>]*value="([0-9a-f-]{36})"',form) or re.search(r'value="([0-9a-f-]{36})"[^>]*name="clave"',form)
            return clave.group(1),int(re.search(r'name="version"[^>]*value="(\d+)"',form).group(1))
        def pagina():
            return leer(self.client.get(url).content.decode())
        def datos(clave,version,op,**campos):
            base=dict(op=op,clave=clave,version=version,fecha=timezone.localdate().isoformat(),fallas='Desgaste ficticio QA',
                causas='Hipótesis QA',componentes_afectados='Componente QA',recomendaciones='Reemplazar',conclusiones='Alcance QA',
                repuestos='REQUERIDOS',motivo_sin_evidencia='Sin evidencia adicional QA',observaciones='')
            return base|campos
        def eventos(): return Auditoria.objects.filter(expediente=self.exp,accion='DIAGNOSTICO_GUARDADA').count()
        k1,v1=pagina()
        # 1) Un «Validar» que guarda y luego falla vuelve con lo escrito y una clave NUEVA (no la ya consumida).
        malo=self.client.post(url,datos(k1,v1,'validar',fallas='',causas='',componentes_afectados='',recomendaciones='',conclusiones=''))
        self.assertEqual(malo.status_code,400)
        k2,v2=leer(malo.content.decode())
        self.assertNotEqual(k2,k1)
        self.assertEqual(Diagnostico.objects.get(pk=self.diag.pk).enviado_en,None)
        # 2) Corregir el formulario y volver a guardar no produce conflicto, ni con Guardar avance ni con Validar.
        ok=self.client.post(url,datos(k2,v2,'guardar',fallas='Desgaste corregido QA'))
        self.assertEqual(ok.status_code,302)
        antes=eventos()
        # 3) Doble clic / reenvío / reintento de red: misma clave y mismo contenido, sin duplicar ni conflicto.
        for _ in range(3): self.assertEqual(self.client.post(url,datos(k2,v2,'guardar',fallas='Desgaste corregido QA')).status_code,302)
        self.assertEqual(eventos(),antes)
        self.assertEqual(Diagnostico.objects.filter(expediente=self.exp).count(),1)
        # 4) La protección sigue: la misma clave con otro contenido se rechaza y no altera el diagnóstico.
        conflicto=self.client.post(url,datos(k2,v2,'guardar',fallas='Otro contenido'))
        self.assertEqual(conflicto.status_code,400); self.assertContains(conflicto,'Conflicto',status_code=400)
        self.assertEqual(Diagnostico.objects.get(pk=self.diag.pk).fallas,'Desgaste corregido QA')
        # 5) Cada guardado nuevo usa clave nueva: modificar y volver a guardar, y luego validar.
        k3,v3=pagina(); self.assertNotEqual(k3,k2)
        self.assertEqual(self.client.post(url,datos(k3,v3,'guardar',fallas='Tercera edición QA')).status_code,302)
        k4,v4=pagina(); self.assertNotEqual(k4,k3)
        self.assertEqual(self.client.post(url,datos(k4,v4,'validar',fallas='Tercera edición QA')).status_code,302)
        self.assertIsNotNone(Diagnostico.objects.get(pk=self.diag.pk).enviado_en)

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_expediente_nuevo_muestra_solo_los_dos_conceptos_de_taller(self):
        self.preparar_solicitud(enviar=False); diag=self.diag
        for trabajo in (dict(tipo='REPARACION',descripcion='Reparación de eje',cantidad='2',unidad='hora'),
                dict(tipo='MANTENIMIENTO',descripcion='Cambio de sellos',cantidad='1',unidad='servicio')):
            t.agregar_concepto(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4(),datos=trabajo)
        diag.refresh_from_db()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4())
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk]); self.client.force_login(self.oficina)
        respuesta=self.client.get(self.url); form=respuesta.context['forms']['cotizar']
        self.assertEqual(len(form.filas),2)                                    # ni ocho filas vacías ni conceptos ficticios
        self.assertFalse(form.historica)
        self.assertContains(respuesta,'Reparación de eje'); self.assertNotContains(respuesta,'data-solicitud-historica')
        self.assertEqual(diag.conceptos.count(),2)

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_solicitud_historica_sin_conceptos_advierte_y_no_inventa(self):
        self.preparar()                                                        # solicitud enviada sin conceptos estructurados
        self.client.force_login(self.oficina)
        respuesta=self.client.get(self.url); form=respuesta.context['forms']['cotizar']
        self.assertTrue(form.historica); self.assertEqual(len(form.filas),0)
        self.assertContains(respuesta,'data-solicitud-historica')
        self.assertEqual(self.diag.conceptos.count(),0)

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_cotizar_componente_reutiliza_trabajo_sin_duplicar_y_va_al_pdf(self):
        import re
        from io import BytesIO
        from pypdf import PdfReader
        self._con_conceptos(); self.client.force_login(self.oficina)
        pieza=self.diag.piezas.get(); k=f'k{pieza.pk}'
        ids=[c['id'] for c in self.sol.snapshot['conceptos']]
        base={f'c{i}_precio':p for i,p in zip(ids,('100.00','250.00','4.00'))}
        respuesta=self.client.get(self.url); self.assertContains(respuesta,'COTIZAR'); self.assertContains(respuesta,f'data-cotizar-componente="{pieza.pk}"')
        # sin justificación o sin datos completos no se acepta una intervención inventada
        malo=self.client.post(self.url,dict(op='cotizar',clave=uuid.uuid4(),referencia='COT-COMP',**base,**{f'{k}_descripcion':'Reparación del componente',
            f'{k}_cantidad':'1',f'{k}_unidad':'hora',f'{k}_precio':'30.00'}))
        self.assertEqual(malo.status_code,400); self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),0)
        datos=dict(op='cotizar',clave=uuid.uuid4(),referencia='COT-COMP',**base,**{f'{k}_descripcion':'Reparación del componente',
            f'{k}_cantidad':'1',f'{k}_unidad':'hora',f'{k}_precio':'30.00',f'{k}_justificacion':'Desgaste visto en el diagnóstico'})
        self.assertEqual(self.client.post(self.url,datos).status_code,302)
        propuesta=PropuestaComercial.objects.get(expediente=self.exp)
        lineas=propuesta.snapshot['cotizacion']['lineas']
        asociadas=[l for l in lineas if l.get('componente')==pieza.pk]
        self.assertEqual(len(asociadas),1); self.assertEqual(len(lineas),4)
        self.assertTrue(asociadas[0]['requiere_revision_tecnica']); self.assertEqual(asociadas[0]['origen'],'OFICINA')
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'650.00')      # 620 de TALLER + 30; el repuesto no suma
        self.assertNotIn('precio_unitario',[k2 for k2 in self.sol.snapshot['piezas'][0]])
        # El formulario reutiliza el trabajo ya asociado: se rellena en lugar de crear otro.
        form=self.client.get(self.url).context['forms']['cotizar']
        comp=[c for c in form.componentes if c['pieza']['pieza']==pieza.pk][0]
        self.assertTrue(comp['abierto']); self.assertEqual(comp['descripcion'].value(),'Reparación del componente')
        # Un componente ajeno o duplicado se rechaza en el servicio.
        for malo in (dict(componente=999999),dict(componente=pieza.pk)):
            with self.assertRaises(ValidationError):
                s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='COT-DUP',cotizacion=dict(moneda='USD',
                    lineas=[dict(concepto=i,precio_unitario='1') for i in ids]+[dict(tipo='SERVICIO',descripcion='A',cantidad='1',unidad='u',precio_unitario='1',justificacion='J',componente=pieza.pk),
                        dict(tipo='SERVICIO',descripcion='B',cantidad='1',unidad='u',precio_unitario='1',justificacion='J',**malo)]))
        ev=s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk)
        with ev.documento.archivo.open('rb') as f:texto=chr(10).join(pg.extract_text() for pg in PdfReader(BytesIO(f.read())).pages)
        for esperado in ('Trabajo asociado al componente','Reparación del componente','650.00','REPUESTOS REQUERIDOS (no incluidos'):
            self.assertIn(esperado,texto)
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_conceptos_congelados_protegidos_y_oficina_solo_asigna_precios(self):
        from django.db import DatabaseError, transaction
        from .tecnica_models import ConceptoTecnico
        trabajos=self._con_conceptos()
        diag=self.diag
        concepto=diag.conceptos.first()
        # PostgreSQL impide modificar, eliminar o insertar conceptos ya congelados (aunque se eluda el servicio).
        for accion in (lambda: ConceptoTecnico.objects.filter(pk=concepto.pk).update(cantidad=99),
                lambda: ConceptoTecnico.objects.filter(pk=concepto.pk).update(unidad='kg'),
                lambda: ConceptoTecnico.objects.filter(pk=concepto.pk).delete(),
                lambda: ConceptoTecnico.objects.create(diagnostico=diag,tipo='SERVICIO',descripcion='Colado',cantidad=1,unidad='u'),
                lambda: diag.conceptos.all().delete()):
            with self.assertRaises(DatabaseError):
                with transaction.atomic(): accion()
        self.assertEqual(diag.conceptos.count(),3)
        concepto.refresh_from_db(); self.assertEqual(str(concepto.cantidad.normalize()),'1')
        # OFICINA (u otro rol) no registra ni retira conceptos técnicos.
        base=dict(usuario=self.oficina,empresa=self.empresa,diagnostico_id=diag.pk,version=diag.version_edicion,clave=uuid.uuid4())
        with self.assertRaises((PermissionDenied,ValidationError)): t.agregar_concepto(**base,datos=trabajos[0])
        with self.assertRaises((PermissionDenied,ValidationError)): t.quitar_concepto(**base,concepto_id=concepto.pk)
        self.client.force_login(self.oficina)
        for op in ('concepto','concepto_quitar'):
            respuesta=self.client.post(self.url,dict(op=op,clave=uuid.uuid4(),concepto=concepto.pk,version=diag.version_edicion,tipo='SERVICIO',
                descripcion='Colado',cantidad='1',unidad='u'))
            self.assertIn(respuesta.status_code,(400,403))
        self.assertEqual(diag.conceptos.count(),3)
        # En la cotización, OFICINA solo aporta precios: descripción, cantidad y unidad vienen de la versión enviada.
        ids=[c['id'] for c in self.sol.snapshot['conceptos']]
        manipulada=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p,descripcion='ALTERADA',cantidad='99',unidad='kg',tipo='CONSUMIBLE')
            for i,p in zip(ids,('100.00','250.00','4.00'))])
        propuesta=s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='COT-SEG-QA',cotizacion=manipulada)
        lineas=propuesta.snapshot['cotizacion']['lineas']
        self.assertEqual([(l['descripcion'],l['cantidad'],l['unidad'],l['tipo']) for l in lineas],
            [(x['descripcion'],x['cantidad'],x['unidad'],x['tipo']) for x in trabajos])
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'620.00')
        # La cotización corresponde a la versión técnica enviada (misma revisión y solicitud íntegra).
        self.assertEqual(propuesta.diagnostico_id,diag.pk)
        self.assertEqual(propuesta.snapshot['revision'],diag.revision)
        self.assertEqual(t._digest(self.sol.snapshot),self.sol.huella)

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_correccion_con_nueva_revision_conserva_historial_y_no_sobrescribe(self):
        self._con_conceptos()
        diag=self.diag; ids=[c['id'] for c in self.sol.snapshot['conceptos']]
        antigua=s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='COT-V1-QA',
            cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        nueva=t.crear_revision(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Corrección QA')
        self.assertNotEqual(nueva.pk,diag.pk)
        self.assertEqual(sorted(nueva.conceptos.values_list('descripcion',flat=True)),sorted(diag.conceptos.values_list('descripcion',flat=True)))
        self.assertEqual(diag.conceptos.count(),3)                      # la revisión anterior queda intacta
        self.assertNotEqual(sorted(nueva.conceptos.values_list('pk',flat=True)),sorted(diag.conceptos.values_list('pk',flat=True)))
        t.agregar_concepto(usuario=self.taller,empresa=self.empresa,diagnostico_id=nueva.pk,version=nueva.version_edicion,clave=uuid.uuid4(),
            datos=dict(tipo='SERVICIO',descripcion='Alineación láser',cantidad='1',unidad='servicio'))
        self.assertEqual(nueva.conceptos.count(),4); self.assertEqual(diag.conceptos.count(),3)
        antigua.refresh_from_db()
        self.assertEqual(antigua.diagnostico_id,diag.pk)                # no se sobrescribe la cotización histórica
        self.assertEqual(antigua.snapshot['cotizacion']['total'],'620.00')
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp,accion='DIAGNOSTICO_CREADA').exists())

    @override_settings(DEBUG=False,MASISCAM_OPERACION_LOCAL=False)
    def test_error_no_redirige_e_historial_conserva_versiones(self):
        self.preparar()
        response=self.client.post(self.url,dict(op='cotizar',clave=uuid.uuid4(),referencia='QA',
            x0_descripcion='Servicio QA',x0_cantidad='2',x0_unidad='servicio',x0_justificacion='Acordado QA',x0_precio='invalido'),follow=True)
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.redirect_chain,[])
        self.assertEqual(PropuestaComercial.objects.count(),0)
        primera=self.cotizar(archivo=None)
        doc=primera.evidencias.get().documento
        segunda=self.cotizar(archivo=None,cotizacion=self.detalle('15.00'))
        for user in (self.oficina,self.admin):
            self.client.force_login(user)
            page=self.client.get(self.url)
            self.assertContains(page,'data-quote-version="2" data-current="true"')
            self.assertContains(page,'data-quote-version="1" data-current="false"')
            self.assertContains(page,'Historial de cotizaciones y documentos anteriores')
            self.assertContains(page,'Ver PDF')
            self.assertContains(page,'Descargar PDF')
        self.assertEqual(PropuestaComercial.objects.count(),2)
        self.assertEqual(primera.evidencias.get().documento_id,doc.pk)
        self.assertNotEqual(segunda.evidencias.get().documento_id,doc.pk)

    def test_precios_factura_versiones_y_reintento(self):
        self.preparar();key=uuid.uuid4()
        propuesta=self.cotizar(clave=key)
        self.assertEqual(self.cotizar(clave=key).pk,propuesta.pk)
        quote=propuesta.snapshot['cotizacion']
        self.assertEqual(quote['total'],'24.70')
        self.assertEqual(quote['factura']['numero'],'FACTURA-REFERENCIA-QA')
        self.assertEqual(PropuestaComercial.objects.count(),1)
        self.aceptar(propuesta)
        anterior=propuesta.snapshot.copy()
        nueva=self.cotizar(cotizacion=self.detalle('13.00'))
        self.assertEqual(nueva.version,2);self.assertEqual(nueva.sustituye_id,propuesta.pk)
        propuesta.refresh_from_db();self.assertEqual(propuesta.snapshot,anterior)
        with self.assertRaises(ValidationError):s.autorizacion(self.sol)
        with transaction.atomic(),self.assertRaises(IntegrityError):
            PropuestaComercial.objects.filter(pk=propuesta.pk).update(snapshot={})

    def test_cotizacion_opcional_pdf_y_guardas_servidor(self):
        self.preparar()
        for detail in (self.detalle('NaN'),self.detalle('-1'),self.detalle('1.001'),
                dict(self.detalle(),lineas=[]),dict(self.detalle(),lineas=[dict(pieza=99999,precio_unitario='1')])):
            with self.assertRaises(ValidationError):self.cotizar(cotizacion=detail)
        propuesta=self.cotizar(archivo=None)
        self.assertEqual(propuesta.evidencias.count(),1)
        with self.assertRaises(ValidationError):s.autorizacion(self.sol)
        for user in (self.taller,self.admin,self.otro):
            with self.assertRaises(PermissionDenied):self.cotizar(usuario=user)
        response=self.client.get(self.url)
        self.assertContains(response,'Pendiente de aprobación');self.assertNotContains(response,'Recepción y documentos')
        self.assertNotContains(response,'<footer class="mf-links">')

    def test_repuestos_del_cliente_sin_compra_ni_envio_y_reparacion_exige_recepcion(self):
        self.preparar();propuesta=self.cotizar()
        self.aceptar(propuesta)
        self.assertFalse(self.client.get(self.url).context['oficina_envio'])
        self.assertEqual(s.autorizacion(self.sol).propuesta_id,propuesta.pk)
        response=self.client.get(self.url)
        for retirado in ('Registrar repuestos disponibles','Enviar repuestos a TALLER','oficina-enviar','oficina-disponibles'):
            self.assertNotContains(response,retirado)
        for op in ('adquirir','enviar'):   # acciones retiradas del flujo activo
            self.assertEqual(self.client.post(self.url,dict(op=op,clave=uuid.uuid4())).status_code,400)
        self.assertEqual(EnvioRepuestos.objects.count(),0)
        accion=s.resumen(self.exp,self.oficina)['accion']
        self.assertIn('/operacion/accion/repuesto/',accion['url'])
        self.client.force_login(self.taller)
        with self.assertRaises(ValidationError):   # sin recepción/disponibilidad registrada no se repara
            o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
                datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        o.recibir_repuesto(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(pieza_id=self.pieza.pk,procedencia='Cliente',entregado_por='Cliente QA',numero_parte=self.pieza.numero_parte,
                descripcion=self.pieza.descripcion,cantidad=str(self.pieza.cantidad),unidad=self.pieza.unidad,fecha=timezone.now(),guia='',discrepancia=''))
        self.assertEqual(RecepcionRepuesto.objects.count(),1)
        self.assertEqual(EnvioRepuestos.objects.count(),0)
        self.assertEqual(AdquisicionRepuesto.objects.count(),0)
        o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))

    test_reintentos_http=qa_flujo.FlujoTests.test_reintentos_http_no_duplican_solicitud_y_no_hay_compra_ni_envio
    test_sin_autorizacion=qa_flujo.FlujoTests.test_compras_y_reparaciones_no_se_crean_sin_autorizacion
    test_sin_repuestos=qa_flujo.FlujoTests.test_sin_repuestos_no_obliga_a_inventar_piezas_compras_o_envios
    test_recepcion_politica_retirada=qa_flujo.FlujoTests.test_recepcion_fisica_con_politica_retirada_no_autoriza_reparar
    def test_aceptacion_parcial(self):
        qa_flujo.FlujoTests.test_aceptacion_parcial_no_autoriza_comprar_el_listado_completo(self)
        parcial=PropuestaComercial.objects.filter(snapshot__has_key='propuesta_origen').get()
        with self.assertRaises(ValidationError):
            s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=parcial.pk)
    def test_multiempresa(self):
        from accounts.models import Empresa
        from .test_tecnica import usuario
        self.preparar()
        otra=Empresa.objects.create(nombre='Empresa ajena QA')
        ajena=usuario(otra,'oficina-ajena-gestion','OFICINA')
        self.client.force_login(ajena)
        self.assertEqual(self.client.get(self.url).status_code,403)
        with self.assertRaises(PermissionDenied):self.cotizar(usuario=ajena,empresa=otra)
        self.client.force_login(self.taller)
        self.assertEqual(self.client.post(self.url,dict(op='cotizar',clave=uuid.uuid4(),referencia='QA',moneda='USD',
            x0_descripcion='Servicio QA',x0_cantidad='2',x0_unidad='servicio',x0_justificacion='Acordado QA',x0_precio='1')).status_code,403)

    def test_pdf_versionado_integridad_y_permisos(self):
        from io import BytesIO
        from pathlib import Path
        from pypdf import PdfReader
        from accounts.models import Empresa
        from .test_tecnica import usuario
        self.preparar()
        propuesta=self.cotizar(archivo=None,observaciones='Observaciones de prueba',
            cotizacion=dict(self.detalle(),condiciones='Entrega acordada con el cliente.'))
        ev=s.generar_cotizacion_pdf(usuario=self.oficina,empresa=self.empresa,propuesta_id=propuesta.pk)
        self.assertEqual(propuesta.evidencias.count(),1)
        doc=ev.documento
        with doc.archivo.open('rb') as archivo:contenido=archivo.read()
        pdf=PdfReader(BytesIO(contenido));text='\n'.join(page.extract_text() for page in pdf.pages)
        for esperado in ('COTIZACION-QA','24.70','12.35','USD',self.exp.numero,
                propuesta.snapshot['cliente']['ruc'],'Entrega acordada','ACEPTACIÓN DEL CLIENTE'):
            self.assertIn(esperado,text)
        self.assertTrue(pdf.pages[0].images)
        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
            (Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'cotizacion-v1.pdf').write_bytes(contenido)
        original_hash=doc.hash_sha256
        nueva=self.cotizar(archivo=None,cotizacion=self.detalle('15.00'))
        self.assertEqual(nueva.version,2)
        self.assertEqual(nueva.evidencias.count(),1)
        self.assertNotEqual(nueva.evidencias.get().documento_id,doc.pk)
        doc.refresh_from_db();self.assertEqual(doc.hash_sha256,original_hash)
        t.documento_valido(doc,self.empresa)
        url=reverse('masiscam:operacion_documento',args=[self.exp.pk,doc.pk])
        for suffix,disposition in (('','attachment'),('?ver=1','inline')):
            response=self.client.get(url+suffix)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response['Content-Disposition'].startswith(disposition))
            self.assertEqual(response['Content-Type'],'application/pdf')
            self.assertEqual(b''.join(response.streaming_content),contenido)
        historica=o.proponer(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,
            diagnostico_id=self.diag.pk,clave=uuid.uuid4(),datos=dict(alcance='Alcance existente de prueba',
                presupuesto_referencia='COT-HISTORICA-QA',cotizacion=self.detalle()))
        self.assertFalse(historica.evidencias.exists())
        self.assertContains(self.client.get(self.url),'Generar PDF de esta versión')
        payload=dict(op='pdf_cotizacion',propuesta_id=historica.pk,clave=uuid.uuid4())
        for _ in range(2):self.assertEqual(self.client.post(self.url,payload).status_code,302)
        self.assertEqual(historica.evidencias.count(),1)
        self.assertEqual(PropuestaComercial.objects.count(),3)
        for user in (self.taller,usuario(Empresa.objects.create(nombre='Ajena PDF'),'oficina-pdf-ajena','OFICINA')):
            self.client.force_login(user)
            self.assertIn(self.client.get(url).status_code,(403,404))
            with self.assertRaises(PermissionDenied):
                s.generar_cotizacion_pdf(usuario=user,empresa=self.empresa,propuesta_id=propuesta.pk)

    def test_usd_fijo_calculos_y_condiciones_invalidan_aceptacion(self):
        from .flujo_forms import CotizacionForm
        self.preparar()
        self.assertNotIn('moneda',CotizacionForm(solicitud=self.sol).fields)
        with self.assertRaises(ValidationError):self.cotizar(cotizacion=dict(self.detalle(),moneda='EUR'))
        propuesta=self.cotizar(archivo=None)
        self.aceptar(propuesta)
        nueva=self.cotizar(archivo=None,cotizacion=dict(self.detalle(),condiciones='Nueva condición explícita'))
        self.assertIn('Nueva condición explícita',nueva.alcance)
        with self.assertRaises(ValidationError):s.autorizacion(self.sol)
        self.assertEqual(nueva.snapshot['cotizacion']['total'],'24.70')

    def test_pdf_extenso_conserva_detalle_y_paginas(self):
        from io import BytesIO
        from pathlib import Path
        from pypdf import PdfReader
        from .flujo_pdf import cotizacion_comercial
        contenido=cotizacion_comercial(dict(referencia='COTIZACION-EXTENSA-QA',version=1,fecha='14/11/2026',
            cliente=dict(cliente='Cliente ficticio de prueba',ruc='RUC-QA',equipo='DERAN',serie='SERIE-QA'),expediente='EXP-QA',
            lineas=[dict(tipo='SERVICIO',descripcion=f'Servicio QA {i} '*5,cantidad='2',unidad='servicio',precio_unitario='12.35',total='24.70') for i in range(1,36)],
            repuestos=[dict(posicion=str(i),codigo=f'COD-QA-{i}',descripcion='Componente registrado por TALLER. '*3,cantidad='2',unidad='unidad',compatibilidad='Plano QA r2') for i in range(1,36)],
            total='864.50',observaciones='Observaciones de prueba. '*30,condiciones='Condiciones aportadas por OFICINA. '*20))
        pdf=PdfReader(BytesIO(contenido))
        self.assertGreater(len(pdf.pages),1)
        text='\n'.join(page.extract_text() for page in pdf.pages)
        for value in ('COD-QA-35','864.50','ACEPTACIÓN DEL CLIENTE'):self.assertIn(value,text)
        for i,page in enumerate(pdf.pages,1):self.assertIn(f'Página {i}',page.extract_text())
        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
            (Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'cotizacion-extensa.pdf').write_bytes(contenido)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador aislado explícito')
@override_settings(**FLAGS)
class OficinaGestionBrowserTests(OficinaFixture,StaticLiveServerTestCase):
    def _galleta(self,usuario):
        client=Client();client.force_login(usuario)
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def _con_conceptos(self):
        return OficinaGestionTests._con_conceptos(self)

    @override_settings(**FLAGS)
    def test_taller_ve_en_el_mismo_expediente_cada_cambio_de_estado(self):
        from playwright.sync_api import sync_playwright, expect
        OficinaGestionTests.preparar_solicitud(self,enviar=True)
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        prop=OficinaGestionTests.cotizar(self,archivo=None)
        galleta=self._galleta(self.taller)
        pagina=self.live_server_url+reverse('masiscam:taller_equipo',args=[self.exp.registro.equipo_id])+f'?expediente={self.exp.pk}'
        flujo_url=self.live_server_url+reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        def ver(texto,botones=(),paso3=None,cookie=None,destino=None):
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True)
                try:
                    for ancho in (1440,390):
                        context=browser.new_context(viewport=dict(width=ancho,height=1000))
                        context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie or galleta,url=self.live_server_url)])
                        page=context.new_page(); page.goto(destino or pagina)
                        expect(page.locator('body')).to_contain_text(texto)
                        for boton in botones: expect(page.get_by_role('link',name=boton).or_(page.get_by_role('button',name=boton)).first).to_be_visible()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'desborda a {ancho}px')
                        if paso3:   # stepper de 5 etapas con chips de estado y color consistente
                            page.goto(flujo_url)
                            expect(page.locator('.mf-steps > li')).to_have_count(5)
                            tercero=page.locator('.mf-steps > li').nth(2)
                            expect(tercero).to_have_attribute('data-estado',paso3[0]); expect(tercero).to_have_class(re.compile(paso3[1]))
                            expect(tercero.locator('.mf-chip')).to_have_text(paso3[0])
                            if paso3[0]=='Aprobado':
                                expect(page.locator('[data-aprobado-admin]')).to_contain_text('Aprobado por ADMINISTRADOR')
                                expect(page.locator('body')).not_to_contain_text('aceptación del cliente')
                                color=tercero.locator('.mf-number').evaluate('e=>getComputedStyle(e).backgroundColor')
                                self.assertEqual(color,'rgb(47, 143, 85)')   # verde = aprobado
                            expect(page.locator('.mf-next .mf-main-action').first).to_be_visible() if page.locator('.mf-next .mf-main-action').count() else None
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'desborda a {ancho}px (flujo)')
                        context.close()
                finally:
                    browser.close()
        ver('Tu diagnóstico está en revisión',paso3=('Bloqueado','is-bloqueado'))
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        ver('Aprobado por ADMINISTRADOR',botones=('Registrar repuestos del cliente',),paso3=('Aprobado','is-ok'))
        # ADMINISTRADOR y OFICINA ven el mismo stepper y los mismos chips que TALLER.
        ver('Aprobado por ADMINISTRADOR',paso3=('Aprobado','is-ok'),cookie=self._galleta(self.admin),destino=flujo_url)
        ver('Aprobado por ADMINISTRADOR',paso3=('Aprobado','is-ok'),cookie=self._galleta(self.oficina),destino=flujo_url)   # sin aceptación del cliente ni política JSON
        pieza=self.diag.piezas.get()
        o.recibir_repuesto(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(pieza_id=pieza.pk,procedencia='Cliente',entregado_por='Cliente QA',numero_parte=pieza.numero_parte,
                descripcion=pieza.descripcion,cantidad=str(pieza.cantidad),unidad=pieza.unidad,fecha=timezone.now(),guia='',discrepancia=''))
        ver('Registra la reparación',botones=('Iniciar reparación',))
        from .models import ExpedienteMantenimiento
        self.assertEqual(ExpedienteMantenimiento.objects.filter(registro__equipo=self.exp.registro.equipo).count(),1)

    @override_settings(MASISCAM_OPERACION_LOCAL=False,MASISCAM_CALIBRACION_LOCAL=False,MASISCAM_CIERRE_LOCAL=False)
    def test_guardar_modificar_guardar_doble_clic_y_reintento_de_red_sin_conflicto(self):
        from playwright.sync_api import sync_playwright, expect
        from .models import Diagnostico
        OficinaGestionTests.preparar_solicitud(self,enviar=False)
        url=self.live_server_url+reverse('masiscam:flujo_detalle',args=[self.exp.pk]); galleta=self._galleta(self.taller)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for ancho in (1440,390):
                    context=browser.new_context(viewport=dict(width=ancho,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galleta,url=self.live_server_url)])
                    page=context.new_page(); page.goto(url)
                    form=page.locator('#mf-diagnostico')
                    aviso=page.locator('body')
                    # Guardar, modificar y volver a guardar sin recargar a mano.
                    for texto in (f'Primera edición {ancho}',f'Segunda edición {ancho}'):
                        form.locator('[name=fallas]').fill(texto)
                        form.get_by_role('button',name='Guardar avance').click()
                        expect(form.locator('[name=fallas]')).to_have_value(texto)
                        expect(aviso).not_to_contain_text('Conflicto')
                    # Reintento de red: el primer envío se pierde, el segundo conserva la clave y funciona.
                    form.locator('[name=fallas]').fill(f'Tras fallo de red {ancho}')
                    perdidas=[]
                    def cortar(route):
                        if not perdidas: perdidas.append(1); route.abort()
                        else: route.continue_()
                    page.route('**/flujo/',cortar)
                    form.get_by_role('button',name='Guardar avance').click()
                    page.wait_for_timeout(500)
                    page.unroute('**/flujo/')
                    page.goto(url); form=page.locator('#mf-diagnostico')
                    # Doble clic: una sola operación efectiva y sin mensaje de conflicto.
                    form.locator('[name=fallas]').fill(f'Doble clic {ancho}')
                    form.get_by_role('button',name='Guardar avance').dblclick()
                    expect(form.locator('[name=fallas]')).to_have_value(f'Doble clic {ancho}')
                    expect(page.locator('body')).not_to_contain_text('Conflicto')
                    context.close()
            finally:
                browser.close()
        self.assertEqual(Diagnostico.objects.get(pk=self.diag.pk).fallas,'Doble clic 390')
        self.assertEqual(Diagnostico.objects.filter(expediente=self.exp).count(),1)

    @override_settings(MASISCAM_OPERACION_LOCAL=False,MASISCAM_CALIBRACION_LOCAL=False,MASISCAM_CIERRE_LOCAL=False)
    def test_boton_cotizar_en_componente_asocia_trabajo_y_actualiza_total(self):
        from playwright.sync_api import sync_playwright, expect
        self._con_conceptos()
        galleta=self._galleta(self.oficina); pieza_id=self.diag.piezas.get().pk
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for ancho in (1440,390):
                    context=browser.new_context(viewport=dict(width=ancho,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galleta,url=self.live_server_url)])
                    page=context.new_page();page.goto(self.live_server_url+self.url)
                    quote=page.locator('#oficina-cotizacion')
                    expect(quote.locator('tr[data-concepto]')).to_have_count(3)
                    for fila,precio in zip(quote.locator('tr[data-concepto]').all(),('100.00','250.00','4.00')):
                        fila.locator('input[name$=_precio]').fill(precio)
                    expect(quote.locator(f'[data-componente-form="{pieza_id}"]')).to_be_hidden()
                    quote.locator(f'[data-cotizar-componente="{pieza_id}"]').click()
                    bloque=quote.locator(f'[data-componente-form="{pieza_id}"]'); expect(bloque).to_be_visible()
                    k=f'k{pieza_id}'
                    quote.locator(f'[name={k}_descripcion]').fill('Reparación del componente'); quote.locator(f'[name={k}_cantidad]').fill('1')
                    quote.locator(f'[name={k}_unidad]').fill('hora'); quote.locator(f'[name={k}_precio]').fill('30.00')
                    expect(page.locator('#oficina-total')).to_have_text('650.00')       # el repuesto no suma; solo el trabajo asociado
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'desborda a {ancho}px')
                    context.close()
            finally:
                browser.close()

    @override_settings(MASISCAM_OPERACION_LOCAL=False,MASISCAM_CALIBRACION_LOCAL=False,MASISCAM_CIERRE_LOCAL=False)
    def test_servicio_adicional_con_justificacion_y_aviso_de_nueva_revision(self):
        from playwright.sync_api import sync_playwright, expect
        self._con_conceptos()
        galletas={n:self._galleta(u) for n,u in (('OFICINA',self.oficina),('ADMIN',self.admin))}
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galletas['OFICINA'],url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.url)
                quote=page.locator('#oficina-cotizacion')
                expect(quote.locator('tr[data-concepto]')).to_have_count(3)
                for fila,precio in zip(quote.locator('tr[data-concepto]').all(),('100.00','250.00','4.00')):
                    fila.locator('input[name$=_precio]').fill(precio)
                quote.get_by_text('Agregar un servicio no registrado por TALLER').click()
                quote.locator('[name=referencia]').fill('COT-ADICIONAL-UI')
                quote.locator('[name=x0_descripcion]').fill('Alineación láser')
                quote.locator('[name=x0_cantidad]').fill('1');quote.locator('[name=x0_unidad]').fill('servicio');quote.locator('[name=x0_precio]').fill('50.00')
                quote.get_by_role('button',name='Guardar cotización y enviar a ADMINISTRADOR',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.locator('.errorlist').first).to_contain_text('justificación')   # sin justificación no se acepta
                if page.locator('.swal2-popup').count():
                    page.keyboard.press('Escape');expect(page.locator('.swal2-popup')).to_have_count(0)   # cierra el aviso de error
                quote=page.locator('#oficina-cotizacion')
                quote.locator('details').first.evaluate('d=>d.open=true')
                quote.locator('[name=x0_justificacion]').fill('Se detectó desalineación durante la prueba de arranque')
                for fila,precio in zip(quote.locator('tr[data-concepto]').all(),('100.00','250.00','4.00')):
                    fila.locator('input[name$=_precio]').fill(precio)
                quote.locator('[name=x0_descripcion]').fill('Alineación láser');quote.locator('[name=x0_cantidad]').fill('1')
                quote.locator('[name=x0_unidad]').fill('servicio');quote.locator('[name=x0_precio]').fill('50.00')
                quote.locator('[name=referencia]').fill('COT-ADICIONAL-UI')
                expect(page.locator('#oficina-total')).to_have_text('670.00')
                quote.get_by_role('button',name='Guardar cotización y enviar a ADMINISTRADOR',exact=True).click()
                confirm=page.get_by_role('button',name='Sí, continuar',exact=True)
                if confirm.count(): confirm.click()
                expect(page).to_have_url(self.live_server_url+reverse('masiscam:oficina_inicio'))
                context.close()
            finally:
                browser.close()
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)   # el intento sin justificación no guardó nada
        propuesta=PropuestaComercial.objects.get(expediente=self.exp)
        lineas=propuesta.snapshot['cotizacion']['lineas']
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'670.00')
        adicional=[l for l in lineas if l['origen']=='OFICINA']
        self.assertEqual(len(adicional),1)
        self.assertTrue(adicional[0]['requiere_revision_tecnica'] and adicional[0]['justificacion'])
        self.assertEqual(len([l for l in lineas if l['origen']=='TALLER']),3)
        # ADMINISTRADOR ve el adicional marcado para su revisión.
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=390,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galletas['ADMIN'],url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
                expect(page.locator('[data-servicios-adicionales]')).to_contain_text('Alineación láser')
                expect(page.locator('[data-servicios-adicionales]')).to_contain_text('desalineación')
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                context.close()
            finally:
                browser.close()
        # TALLER corrige mediante una nueva revisión: OFICINA recibe el aviso y la cotización histórica queda intacta.
        nueva=t.crear_revision(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Corrección QA')
        nueva.refresh_from_db()
        nueva_sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=nueva.pk,version=nueva.version_edicion,clave=uuid.uuid4())
        self.assertEqual(nueva_sol.diagnostico_id,nueva.pk)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for ancho in (1440,390):
                    context=browser.new_context(viewport=dict(width=ancho,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galletas['OFICINA'],url=self.live_server_url)])
                    page=context.new_page();page.goto(self.live_server_url+self.url)
                    expect(page.locator('[data-cotizacion-otra-revision]')).to_contain_text('otra revisión')
                    expect(page.locator('#oficina-cotizacion tr[data-concepto]')).to_have_count(3)
                    expect(page.locator('#oficina-cotizacion input[name$=_precio]').first).to_have_value('')   # no hereda precios de otra revisión
                    context.close()
            finally:
                browser.close()
        propuesta.refresh_from_db()
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'670.00')

    @override_settings(MASISCAM_OPERACION_LOCAL=False,MASISCAM_CALIBRACION_LOCAL=False,MASISCAM_CIERRE_LOCAL=False)
    def test_taller_registra_conceptos_y_oficina_los_recibe_para_cotizar(self):
        from playwright.sync_api import sync_playwright, expect
        from io import BytesIO
        from pypdf import PdfReader
        self.preparar_solicitud(enviar=False)
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        galletas={n:self._galleta(u) for n,u in (('TALLER',self.taller),('OFICINA',self.oficina))}
        trabajos=[('MANTENIMIENTO','Mantenimiento preventivo del reductor','1','servicio'),
            ('REPARACION','Reparación de eje y rodamientos','2','hora'),('CONSUMIBLE','Aceite ISO VG 220','5','L')]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galletas['TALLER'],url=self.live_server_url)])
                page=context.new_page()
                for tipo,descripcion,cantidad,unidad in trabajos:
                    page.goto(self.live_server_url+self.url)
                    form=page.locator('#mf-concepto-form')
                    form.locator('[name=tipo]').select_option(tipo)
                    form.locator('[name=descripcion]').fill(descripcion)
                    form.locator('[name=cantidad]').fill(cantidad)
                    form.locator('[name=unidad]').fill(unidad)
                    form.get_by_role('button',name='Agregar a la cotización').click()
                    expect(page.locator('#mf-conceptos')).to_contain_text(descripcion)
                expect(page.locator('[data-concepto-registrado]')).to_have_count(3)
                context.close()
            finally:
                browser.close()
        self.assertEqual(self.diag.conceptos.count(),3)
        self.diag.refresh_from_db()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,version=self.diag.version_edicion,clave=uuid.uuid4())
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for ancho in (1440,390):
                    context=browser.new_context(viewport=dict(width=ancho,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galletas['OFICINA'],url=self.live_server_url)])
                    page=context.new_page();page.goto(self.live_server_url+self.url)
                    quote=page.locator('#oficina-cotizacion')
                    expect(quote.locator('tr[data-concepto]')).to_have_count(3)    # sin filas vacías
                    for _,descripcion,_,_ in trabajos:
                        expect(quote).to_contain_text(descripcion)
                    expect(page.locator('#oficina-repuestos-titulo')).to_be_visible()
                    expect(page.get_by_text('Componente solicitado QA').first).to_be_visible()
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'desborda a {ancho}px')
                    if ancho==1440:
                        for fila,precio in zip(quote.locator('tr[data-concepto]').all(),('100.00','250.00','4.00')):
                            fila.locator('input[name$=_precio]').fill(precio)
                        expect(page.locator('#oficina-total')).to_have_text('620.00')
                        quote.locator('[name=referencia]').fill('COT-DIAGNOSTICO-UI')
                        quote.get_by_role('button',name='Guardar cotización y enviar a ADMINISTRADOR',exact=True).click()
                        page.get_by_role('button',name='Sí, continuar',exact=True).click()
                        expect(page).to_have_url(self.live_server_url+reverse('masiscam:oficina_inicio'))
                    context.close()
            finally:
                browser.close()
        propuesta=PropuestaComercial.objects.get(expediente=self.exp)
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'620.00')
        self.assertEqual(len(propuesta.snapshot['cotizacion']['lineas']),3)
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)
        ev=propuesta.evidencias.filter(clave=uuid.uuid5(propuesta.clave,'cotizacion-pdf-v1')).get()
        with ev.documento.archivo.open('rb') as f:texto=chr(10).join(pg.extract_text() for pg in PdfReader(BytesIO(f.read())).pages)
        for esperado in ('DIAGNÓSTICO TÉCNICO','Aceite ISO VG 220','620.00','REPUESTOS REQUERIDOS'):
            self.assertIn(esperado,texto)

    @override_settings(MASISCAM_OPERACION_LOCAL=False,MASISCAM_CALIBRACION_LOCAL=False,MASISCAM_CIERRE_LOCAL=False)
    def test_envio_pdf_privado_y_tarjetas_escritorio_movil(self):
        from playwright.sync_api import sync_playwright, expect
        import hashlib
        self.preparar()
        cookies={}
        for name,user in [('OFICINA',self.oficina),('ADMIN',self.admin)]:
            client=Client();client.force_login(user)
            cookies[name]=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies['OFICINA'],url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.url)
                quote=page.locator('#oficina-cotizacion')
                quote.locator('[name=referencia]').fill('COT-NAVEGADOR-QA')
                quote.locator('[name=x0_descripcion]').fill('Servicio QA');quote.locator('[name=x0_cantidad]').fill('2');quote.locator('[name=x0_unidad]').fill('servicio');quote.locator('[name=x0_justificacion]').fill('Acordado QA');quote.locator('[name=x0_precio]').fill('12.35')
                quote.get_by_role('button',name='Guardar cotización y enviar a ADMINISTRADOR',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page).to_have_url(self.live_server_url+reverse('masiscam:oficina_inicio'))
                expect(page.locator('.swal2-popup')).to_contain_text('Cotización enviada correctamente. Pendiente de aprobación del ADMINISTRADOR')
                expect(page.locator('[data-estado-revision]')).to_have_text('Pendiente de aprobación')
                context.close()
            finally:
                browser.close()
        propuesta=PropuestaComercial.objects.get(expediente=self.exp)
        doc=propuesta.evidencias.get().documento
        doc_path=reverse('masiscam:flujo_documento',args=[self.exp.pk,doc.pk])
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for role in ('OFICINA','ADMIN'):
                    for width in (1440,390):
                        context=browser.new_context(viewport=dict(width=width,height=1000))
                        context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies[role],url=self.live_server_url)])
                        page=context.new_page();page.goto(self.live_server_url+self.url)
                        expect(page.locator('[data-pdf-preview]')).to_have_attribute('data-ready','true',timeout=20000)
                        expect(page.locator('[data-current=true]')).to_be_visible()
                        expect(page.get_by_role('link',name='Ver PDF',exact=True)).to_be_visible()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        response=context.request.get(self.live_server_url+doc_path+'?ver=1')
                        self.assertEqual(response.status,200)
                        self.assertTrue(response.headers['content-disposition'].startswith('inline'))
                        self.assertEqual(response.headers['content-type'],'application/pdf')
                        self.assertEqual(hashlib.sha256(response.body()).hexdigest(),doc.hash_sha256)
                        with page.expect_download() as saved:
                            page.get_by_role('link',name='Descargar PDF',exact=True).click()
                        self.assertEqual(hashlib.sha256(Path(saved.value.path()).read_bytes()).hexdigest(),doc.hash_sha256)
                        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                            output=Path(os.environ['MASISCAM_UI_EVIDENCE_DIR']);output.mkdir(parents=True,exist_ok=True)
                            page.screenshot(path=str(output/f'cotizacion-{role.lower()}-{width}.png'),full_page=True)
                        if role=='ADMIN' and width==390:
                            page.route(self.live_server_url+doc_path+'?ver=1',lambda route:route.fulfill(status=503,body='QA archivo ausente'))
                            page.reload()
                            expect(page.locator('[data-pdf-preview]')).to_have_attribute('data-ready','error')
                            expect(page.get_by_role('link',name='Ver PDF',exact=True)).to_be_visible()
                        context.close()
            finally:
                browser.close()
        self.assertEqual(PropuestaComercial.objects.filter(expediente=self.exp).count(),1)
        self.assertEqual(propuesta.evidencias.count(),1)

    def test_devolucion_con_motivo_sweetalert_y_destinatario(self):
        from playwright.sync_api import sync_playwright,expect
        self.preparar(aprobar_tecnico=False);self.cotizar(archivo=None)
        admin_client=Client();admin_client.force_login(self.admin)
        admin_cookie=admin_client.cookies[settings.SESSION_COOKIE_NAME].value
        oficina_cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=admin_cookie,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.url)
                expect(page.locator('#pl-preview')).to_have_attribute('data-ready','true')
                for width in (390,1440):
                    page.set_viewport_size(dict(width=width,height=1000))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                        from pathlib import Path
                        page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/f'admin-revision-{width}.png'),full_page=True)
                page.get_by_role('button',name='Devolver para corrección',exact=True).click()
                page.get_by_role('button',name='Devolver expediente',exact=True).click()
                expect(page.locator('.swal2-validation-message')).to_be_visible()
                page.locator('.swal2-textarea').fill('Corregir condiciones comerciales QA')
                page.locator('#revision-destino').select_option('OFICINA')
                page.get_by_role('button',name='Devolver expediente',exact=True).click()
                expect(page.get_by_text('Devuelto para corrección',exact=True)).to_be_visible()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=oficina_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
                expect(page.locator('[data-devolucion-expediente]')).to_contain_text('Corregir condiciones comerciales QA')
            finally:browser.close()

    def test_cotizar_factura_autorizar_enviar_y_recibir(self):
        from playwright.sync_api import sync_playwright, expect
        self.preparar_solicitud(enviar=False)
        self.pieza=self.diag.piezas.get()
        self.sol=s.enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4(),preliminar=True)
        self.url=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        self.client.force_login(self.oficina)
        admin_client=Client();admin_client.force_login(self.admin)
        admin_cookie=admin_client.cookies[settings.SESSION_COOKIE_NAME].value
        oficina_cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        taller_client=Client();taller_client.force_login(self.taller)
        taller_cookie=taller_client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.url)
                expect(page.locator('#oficina-estado')).to_have_text('Pendiente')
                expect(page.get_by_role('link',name='Recepción y documentos',exact=True)).to_have_count(0)
                expect(page.locator('footer.mf-links')).to_have_count(0)
                expect(page.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True)).to_have_count(0)
                quote=page.locator('#oficina-cotizacion')
                expect(page.locator('[data-diagnostico-pendiente]')).to_be_visible()
                quote.locator('[name=x0_descripcion]').fill('Servicio QA');quote.locator('[name=x0_cantidad]').fill('2');quote.locator('[name=x0_unidad]').fill('servicio');quote.locator('[name=x0_justificacion]').fill('Acordado QA');quote.locator('[name=x0_precio]').fill('12.35')
                expect(page.locator('#oficina-total')).to_have_text('24.70')
                quote.locator('[name=referencia]').fill('COTIZACION-UI-QA')
                expect(quote.locator('[name=moneda]')).to_have_count(0)
                expect(quote.locator('[data-total-general]')).to_have_text('24.70')
                quote.locator('[name=factura_numero]').fill('FACTURA-UI-QA')
                quote.locator('[name=factura_fecha]').fill('2026-11-14')
                pdf=presupuesto_qa()
                quote.locator('[name=archivo]').set_input_files(dict(name=pdf.name,mimeType='application/pdf',buffer=pdf.read()))
                quote.get_by_role('button',name='Guardar cotización y enviar a ADMINISTRADOR',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page).to_have_url(self.live_server_url+reverse('masiscam:oficina_inicio'))
                page.goto(self.live_server_url+self.url)
                expect(page.locator('#oficina-estado')).to_have_text('Pendiente de aprobación')
                expect(page.locator('[name=factura_numero]')).to_have_value('FACTURA-UI-QA')
                documents=page.locator('#oficina-documentos')
                expect(documents.get_by_text('Cotización MASISCAM v1',exact=False)).to_be_visible()
                for name,disposition in (('Ver PDF','inline'),('Descargar PDF','attachment')):
                    link=documents.get_by_role('link',name=name,exact=True).last
                    response=context.request.get(self.live_server_url+link.get_attribute('href'))
                    self.assertEqual(response.status,200)
                    self.assertTrue(response.headers['content-disposition'].startswith(disposition))
                    self.assertTrue(response.body().startswith(b'%PDF-'))
                expect(page.locator('#oficina-aceptacion')).to_have_count(0)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=admin_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
                expect(page.get_by_role('button',name='Aprobar expediente',exact=True)).to_be_enabled()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=taller_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
                expect(page.locator('#informacion-tecnica')).to_be_visible()  # el formulario unificado ya no es un desplegable
                page.get_by_role('button',name='Validar diagnóstico con ADMINISTRADOR',exact=True).click()
                confirm=page.get_by_role('button',name='Sí, continuar',exact=True)
                if confirm.count():confirm.click()
                expect(page.locator('#mf-diagnostico')).to_have_count(0)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=admin_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
                expect(page.locator('#revision-expediente')).to_be_visible()
                expect(page.locator('#pl-preview')).to_have_attribute('data-ready','true')
                expect(page.get_by_role('button',name='Registrar asignación',exact=True)).to_have_count(0)
                expect(page.get_by_role('button',name='Aprobar expediente',exact=True)).to_be_enabled()
                page.get_by_role('button',name='Aprobar expediente',exact=True).click()
                page.get_by_role('button',name='Sí, continuar',exact=True).click()
                expect(page.get_by_text('Expediente aprobado',exact=True)).to_be_visible()
                if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                    from pathlib import Path
                    page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'administrador-expediente.png'),full_page=True)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=oficina_cookie,url=self.live_server_url)])
                page.goto(self.live_server_url+self.url)
                expect(page.locator('#oficina-aceptacion')).to_have_count(0)   # ya no se registra una aceptación del cliente
                expect(page.locator('#oficina-estado')).to_have_text('Autorizado')
                expect(page.get_by_role('button',name='Aprobar y enviar a TALLER',exact=True)).to_have_count(0)
                for retirado in ('#oficina-disponibles','#oficina-enviar'):
                    expect(page.locator(retirado)).to_have_count(0)
                expect(page.get_by_text('Registrar repuestos disponibles')).to_have_count(0)
                for width in (390,1440):
                    page.set_viewport_size(dict(width=width,height=1000))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            finally:browser.close()
        propuesta=PropuestaComercial.objects.get(expediente=self.exp)
        self.assertEqual(propuesta.snapshot['cotizacion']['total'],'24.70')
        self.assertEqual(EnvioRepuestos.objects.filter(expediente=self.exp).count(),0)   # MASISCAM no envía repuestos
        self.assertEqual(RecepcionRepuesto.objects.filter(expediente=self.exp).count(),0)   # aún no se registran repuestos del cliente

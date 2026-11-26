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
        from .models import ExpedienteMantenimiento, RevisionAdministrativa, DecisionComercial, RevisionReparacion
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
        r=taller(); self.assertEqual(r['accion']['texto'],'Registrar mantenimiento'); self.assertNotIn('aceptación del cliente',r['mensaje'].lower())
        self.assertNotIn('repuestos',r['mensaje'].lower())   # TALLER no registra repuestos: la lista del diagnóstico es informativa
        self.assertEqual([(e['texto'],e['estado']) for e in r['secuencia']],[('Registrar mantenimiento','Disponible'),
            ('Registrar calibración','No disponible'),('Generar informe final','No disponible')])
        self.assertEqual(s.resumen(self.exp,self.admin)['secuencia'],[]); self.assertEqual(s.resumen(self.exp,self.oficina)['secuencia'],[])   # solo guía a TALLER
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
        self.assertIn('mf-main-action',html_taller)   # TALLER: botón principal «Registrar mantenimiento»
        for sobrante in ('Registrar repuestos del cliente','Registrar repuesto utilizado','Adjuntar fotografía de reparación','Abrir actividades y evidencias'): self.assertNotIn(sobrante,html_taller)
        self.assertNotIn('no sustituye la aceptación',html_admin); self.assertNotIn('Pendiente de aceptación del cliente',html_admin)
        self.assertNotIn('Registrar respuesta del cliente',str(s.resumen(self.exp,self.oficina)['accion']))
        self.assertFalse(DecisionComercial.objects.filter(expediente=self.exp).exists())   # sin aprobaciones inventadas hasta usarla
        # 3) Sin registrar repuestos: TALLER registra el mantenimiento en una sola pantalla (avance con fotografía y cierre).
        self.assertEqual(RecepcionRepuesto.objects.filter(expediente=self.exp).count(),0)
        clave=uuid.uuid4()
        act=o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=clave,inicio=timezone.now(),
            componentes=[str(self.pieza.pk)],observaciones='Desarmado y limpieza',fotos=[foto_pieza()])
        self.assertIn(self.pieza.descripcion[:20],act.componentes); self.assertTrue(act.trabajo.strip())
        self.assertEqual(act.evidencias.count(),1)
        o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=clave,inicio=timezone.now(),   # reintento: sin duplicados
            componentes=[str(self.pieza.pk)],observaciones='Desarmado y limpieza',fotos=[foto_pieza()])
        self.assertEqual((type(act).objects.count(),act.evidencias.count()),(1,1))
        r=taller(); self.assertEqual((r['paso'],r['accion']['texto']),(4,'Registrar mantenimiento')); self.assertIn('Registra el mantenimiento',r['mensaje'])
        self.assertEqual([e['estado'] for e in r['secuencia']],['En proceso','No disponible','No disponible'])
        self.assertEqual([e['clase'] for e in r['secuencia']],['proceso','pendiente','pendiente'])
        o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),inicio=timezone.now(),
            observaciones='Desarmado y limpieza',resultado='Resultado QA',finalizar=True)
        resultado=act.resultados.get()
        self.assertEqual(RecepcionRepuesto.objects.filter(expediente=self.exp).count(),0)
        # Finalizado: la calibración queda disponible de inmediato; ADMINISTRADOR no tiene que aprobar el mantenimiento.
        self.assertIn('Mantenimiento finalizado',taller()['mensaje']); self.assertNotIn('debe revisarlo',taller()['mensaje'])
        self.assertEqual([e['estado'] for e in taller()['secuencia']],['Finalizado','Disponible','No disponible'])
        self.assertFalse(RevisionReparacion.objects.exists())
        dec=DecisionComercial.objects.get(expediente=self.exp)   # autorización derivada de la revisión de ADMINISTRADOR
        self.assertEqual((dec.politica['tipo'],dec.politica['revision']),('ADMINISTRATIVA',RevisionAdministrativa.objects.get(expediente=self.exp).pk))
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='AUTORIZACION_ADMINISTRATIVA').count(),1)
        self.assertIn('Reparación y mantenimiento',lista())
        r=taller()
        self.assertTrue(r['aprobado_admin']); self.assertEqual([p['estado'] for p in r['pasos']][2],'Aprobado'); self.assertEqual(r['pasos'][2]['clase'],'ok')
        self.assertIn('clase',r['pasos'][0]); self.assertNotIn('Pendiente de aceptación del cliente',r['mensaje'])
        # Calibración: sin protocolo aprobado se bloquea con un mensaje claro; con protocolo validado TALLER genera el informe.
        self.assertEqual(r['accion']['texto'],'Registrar calibración'); self.assertFalse(r['bloqueo'])
        self.assertIn('datos preliminares',r['mensaje']); self.assertIn('No existe un protocolo de calibración',r['mensaje'])
        from . import calibracion_services as c, test_calibracion as cq
        # La pantalla de inicio de calibración no depende de la política comercial JSON: sin protocolo avisa; con protocolo ofrece iniciar.
        self.client.force_login(self.taller); url_cal=reverse('masiscam:calibracion_expediente',args=[self.exp.pk])+'?flujo=1'
        html=self.client.get(url_cal).content.decode()
        self.assertIn('data-sin-protocolo',html); self.assertIn('Falta protocolo de calibración aprobado para continuar',html); self.assertNotIn('No hay acciones de inicio disponibles',html)
        definicion=cq.definicion_qa(); definicion['aplicacion'].update(marca=self.exp.registro.equipo.marca,modelo=self.exp.registro.equipo.modelo)
        protocolo=c.crear_plantilla(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='OG-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.exp.registro.equipo.tipo_producto,definicion=definicion,proyecto_id=self.exp.registro.equipo.proyecto_id))
        c.aplicar_plantilla(usuario=self.admin,empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.exp.registro.equipo_id,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            from .test_tecnica import usuario as _usuario
            cq.validar_qa(self.empresa,_usuario(self.empresa,'validador-og-qa','ADMIN'),protocolo)
            r=taller(); self.assertEqual(r['accion']['texto'],'Registrar calibración'); self.assertFalse(r['bloqueo'])
            html=self.client.get(url_cal).content.decode()
            self.assertIn('Iniciar calibración',html); self.assertNotIn('data-sin-protocolo',html); self.assertNotIn('No hay acciones de inicio disponibles',html)
            self.client.force_login(self.taller)
            html=self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).content.decode()
            self.assertIn('Registrar calibración',html); self.assertIn('data-aprobado-admin',html); self.assertIn('is-ok',html)
            self.assertIn(url_cal.split('?')[0],html)   # la hoja de calibración es accesible desde la secuencia de TALLER
            self.assertNotIn('Registrar respuesta del cliente',html); self.assertNotIn('MASISCAM_POLITICA_COMERCIAL',html)
            self.client.force_login(self.oficina)
            self.assertNotEqual(s.resumen(self.exp,self.oficina)['accion'] and s.resumen(self.exp,self.oficina)['accion']['texto'],'Registrar calibración')
        self.assertEqual(ExpedienteMantenimiento.objects.filter(registro__equipo=self.exp.registro.equipo).count(),1)
        self.assertEqual((EnvioRepuestos.objects.count(),AdquisicionRepuesto.objects.count()),(0,0))

    def test_formularios_de_reparacion_y_repuesto_utilizado_usan_lo_aprobado_por_administrador(self):
        from .models import ActividadReparacion
        self._con_conceptos(); ids=[c['id'] for c in self.sol.snapshot['conceptos']]
        prop=self.cotizar(archivo=None,cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        pieza=self.diag.piezas.get()
        self.client.force_login(self.taller)
        url_act=reverse('masiscam:operacion_operar',args=[self.exp.pk,'actividad']); url_uso=reverse('masiscam:operacion_operar',args=[self.exp.pk,'uso'])
        # Reparación: actividades = conceptos técnicos aprobados (el consumible no es una actividad) y componentes = piezas del expediente.
        html=self.client.get(url_act).content.decode()
        for esperado in ('Mantenimiento preventivo del reductor','Reparación de eje y rodamientos'): self.assertIn(f'<option value="{esperado}"',html)
        self.assertNotIn('<option value="Cambio de aceite"',html); self.assertNotIn('<option value="Aceite ISO VG 220"',html)
        self.assertNotIn('<textarea name="componentes"',html); self.assertEqual(html.count('type="checkbox" name="componentes"'),1)
        self.assertIn('Fecha y hora de inicio',html); self.assertIn('Observaciones',html); self.assertNotIn('Identifique los componentes',html)
        # Repuesto utilizado sin actividades registradas: mensaje claro y sin formulario.
        html=self.client.get(url_uso).content.decode()
        self.assertIn('Primero registre actividades técnicas antes de usar repuestos.',html); self.assertNotIn('Registrar repuesto utilizado',html)
        o.recibir_repuesto(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(pieza_id=pieza.pk,procedencia='Cliente',entregado_por='Cliente QA',numero_parte=pieza.numero_parte,
                descripcion=pieza.descripcion,cantidad=str(pieza.cantidad),unidad=pieza.unidad,fecha=timezone.now(),guia='',discrepancia=''))
        inicio=(timezone.localtime()-timezone.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        datos=dict(clave=uuid.uuid4(),trabajo='Reparación de eje y rodamientos',componentes=[str(pieza.pk)],inicio=inicio,observaciones='')
        # Una actividad ajena a la lista aprobada o un componente que no es del expediente se rechaza.
        for malo in (dict(datos,clave=uuid.uuid4(),trabajo='Trabajo inventado'),dict(datos,clave=uuid.uuid4(),componentes=['999999']),dict(datos,clave=uuid.uuid4(),componentes=[])):
            self.assertEqual(self.client.post(url_act,malo).status_code,400)
        self.assertFalse(ActividadReparacion.objects.exists())
        self.assertEqual(self.client.post(url_act,datos).status_code,302)
        act=ActividadReparacion.objects.get()
        self.assertEqual(act.trabajo,'Reparación de eje y rodamientos'); self.assertIn(pieza.descripcion[:20],act.componentes)
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='REPARACION_INICIADA').count(),1)
        # Con una sola actividad abierta, el campo «Actividad» ya llega seleccionado.
        html=self.client.get(url_uso).content.decode()
        self.assertNotIn('Primero registre actividades',html); self.assertIn(f'<option value="{act.pk}" selected>',html); self.assertIn('Registrar repuesto utilizado',html)
        # Otros roles no registran repuestos utilizados.
        self.client.force_login(self.oficina); self.assertEqual(self.client.get(url_uso).status_code,403)

    def _png(self,color):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        datos=BytesIO(); Image.new('RGB',(40,30),color).save(datos,'PNG')
        return datos.getvalue()

    def test_pantalla_unica_de_mantenimiento_sin_repuestos_ni_formularios_sueltos(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ActividadReparacion, EvidenciaOperativa, ResultadoReparacion, RevisionReparacion
        self._con_conceptos(); ids=[c['id'] for c in self.sol.snapshot['conceptos']]
        prop=self.cotizar(archivo=None,cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        pieza=self.diag.piezas.get()
        url=reverse('masiscam:operacion_operar',args=[self.exp.pk,'mantenimiento'])
        foto=lambda nombre,color:SimpleUploadedFile(nombre,self._png(color),content_type='image/png')
        # Sin aprobación conjunta de ADMINISTRADOR no hay mantenimiento ni formulario.
        self.client.force_login(self.taller); html=self.client.get(url).content.decode()
        self.assertIn('data-sin-autorizacion',html); self.assertNotIn('data-form-mantenimiento',html)
        with self.assertRaises(ValidationError):
            o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),inicio=timezone.now())
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        # Pantalla única: trabajos autorizados (sin el consumible), componentes precargados y solo dos botones de acción.
        html=self.client.get(url).content.decode()
        for esperado in ('Trabajos autorizados','Mantenimiento preventivo del reductor','Reparación de eje y rodamientos','Guardar avance','Finalizar mantenimiento','Observaciones','Fotografías del mantenimiento'):
            self.assertIn(esperado,html,esperado)
        self.assertNotIn('Aceite ISO VG 220',html); self.assertNotIn('Registrar repuestos del cliente',html); self.assertNotIn('Repuesto utilizado',html)
        self.assertRegex(html,r'type="checkbox" name="componentes"[^>]*checked'); self.assertIn('multiple',html)
        # Otros roles no acceden.
        for otro in (self.oficina,self.admin):
            self.client.force_login(otro); self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.taller)
        base=dict(inicio=timezone.now().isoformat(),componentes=[str(pieza.pk)],observaciones='Desarmado y limpieza',resultado='')
        # Guardar avance con dos fotografías: crea la actividad con los trabajos aprobados y la evidencia, sin recepción de repuestos.
        clave=uuid.uuid4()
        datos=lambda **extra:dict(base,clave=clave,accion='avance',fotografias=[foto('a.png','red'),foto('b.png','blue')],**extra)
        self.assertEqual(self.client.post(url,datos()).status_code,302)
        act=ActividadReparacion.objects.get()
        self.assertIn('Mantenimiento preventivo del reductor',act.trabajo); self.assertIn('Reparación de eje y rodamientos',act.trabajo)
        self.assertNotIn('Aceite',act.trabajo); self.assertIn(pieza.descripcion[:20],act.componentes)
        self.assertEqual(act.evidencias.filter(clase='DURANTE').count(),2); self.assertFalse(RecepcionRepuesto.objects.exists())
        # Doble clic / reintento de red con la misma clave: sin duplicados ni conflicto.
        self.assertEqual(self.client.post(url,datos()).status_code,302)
        self.assertEqual((ActividadReparacion.objects.count(),EvidenciaOperativa.objects.filter(actividad=act).count()),(1,2))
        # Operación: TALLER ve una sola entrada; no la lista de formularios sueltos.
        html=self.client.get(reverse('masiscam:operacion_detalle',args=[self.exp.pk])).content.decode()
        self.assertIn('Registrar mantenimiento',html)
        for sobrante in ('Iniciar actividad','Repuesto utilizado','Fotografía de actividad','Resultado de actividad','Registrar repuestos del cliente'): self.assertNotIn(sobrante,html)
        # Un avance posterior (clave nueva) añade fotografías a la misma actividad.
        self.assertEqual(self.client.post(url,dict(base,clave=uuid.uuid4(),accion='avance',fotografias=[foto('c.png','green')])).status_code,302)
        self.assertEqual((ActividadReparacion.objects.count(),act.evidencias.count()),(1,3))
        # Un cambio de observaciones sin fotografía no se puede guardar (los registros son inmutables) y no pierde datos.
        r=self.client.post(url,dict(base,clave=uuid.uuid4(),accion='avance',observaciones='Otra nota'))
        self.assertEqual(r.status_code,400); self.assertIn('Adjunte una fotografía',r.content.decode()); self.assertEqual(act.evidencias.count(),3)
        # Finalizar exige el resultado.
        r=self.client.post(url,dict(base,clave=uuid.uuid4(),accion='finalizar')); self.assertEqual(r.status_code,400); self.assertFalse(ResultadoReparacion.objects.exists())
        self.assertIn('Describa el resultado',r.content.decode()); self.assertIn('Finalizar mantenimiento',r.content.decode()); self.assertIn('data-form-mantenimiento',r.content.decode())
        fin=uuid.uuid4()
        final=dict(base,clave=fin,accion='finalizar',resultado='Reductor mantenido y probado')
        self.assertEqual(self.client.post(url,final).status_code,302)
        self.assertEqual(self.client.post(url,final).status_code,302)   # doble clic: idempotente
        resultado=ResultadoReparacion.objects.get(); self.assertEqual(resultado.resultado,'Reductor mantenido y probado')
        self.assertEqual({a:Auditoria.objects.filter(expediente=self.exp,accion=a).count() for a in ('REPARACION_INICIADA','REPARACION_EVIDENCIA','REPARACION_RESULTADO')},
            dict(REPARACION_INICIADA=1,REPARACION_EVIDENCIA=3,REPARACION_RESULTADO=1))
        html=self.client.get(url).content.decode(); self.assertIn('data-mantenimiento-enviado',html); self.assertNotIn('data-form-mantenimiento',html)
        self.assertEqual(self.client.post(url,dict(base,clave=uuid.uuid4(),accion='avance',fotografias=[foto('d.png','black')])).status_code,400)   # enviado: sin cambios
        self.assertEqual(act.evidencias.count(),3)
        self.assertEqual(o.estado(self.exp),'REPARACION_FINALIZADA')   # sin revisión adicional: la aprobación conjunta ya autorizó el trabajo
        # Revisión opcional: TALLER no revisa su trabajo; si ADMINISTRADOR lo observa expresamente, TALLER lo corrige con una nueva versión.
        with self.assertRaises(PermissionDenied):
            o.revisar_actividad(usuario=self.taller,empresa=self.empresa,resultado_id=resultado.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='propio')
        o.revisar_actividad(usuario=self.admin,empresa=self.empresa,resultado_id=resultado.pk,clave=uuid.uuid4(),decision='OBSERVADO',motivo='Falta torque final')
        self.assertEqual(o.estado(self.exp),'OBSERVADO')
        html=self.client.get(url).content.decode(); self.assertIn('data-observado',html); self.assertIn('Falta torque final',html); self.assertIn('data-form-mantenimiento',html)
        self.assertEqual(self.client.post(url,dict(base,clave=uuid.uuid4(),accion='finalizar',resultado='Torque final aplicado')).status_code,302)
        self.assertEqual(ResultadoReparacion.objects.count(),2)
        self.assertEqual(o.estado(self.exp),'REPARACION_FINALIZADA')   # la corrección se finaliza sin otra aprobación; self.assertFalse(RecepcionRepuesto.objects.exists())

    def test_calibracion_accesible_tras_aprobacion_pero_exige_mantenimiento_y_protocolo(self):
        from . import calibracion_services as c
        self._con_conceptos(); ids=[x['id'] for x in self.sol.snapshot['conceptos']]
        prop=self.cotizar(archivo=None,cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        self.client.force_login(self.taller)
        r=self.client.get(reverse('masiscam:calibracion_expediente',args=[self.exp.pk])+'?flujo=1')
        self.assertEqual(r.status_code,200); html=r.content.decode()
        self.assertIn('data-espera-mantenimiento',html); self.assertNotIn('Iniciar calibración',html); self.assertNotIn('revisado por ADMINISTRADOR',html)
        with self.assertRaises(ValidationError):   # el servidor mantiene los bloqueos aunque la hoja sea accesible
            c.iniciar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=1,clave=uuid.uuid4())
        accion=s.resumen(self.exp,self.taller)['secuencia'][1]
        self.assertEqual((accion['texto'],accion['estado']),('Registrar calibración','No disponible')); self.assertIn('/calibracion/',accion['url'])

    @override_settings(DEBUG=False,MASISCAM_CALIBRACION_LOCAL=True,MASISCAM_CIERRE_LOCAL=True)
    def test_informe_final_consolida_sin_repuestos_recibidos_y_taller_sin_botones_antiguos(self):
        import re
        from io import BytesIO
        from pypdf import PdfReader
        from . import calibracion_services as c, cierre_services as cs, test_calibracion as cq
        from .models import EmisionOperativa, InformeFinal, UsoRepuesto
        from .test_plano import foto_pieza
        from .test_tecnica import usuario as _usuario
        self._con_conceptos(); ids=[x['id'] for x in self.sol.snapshot['conceptos']]
        prop=self.cotizar(archivo=None,cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        self.pieza=self.diag.piezas.get(); self.assertEqual(RecepcionRepuesto.objects.count(),0)
        act=o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),inicio=timezone.now(),
            componentes=[str(self.pieza.pk)],observaciones='Desarmado y limpieza',fotos=[foto_pieza()])
        # TALLER: sin enlaces ni formularios antiguos en ninguna de sus pantallas (las rutas y registros históricos se conservan).
        antiguos=re.compile(r'operacion/accion/(repuesto|actividad|uso|foto|resultado|foto_repuesto)/')
        self.client.force_login(self.taller)
        for ruta in (reverse('masiscam:flujo_detalle',args=[self.exp.pk]),reverse('masiscam:operacion_detalle',args=[self.exp.pk]),
                reverse('masiscam:operacion_operar',args=[self.exp.pk,'mantenimiento']),reverse('masiscam:calibracion_expediente',args=[self.exp.pk]),
                reverse('masiscam:taller_equipo',args=[self.exp.registro.equipo_id])+f'?expediente={self.exp.pk}',reverse('masiscam:taller_estado')):
            html=self.client.get(ruta).content.decode()
            self.assertIsNone(antiguos.search(html),ruta)
            for texto in ('Registrar repuestos del cliente','Repuesto utilizado','Iniciar actividad','Resultado de actividad','Fotografía de actividad'): self.assertNotIn(texto,html,ruta)
        o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),inicio=timezone.now(),
            observaciones='Desarmado y limpieza',resultado='Reductor mantenido y probado sin fugas',finalizar=True)
        definicion=cq.definicion_qa(); definicion['aplicacion'].update(marca=self.exp.registro.equipo.marca,modelo=self.exp.registro.equipo.modelo)
        protocolo=c.crear_plantilla(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='IF-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.exp.registro.equipo.tipo_producto,definicion=definicion,proyecto_id=self.exp.registro.equipo.proyecto_id))
        c.aplicar_plantilla(usuario=self.admin,empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.exp.registro.equipo_id,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            cq.validar_qa(self.empresa,_usuario(self.empresa,'validador-if-qa','ADMIN'),protocolo)
            cal=c.iniciar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=protocolo.pk,clave=uuid.uuid4())
            cal=cq.completar(self.empresa,self.taller,cal)
            c.enviar(usuario=self.taller,empresa=self.empresa,calibracion_id=cal.pk,version=cal.version_edicion,clave=uuid.uuid4())
            c.decidir(usuario=self.admin,empresa=self.empresa,calibracion_id=cal.pk,clave=uuid.uuid4(),decision='APROBADA',motivo='SIMULACIÓN QA',
                evaluaciones={r['codigo']:'CONFORME' for r in protocolo.definicion['mediciones']})
            # TALLER ve la tercera acción y genera el informe final con la misma cuenta.
            r=s.resumen(self.exp,self.taller)
            self.assertEqual(r['accion']['texto'],'Generar informe final')
            self.assertEqual([(e['texto'],e['estado']) for e in r['secuencia']],[('Registrar mantenimiento','Finalizado'),('Registrar calibración','Finalizado'),('Generar informe final','Disponible')])
            em=cs.reservar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4())
            cs.generar(em.pk); em.refresh_from_db()
        snap=em.snapshot
        # Sin recepciones ni movimientos inventados; las piezas del diagnóstico figuran como información técnica.
        self.assertEqual((RecepcionRepuesto.objects.count(),UsoRepuesto.objects.count(),EnvioRepuestos.objects.count(),AdquisicionRepuesto.objects.count()),(0,0,0,0))
        self.assertEqual(snap['repuestos'],[]); self.assertEqual(len(snap['tecnico']['piezas']),1)
        self.assertEqual(snap['tecnico']['piezas'][0]['descripcion'],self.pieza.descripcion)
        # Trabajos realizados, fotografías, calibración y aprobaciones.
        rep=snap['reparaciones'][0]
        self.assertIn('Mantenimiento preventivo del reductor',rep['trabajo']); self.assertEqual(rep['resultado'],'Reductor mantenido y probado sin fugas')
        self.assertEqual((rep['aprobacion']['responsable'],rep['usos']),(self.admin.username,[]))
        self.assertTrue(any(e['seccion']=='Intervención / repuestos' and e['foto'] for e in snap['evidencias']))
        self.assertEqual(snap['calibracion']['estado'],'APROBADA'); self.assertEqual(snap['calibracion']['decision']['responsable'],self.admin.username)
        self.assertEqual(len(snap['calibracion']['mediciones']),16)
        self.assertTrue(snap['autorizacion']['responsable']); self.assertEqual(snap['tecnico']['piezas'][0]['cantidad'],str(self.pieza.cantidad))
        lector=PdfReader(BytesIO(em.documento.archivo.open('rb').read())); texto='\n'.join(p.extract_text() for p in lector.pages)
        for palabra in ('INFORME FINAL','Diagnóstico',self.pieza.descripcion[:15],'Reparación y montaje','Mantenimiento preventivo del reductor','Reductor mantenido y probado sin fugas',
                'Calibración y aprobación técnica','Sin recepciones de repuestos registradas','no condicionó el mantenimiento'): self.assertIn(palabra,texto,palabra)
        self.assertGreaterEqual(sum(len(p.images) for p in lector.pages),1)   # la fotografía del mantenimiento va incrustada
        self.assertEqual(InformeFinal.objects.filter(expediente=self.exp).count(),1)

    @override_settings(DEBUG=False,MASISCAM_CALIBRACION_LOCAL=True,MASISCAM_CIERRE_LOCAL=True)
    def test_recorrido_integral_tres_roles_hasta_cerrado_sin_revision_de_mantenimiento(self):
        from io import BytesIO
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from pypdf import PdfReader
        from . import calibracion_services as c, cierre_services as cs, test_calibracion as cq
        from .models import Calibracion, InformeFinal, CierreExpediente, RevisionReparacion, ActividadReparacion, ResultadoReparacion
        from .test_tecnica import usuario as _usuario
        self._con_conceptos(); ids=[x['id'] for x in self.sol.snapshot['conceptos']]
        self.pieza=self.diag.piezas.get()
        prop=self.cotizar(archivo=None,cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        flujo=reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        def como(user): self.client.force_login(user); return self.client
        def sec(user): return [(e['texto'],e['estado']) for e in s.resumen(self.exp,user)['secuencia']]
        def enlaces(user,*rutas):
            """Todos los enlaces internos del contenido principal responden sin error para ese rol."""
            vistos=set()
            for ruta in rutas:
                respuesta=como(user).get(ruta); self.assertEqual(respuesta.status_code,200,(user.username,ruta))
                main=re.search(r'<main.*?</main>',respuesta.content.decode(),re.S)
                for href in re.findall(r'href="(/[^"#]*)"',main.group(0) if main else ''):
                    if href in vistos or href.startswith(('/static/','/accounts/')) or 'logout' in href: continue
                    vistos.add(href)
                    self.assertLess(como(user).get(href).status_code,400,(user.username,ruta,href))
        # Sin la aprobación conjunta de ADMINISTRADOR no hay secuencia de ejecución para TALLER.
        self.assertEqual(s.resumen(self.exp,self.taller)['secuencia'],[])
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        orden=[('Registrar mantenimiento','Disponible'),('Registrar calibración','No disponible'),('Generar informe final','No disponible')]
        self.assertEqual(sec(self.taller),orden)
        for user in (self.taller,self.oficina,self.admin): enlaces(user,flujo)
        # 1. Mantenimiento: una sola pantalla; al finalizar se habilita la calibración sin otra aprobación.
        url_m=reverse('masiscam:operacion_operar',args=[self.exp.pk,'mantenimiento'])
        base=dict(inicio=timezone.now().isoformat(),componentes=[str(self.pieza.pk)],observaciones='Desarmado, limpieza y montaje',resultado='Reductor mantenido y probado sin fugas')
        clave=uuid.uuid4()
        envio=lambda: dict(base,clave=clave,accion='finalizar',fotografias=[SimpleUploadedFile('m.png',self._png('red'),content_type='image/png')])
        r=como(self.taller).post(url_m,envio()); self.assertEqual((r.status_code,r['Location']),(302,flujo))
        self.assertEqual(self.client.post(url_m,envio()).status_code,302)   # doble clic
        self.assertEqual((ActividadReparacion.objects.count(),ResultadoReparacion.objects.count(),RevisionReparacion.objects.count()),(1,1,0))
        self.assertEqual(o.estado(self.exp),'REPARACION_FINALIZADA')
        self.assertFalse(Auditoria.objects.filter(expediente=self.exp,accion='REPARACION_REVISION').exists())
        self.assertEqual(sec(self.taller),[('Registrar mantenimiento','Finalizado'),('Registrar calibración','Disponible'),('Generar informe final','No disponible')])
        self.assertEqual(s.resumen(self.exp,self.taller)['accion']['texto'],'Registrar calibración')
        self.assertIsNone(s.resumen(self.exp,self.admin)['accion'])   # ADMINISTRADOR no debe aprobar el mantenimiento
        html=como(self.taller).get(flujo).content.decode()
        for prohibido in ('Ahora','debe revisarlo','Revisar reparación'): self.assertNotIn(prohibido,html)
        # 2. Calibración sin protocolo: datos preliminares, requisito exacto, sin conformidad ni informe.
        url_c=reverse('masiscam:calibracion_expediente',args=[self.exp.pk])
        html=como(self.taller).get(url_c).content.decode()
        for marca in ('data-sin-protocolo','data-form-preliminar','No existe un protocolo de calibración'): self.assertIn(marca,html)
        pre=dict(op='preliminar',clave=uuid.uuid4(),fecha='2026-11-20T10:00',instrumentos='Instrumento QA',resultados='Lecturas preliminares QA',observaciones='Sin protocolo')
        self.assertEqual(self.client.post(url_c,pre).status_code,302); self.assertEqual(self.client.post(url_c,pre).status_code,302)
        self.assertEqual((Auditoria.objects.filter(expediente=self.exp,accion='CAL_PRELIMINAR').count(),Calibracion.objects.count()),(1,0))
        html=self.client.get(url_c).content.decode(); self.assertIn('data-preliminares',html); self.assertIn('PRELIMINAR',html)
        for otro in (self.admin,self.oficina):
            self.assertIn(como(otro).post(url_c,dict(pre,clave=uuid.uuid4())).status_code,(400,403))
            self.assertNotIn('data-form-preliminar',self.client.get(url_c).content.decode())
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='CAL_PRELIMINAR').count(),1)
        with self.assertRaises(ValidationError): cs.reservar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4())
        # Protocolo registrado pero no validado ni aprobado por el propietario: dice exactamente qué falta y no permite iniciar.
        definicion=cq.definicion_qa(); definicion['aplicacion'].update(marca=self.exp.registro.equipo.marca,modelo=self.exp.registro.equipo.modelo)
        protocolo=c.crear_plantilla(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='INTEGRAL-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.exp.registro.equipo.tipo_producto,definicion=definicion,proyecto_id=self.exp.registro.equipo.proyecto_id))
        c.aplicar_plantilla(usuario=self.admin,empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.exp.registro.equipo_id,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        html=como(self.taller).get(url_c).content.decode()
        self.assertIn('falta la validación técnica independiente',html); self.assertIn('falta la aprobación del propietario',html)
        r=self.client.post(url_c,dict(clave=uuid.uuid4(),plantilla_id=protocolo.pk)); self.assertEqual(r.status_code,400)
        self.assertFalse(Calibracion.objects.exists())
        with self.assertRaises(ValidationError): c.iniciar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=protocolo.pk,clave=uuid.uuid4())
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            cq.validar_qa(self.empresa,_usuario(self.empresa,'validador-integral-qa','ADMIN'),protocolo)
            self.assertIn('Iniciar calibración',como(self.taller).get(url_c).content.decode())
            r=self.client.post(url_c+'?flujo=1',dict(clave=uuid.uuid4(),plantilla_id=protocolo.pk)); self.assertEqual(r.status_code,302)
            cal=Calibracion.objects.get(); hoja=reverse('masiscam:calibracion_revision',args=[cal.pk])
            self.assertEqual(r['Location'],hoja+'?flujo=1')
            self.assertEqual(sec(self.taller)[1],('Registrar calibración','En proceso'))
            # 3. Hoja única: guardar avance y finalizar sin duplicados.
            def hoja_post(accion,n=16,usuario=None,**extra):
                cal.refresh_from_db()
                datos=dict(op='formato',accion=accion,clave=extra.pop('clave',uuid.uuid4()),version=extra.pop('version',cal.version_edicion),fecha='2026-11-20T10:00',
                    instrumentos='INSTRUMENTO FICTICIO QA',resultados='Registro ficticio QA',observaciones='Observaciones finales QA',param_0='1.5',
                    **{f'medicion_{i}':str(i) for i in range(n)})
                datos.update(extra); return como(usuario or self.taller).post(hoja,datos)
            self.assertEqual(hoja_post('guardar',n=4).status_code,302); cal.refresh_from_db(); self.assertEqual(cal.mediciones.count(),4)
            self.assertEqual(hoja_post('informe',n=8).status_code,400)   # incompleta: se guarda lo escrito pero no se envía
            cal.refresh_from_db(); self.assertEqual((cal.mediciones.count(),cal.enviado_en),(8,None))
            clave_h=uuid.uuid4(); version=cal.version_edicion
            evid=lambda: dict(evidencia_clase='FOTO',evidencia_leyenda='Fotografía de la calibración QA',
                evidencia_archivo=SimpleUploadedFile('cal.png',self._png('green'),content_type='image/png'))
            with patch('masiscam.calibracion_tasks.generar_informe_calibracion.apply_async') as tarea:
                self.assertEqual(hoja_post('informe',clave=clave_h,version=version,**evid()).status_code,302)
                eventos=Auditoria.objects.filter(expediente=self.exp,accion__startswith='CAL_').count()
                self.assertEqual(hoja_post('informe',clave=clave_h,version=version,**evid()).status_code,302)   # doble clic
                self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion__startswith='CAL_').count(),eventos)
            cal.refresh_from_db()
            self.assertEqual((cal.estado,cal.mediciones.count(),cal.evidencias.count(),cal.emisiones.count()),('PENDIENTE_REVISION',16,1,1))
            # La hoja enviada está en revisión: TALLER ve quién debe actuar; no hay «Ahora»; ADMINISTRADOR recibe acceso directo.
            self.assertEqual(sec(self.taller),[('Registrar mantenimiento','Finalizado'),('Registrar calibración','En proceso'),('Generar informe final','No disponible')])
            html=como(self.taller).get(flujo).content.decode()
            self.assertNotIn('Ahora',html); self.assertIn('data-responsables',html); self.assertIn(self.admin.username,html); self.assertIn('Enviada',html)
            accion=s.resumen(self.exp,self.admin)['accion']; self.assertEqual((accion['texto'],accion['url']),('Revisar calibración',hoja+'?flujo=1'))
            for user in (self.taller,self.oficina,self.admin): enlaces(user,flujo)
            # 4. Revisión técnica independiente (real): el autor no aprueba; ADMINISTRADOR sí. La generación del informe final es automática.
            decision=dict(op='decision',decision='APROBADA',motivo='Revisión independiente QA',**{f'eval_{i}':'CONFORME' for i in range(16)})
            self.assertEqual(como(self.taller).post(hoja,dict(decision,clave=uuid.uuid4())).status_code,403)
            clave_d=uuid.uuid4()
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(como(self.admin).post(hoja+'?flujo=1',dict(decision,clave=clave_d)).status_code,302)
            self.assertEqual(self.client.post(hoja+'?flujo=1',dict(decision,clave=clave_d)).status_code,302)   # repetición idempotente
            cal.refresh_from_db(); self.assertEqual(cal.estado,'APROBADA')
            self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='CAL_DECISION').count(),1)
            informe=InformeFinal.objects.get(expediente=self.exp)
            self.assertTrue(informe.documento_id); self.assertEqual(informe.estado,'PENDIENTE_REVISION')
            self.assertEqual(sec(self.taller),[('Registrar mantenimiento','Finalizado'),('Registrar calibración','Finalizado'),('Generar informe final','Finalizado')])
            # 5. Informe final: PDF descargable por TALLER con diagnóstico, trabajos, fotografías, calibración y aprobaciones.
            descarga=como(self.taller).get(reverse('masiscam:cierre_documento',args=[self.exp.pk,informe.pk]))
            self.assertEqual((descarga.status_code,descarga['Content-Type']),(200,'application/pdf'))
            lector=PdfReader(BytesIO(b''.join(descarga.streaming_content))); texto='\n'.join(p.extract_text() for p in lector.pages)
            for palabra in ('INFORME FINAL','Diagnóstico','Reductor mantenido y probado sin fugas','Calibración y aprobación técnica','Sin recepciones de repuestos registradas'): self.assertIn(palabra,texto,palabra)
            self.assertGreaterEqual(sum(len(p.images) for p in lector.pages),2)   # fotografías del mantenimiento y de la calibración
            # 6. Revisión del informe por ADMINISTRADOR y cierre independiente.
            url_f=reverse('masiscam:cierre_detalle',args=[self.exp.pk])
            for user in (self.taller,self.oficina,self.admin): enlaces(user,url_f)
            self.assertEqual(s.resumen(self.exp,self.admin)['accion']['texto'],'Revisar informe final')
            revisar=dict(op='revisar',informe=informe.pk,decision='APROBADO',motivo='Informe conforme QA',clave=uuid.uuid4())
            self.assertEqual(como(self.taller).post(url_f,revisar).status_code,403)
            self.assertEqual(como(self.admin).post(url_f,revisar).status_code,302)
            self.assertEqual(self.client.post(url_f,revisar).status_code,302)   # idempotente
            informe.refresh_from_db(); self.assertEqual(informe.estado,'APROBADO')
            self.assertEqual(s.resumen(self.exp,self.admin)['accion']['texto'],'Revisar cierre administrativo')
            cerrar=dict(op='cerrar',informe=informe.pk,motivo='Cierre administrativo QA',clave=uuid.uuid4())
            self.assertEqual(self.client.post(url_f,cerrar).status_code,403)   # quien revisó no cierra
            self.assertEqual(como(self.admin_cierre).post(url_f,cerrar).status_code,302)
            self.assertEqual(self.client.post(url_f,cerrar).status_code,302)   # idempotente
            informe.refresh_from_db(); self.assertEqual(informe.estado,'FINALIZADO'); self.assertEqual(CierreExpediente.objects.filter(expediente=self.exp).count(),1)
        eventos=set(Auditoria.objects.filter(expediente=self.exp).values_list('accion',flat=True))
        for accion in ('REPARACION_INICIADA','REPARACION_RESULTADO','CAL_PRELIMINAR','CAL_INICIADA','CAL_GUARDADA','CAL_ENVIADA','CAL_DECISION','FINAL_RESERVADO','FINAL_PDF_GENERADO','FINAL_REVISION','EXPEDIENTE_CERRADO'):
            self.assertIn(accion,eventos,accion)
        self.assertNotIn('REPARACION_REVISION',eventos)
        # Cerrado: lectura para todos los roles; TALLER solo puede iniciar un nuevo mantenimiento.
        for user in (self.taller,self.oficina,self.admin): enlaces(user,flujo)
        self.assertEqual(s.resumen(self.exp,self.taller)['accion']['texto'],'Registrar nuevo mantenimiento')
        self.assertEqual((RecepcionRepuesto.objects.count(),EnvioRepuestos.objects.count(),AdquisicionRepuesto.objects.count()),(0,0,0))

    @override_settings(DEBUG=False,MASISCAM_CALIBRACION_LOCAL=True,MASISCAM_CIERRE_LOCAL=True)
    def test_recorrido_con_observaciones_de_calibracion_e_informe_corrige_y_cierra(self):
        """Ramas de corrección: calibración observada → corrección autorizada por ADMINISTRADOR → informe observado → nueva versión → cierre."""
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from . import calibracion_services as c, cierre_services as cs, test_calibracion as cq
        from .models import Calibracion, InformeFinal, CierreExpediente
        from .test_plano import foto_pieza
        from .test_tecnica import usuario as _usuario
        self._con_conceptos(); ids=[x['id'] for x in self.sol.snapshot['conceptos']]; self.pieza=self.diag.piezas.get()
        prop=self.cotizar(archivo=None,cotizacion=dict(moneda='USD',lineas=[dict(concepto=i,precio_unitario=p) for i,p in zip(ids,('100.00','250.00','4.00'))]))
        s.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,diagnostico_id=prop.diagnostico_id,
            propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),inicio=timezone.now(),
            componentes=[str(self.pieza.pk)],observaciones='Desarmado y limpieza',fotos=[foto_pieza()])
        o.registrar_mantenimiento(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),inicio=timezone.now(),
            observaciones='Desarmado y limpieza',resultado='Mantenimiento concluido',finalizar=True)
        flujo=reverse('masiscam:flujo_detalle',args=[self.exp.pk]); url_c=reverse('masiscam:calibracion_expediente',args=[self.exp.pk])
        url_f=reverse('masiscam:cierre_detalle',args=[self.exp.pk])
        def como(user): self.client.force_login(user); return self.client
        definicion=cq.definicion_qa(); definicion['aplicacion'].update(marca=self.exp.registro.equipo.marca,modelo=self.exp.registro.equipo.modelo)
        protocolo=c.crear_plantilla(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='OBS-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.exp.registro.equipo.tipo_producto,definicion=definicion,proyecto_id=self.exp.registro.equipo.proyecto_id))
        c.aplicar_plantilla(usuario=self.admin,empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.exp.registro.equipo_id,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            cq.validar_qa(self.empresa,_usuario(self.empresa,'validador-obs-qa','ADMIN'),protocolo)
            cal1=cq.completar(self.empresa,self.taller,c.iniciar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=protocolo.pk,clave=uuid.uuid4()))
            c.enviar(usuario=self.taller,empresa=self.empresa,calibracion_id=cal1.pk,version=cal1.version_edicion,clave=uuid.uuid4())
            hoja1=reverse('masiscam:calibracion_revision',args=[cal1.pk])
            # ADMINISTRADOR observa la calibración: queda registrado, TALLER ve quién debe actuar y no puede reabrirla por su cuenta.
            observar=dict(op='decision',decision='OBSERVADA',motivo='Repetir mediciones 3 y 4',**{f'eval_{i}':'PENDIENTE' for i in range(16)})
            self.assertEqual(como(self.admin).post(hoja1,dict(observar,clave=uuid.uuid4())).status_code,302)
            cal1.refresh_from_db(); self.assertEqual(cal1.estado,'OBSERVADA')
            r=s.resumen(self.exp,self.taller)
            self.assertIn('observó la calibración',r['mensaje']); self.assertEqual([(e['texto'],e['estado']) for e in r['secuencia']][1],('Registrar calibración','En proceso'))
            self.assertTrue(r['secuencia'][1]['responsables'])
            accion=s.resumen(self.exp,self.admin)['accion']; self.assertEqual((accion['texto'],accion['url']),('Habilitar corrección',url_c+'?flujo=1'))
            corregir=dict(plantilla_id=protocolo.pk,motivo='Corrección autorizada tras la observación')
            self.assertEqual(como(self.taller).post(url_c,dict(corregir,clave=uuid.uuid4())).status_code,403)
            clave_c=uuid.uuid4()
            self.assertEqual(como(self.admin).post(url_c+'?flujo=1',dict(corregir,clave=clave_c)).status_code,302)
            self.assertEqual(self.client.post(url_c+'?flujo=1',dict(corregir,clave=clave_c)).status_code,302)   # idempotente
            self.assertEqual(Calibracion.objects.filter(expediente=self.exp).count(),2)
            cal2=Calibracion.objects.get(revision=2); self.assertEqual((cal2.sustituye_id,cal2.tecnico_id,cal2.estado),(cal1.pk,self.taller.pk,'NO_INICIADA'))
            self.assertIn('Registrar calibración',s.resumen(self.exp,self.taller)['accion']['texto'])
            hoja2=reverse('masiscam:calibracion_revision',args=[cal2.pk])
            datos=dict(op='formato',accion='informe',clave=uuid.uuid4(),version=cal2.version_edicion,fecha='2026-11-21T10:00',instrumentos='INSTRUMENTO FICTICIO QA',
                resultados='Registro corregido QA',observaciones='Mediciones repetidas',param_0='1.5',motivo_sin_evidencia='Prueba sin fotografías',
                **{f'medicion_{i}':str(i) for i in range(16)})
            with patch('masiscam.calibracion_tasks.generar_informe_calibracion.apply_async'):
                self.assertEqual(como(self.taller).post(hoja2,datos).status_code,302)
            cal2.refresh_from_db(); self.assertEqual(cal2.estado,'PENDIENTE_REVISION')
            aprobar=dict(op='decision',decision='APROBADA',motivo='Corrección conforme QA',**{f'eval_{i}':'CONFORME' for i in range(16)})
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(como(self.admin).post(hoja2,dict(aprobar,clave=uuid.uuid4())).status_code,302)
            cal2.refresh_from_db(); self.assertEqual(cal2.estado,'APROBADA')
            informe1=InformeFinal.objects.get(expediente=self.exp,version=1); self.assertTrue(informe1.documento_id)
            # ADMINISTRADOR observa el informe final: TALLER prepara una nueva versión (la anterior se conserva) y se cierra.
            self.assertEqual(como(self.admin).post(url_f,dict(op='revisar',informe=informe1.pk,decision='OBSERVADO',motivo='Aclarar el alcance',clave=uuid.uuid4())).status_code,302)
            informe1.refresh_from_db(); self.assertEqual(informe1.estado,'OBSERVADO')
            self.assertEqual(como(self.taller).post(url_f,dict(op='reservar',clave=uuid.uuid4(),nueva_version='on',motivo='x')).status_code,403)   # la ventana de cierre es de consulta para TALLER
            r=s.resumen(self.exp,self.taller); self.assertIn('Aclarar el alcance',r['mensaje'])
            self.assertEqual((r['accion']['texto'],r['accion']['op']),('Preparar nueva versión del informe','final'))
            self.assertEqual([(e['texto'],e['estado']) for e in r['secuencia']][2],('Generar informe final','En proceso'))
            clave_v=uuid.uuid4()
            self.assertEqual(como(self.taller).post(flujo,dict(op='final',clave=clave_v)).status_code,302)
            self.assertEqual(self.client.post(flujo,dict(op='final',clave=uuid.uuid4())).status_code,302)   # reintento: no duplica la versión
            informe2=InformeFinal.objects.get(expediente=self.exp,version=2); informe2.refresh_from_db()
            self.assertEqual(InformeFinal.objects.filter(expediente=self.exp).count(),2); self.assertIn('Aclarar el alcance',informe2.motivo)
            self.assertTrue(informe2.documento_id); self.assertEqual((informe1.version,InformeFinal.objects.filter(expediente=self.exp).count()),(1,2))
            self.assertEqual(como(self.admin).post(url_f,dict(op='revisar',informe=informe2.pk,decision='APROBADO',motivo='Informe conforme QA',clave=uuid.uuid4())).status_code,302)
            self.assertEqual(como(self.admin_cierre).post(url_f,dict(op='cerrar',informe=informe2.pk,motivo='Cierre administrativo QA',clave=uuid.uuid4())).status_code,302)
            informe2.refresh_from_db(); self.assertEqual(informe2.estado,'FINALIZADO'); self.assertEqual(CierreExpediente.objects.filter(expediente=self.exp,informe=informe2).count(),1)
        for user in (self.taller,self.oficina,self.admin): self.assertEqual(como(user).get(flujo).status_code,200)
        self.assertEqual(s.resumen(self.exp,self.taller)['accion']['texto'],'Registrar nuevo mantenimiento')

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

    def test_repuestos_del_cliente_sin_compra_ni_envio_y_mantenimiento_no_exige_recepcion(self):
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
        self.assertIsNone(s.resumen(self.exp,self.oficina)['accion'])   # OFICINA no registra repuestos
        accion=s.resumen(self.exp,self.taller)['accion']
        self.assertEqual(accion['texto'],'Registrar mantenimiento'); self.assertIn('/operacion/accion/mantenimiento/',accion['url'])
        # La lista de piezas del diagnóstico es informativa: con la aprobación de ADMINISTRADOR TALLER repara sin registrar repuestos.
        self.client.force_login(self.taller)
        o.iniciar_actividad(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(trabajo='QA',componentes='QA',inicio=timezone.now()))
        self.assertEqual((RecepcionRepuesto.objects.count(),EnvioRepuestos.objects.count(),AdquisicionRepuesto.objects.count()),(0,0,0))
        # El registro histórico de recepciones sigue disponible, pero ya no condiciona el trabajo.
        o.recibir_repuesto(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(pieza_id=self.pieza.pk,procedencia='Cliente',entregado_por='Cliente QA',numero_parte=self.pieza.numero_parte,
                descripcion=self.pieza.descripcion,cantidad=str(self.pieza.cantidad),unidad=self.pieza.unidad,fecha=timezone.now(),guia='',discrepancia=''))
        self.assertEqual(RecepcionRepuesto.objects.count(),1)

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

    def _mantenimiento_edge(self,pagina):
        from io import BytesIO
        from PIL import Image
        from playwright.sync_api import sync_playwright, expect
        def png(color):
            datos=BytesIO(); Image.new('RGB',(60,40),color).save(datos,'PNG'); return dict(name=f'{color}.png',mimeType='image/png',buffer=datos.getvalue())
        url=self.live_server_url+reverse('masiscam:operacion_operar',args=[self.exp.pk,'mantenimiento'])+'?flujo=1'
        galleta=self._galleta(self.taller)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for ancho in (390,1440):   # primero el móvil (solo diseño); el escritorio guarda y finaliza
                    context=browser.new_context(viewport=dict(width=ancho,height=1000))
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galleta,url=self.live_server_url)])
                    page=context.new_page(); page.goto(url)
                    expect(page.locator('h1')).to_have_text('Registrar mantenimiento')
                    expect(page.get_by_text('Trabajos autorizados')).to_be_visible()
                    expect(page.locator('[data-componentes] input[type=checkbox]:checked')).to_have_count(1)
                    for boton in ('Guardar avance','Finalizar mantenimiento'): expect(page.get_by_role('button',name=boton)).to_be_visible()
                    for ausente in ('Registrar repuestos del cliente','Repuesto utilizado','Resultado de actividad'): expect(page.locator('body')).not_to_contain_text(ausente)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'desborda a {ancho}px')
                    if ancho==390:
                        ys=[page.get_by_role('button',name=b).bounding_box()['y'] for b in ('Guardar avance','Finalizar mantenimiento')]
                        self.assertTrue(ys[0]<=ys[1],'botones fuera de orden en móvil')
                    else:
                        page.locator('textarea[name=observaciones]').fill('Desarmado, limpieza y cambio de sellos')
                        page.locator('#id_fotografias').set_input_files([png('red'),png('blue')])
                        page.get_by_role('button',name='Guardar avance').click()
                        expect(page.locator('body')).to_contain_text('Avance guardado')
                        expect(page.locator('[data-fotos]')).to_contain_text('FOTOGRAFÍAS GUARDADAS (2)')
                        page.get_by_role('button',name='Finalizar mantenimiento').click()
                        expect(page.locator('.swal2-popup')).to_contain_text('Revisa los campos señalados')   # sin resultado no se finaliza: aviso modal del sistema
                        page.get_by_role('button',name='Aceptar').click()
                        expect(page.locator('[data-form-mantenimiento] .errorlist')).to_contain_text('Describa el resultado')
                        page.locator('textarea[name=resultado]').fill('Mantenimiento concluido sin fugas')
                        page.get_by_role('button',name='Finalizar mantenimiento').click()
                        expect(page.locator('body')).to_contain_text('Mantenimiento finalizado')   # sin otra aprobación: la calibración queda disponible
                    context.close()
            finally:
                browser.close()

    @override_settings(**dict(FLAGS,MASISCAM_CALIBRACION_LOCAL=True,MASISCAM_CIERRE_LOCAL=True))
    def test_taller_ve_en_el_mismo_expediente_cada_cambio_de_estado(self):
        from playwright.sync_api import sync_playwright, expect
        OficinaGestionTests.preparar_solicitud(self,enviar=True)
        t.decidir_diagnostico(usuario=self.admin,empresa=self.empresa,diagnostico_id=self.diag.pk,decision='APROBADO',motivo='QA',clave=uuid.uuid4())
        prop=OficinaGestionTests.cotizar(self,archivo=None)
        galleta=self._galleta(self.taller)
        pagina=self.live_server_url+reverse('masiscam:taller_equipo',args=[self.exp.registro.equipo_id])+f'?expediente={self.exp.pk}'
        flujo_url=self.live_server_url+reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        def ver(texto,botones=(),paso3=None,cookie=None,destino=None,secuencia=None,dx=False,responsables=False):
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True)
                try:
                    for ancho in (1440,390):
                        context=browser.new_context(viewport=dict(width=ancho,height=1000))
                        context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie or galleta,url=self.live_server_url)])
                        page=context.new_page(); page.goto(destino or pagina)
                        expect(page.locator('body')).to_contain_text(texto)
                        for boton in botones: expect(page.get_by_role('link',name=boton).or_(page.get_by_role('button',name=boton)).first).to_be_visible()
                        if secuencia:   # TALLER: Registrar mantenimiento → Registrar calibración → Generar informe final
                            lista=page.locator('[data-secuencia] li')
                            expect(lista).to_have_count(3)
                            for i,estado in enumerate(secuencia): expect(lista.nth(i)).to_have_attribute('data-estado',estado)   # Disponible / En proceso / Finalizado
                            expect(page.locator('[data-secuencia]')).not_to_contain_text('Ahora')
                            if responsables: expect(page.locator('[data-responsables]').first).to_be_visible()   # quién debe actuar
                            for i,texto_paso in enumerate(('Registrar mantenimiento','Registrar calibración','Generar informe final')):
                                expect(lista.nth(i)).to_contain_text(texto_paso)
                            ys=[lista.nth(i).bounding_box()['y'] for i in range(3)]
                            self.assertTrue(max(ys)-min(ys)<5 if ancho==1440 else ys==sorted(ys) and len(set(ys))==3,f'secuencia mal distribuida a {ancho}px')
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'desborda a {ancho}px')
                        if paso3:   # stepper de 5 etapas con chips de estado y color consistente
                            page.goto(flujo_url)
                            if dx:   # resumen del diagnóstico: dos columnas en escritorio, una en móvil; mismo diseño en todos los roles
                                items=page.locator('#diagnostico-resumen .mf-dx-item')
                                expect(items).to_have_count(8)
                                for i,etiqueta in enumerate(('Fallas','Causas','Componentes afectados','Recomendaciones','Conclusiones','Repuestos requeridos','Fecha','Observaciones')):
                                    expect(items.nth(i)).to_contain_text(etiqueta)
                                x0,x3=items.nth(0).bounding_box()['x'],items.nth(3).bounding_box()['x']
                                self.assertTrue(x3-x0>200 if ancho==1440 else abs(x3-x0)<5,f'columnas del diagnóstico a {ancho}px')
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
        ver('Aprobado por ADMINISTRADOR',botones=('Registrar mantenimiento',),paso3=('Aprobado','is-ok'),secuencia=('Disponible','No disponible','No disponible'))
        # ADMINISTRADOR y OFICINA ven el mismo stepper y los mismos chips que TALLER.
        ver('Aprobado por ADMINISTRADOR',paso3=('Aprobado','is-ok'),cookie=self._galleta(self.admin),destino=flujo_url,dx=True)
        ver('Aprobado por ADMINISTRADOR',paso3=('Aprobado','is-ok'),cookie=self._galleta(self.oficina),destino=flujo_url,dx=True)   # sin aceptación del cliente ni política JSON
        # Sin registrar repuestos, TALLER abre la pantalla única de mantenimiento (escritorio y móvil).
        self._mantenimiento_edge(pagina)
        # Mantenimiento finalizado: la calibración queda disponible de inmediato, sin otra aprobación de ADMINISTRADOR.
        from . import calibracion_services as cal, test_calibracion as cq
        from .test_tecnica import usuario as _usuario
        ver('Mantenimiento finalizado',botones=('Registrar calibración',),secuencia=('Finalizado','Disponible','No disponible'))
        calibracion_url=self.live_server_url+reverse('masiscam:calibracion_expediente',args=[self.exp.pk])+'?flujo=1'
        ver('Falta protocolo de calibración aprobado',botones=('Guardar datos preliminares',),destino=calibracion_url)   # datos preliminares y requisito exacto
        definicion=cq.definicion_qa(); definicion['aplicacion'].update(marca=self.exp.registro.equipo.marca,modelo=self.exp.registro.equipo.modelo)
        protocolo=cal.crear_plantilla(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='EDGE-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.exp.registro.equipo.tipo_producto,definicion=definicion,proyecto_id=self.exp.registro.equipo.proyecto_id))
        cal.aplicar_plantilla(usuario=self.admin,empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.exp.registro.equipo_id,
            clave=uuid.uuid4(),fundamento='Solo QA',version_equipo=definicion['aplicacion']['version_equipo'])
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)]):
            cq.validar_qa(self.empresa,_usuario(self.empresa,'validador-edge-qa','ADMIN'),protocolo)
            ver('Protocolo aprobado aplicable',botones=('Iniciar calibración',),destino=calibracion_url)
            hoja=cq.completar(self.empresa,self.taller,cal.iniciar(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,plantilla_id=protocolo.pk,clave=uuid.uuid4()))
            cal.enviar(usuario=self.taller,empresa=self.empresa,calibracion_id=hoja.pk,version=hoja.version_edicion,clave=uuid.uuid4())
            ver('Calibración enviada',secuencia=('Finalizado','En proceso','No disponible'),responsables=True)
            ver('Calibración enviada',botones=('Revisar calibración',),cookie=self._galleta(self.admin),destino=flujo_url)
            # Revisión técnica independiente: al aprobar se genera el informe final (PDF) y ADMINISTRADOR recibe el acceso directo a revisarlo.
            cal.decidir(usuario=self.admin,empresa=self.empresa,calibracion_id=hoja.pk,clave=uuid.uuid4(),decision='APROBADA',motivo='SIMULACIÓN QA',
                evaluaciones={r['codigo']:'CONFORME' for r in protocolo.definicion['mediciones']})
            ver('Generado',secuencia=('Finalizado','Finalizado','Finalizado'),responsables=True)
            ver('Revisar informe final',botones=('Revisar informe final',),cookie=self._galleta(self.admin),destino=flujo_url)
            ver('Informe final y cierre',cookie=self._galleta(self.admin),destino=self.live_server_url+reverse('masiscam:cierre_detalle',args=[self.exp.pk]))
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

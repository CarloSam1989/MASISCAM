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


def definicion_qa(criterios=True,unidad='unidad QA SIN USO INDUSTRIAL',maximo='1'):
    return dict(mediciones=[dict(codigo=f'QA-{n:02}',nombre=f'PUNTO FICTICIO QA {n}',unidad=unidad,
        fuente='Fuente FICTICIA QA, no protocolo real',minimo='-1',maximo=maximo,tolerancia='NO INDUSTRIAL - solo maquetación',
        criterio='Criterio FICTICIO QA para prueba manual, no usar industrialmente' if criterios else '') for n in range(1,17)],
        parametros=[dict(codigo='QA-P',nombre='Parámetro FICTICIO QA',unidad='unidad QA',fuente='Fuente ficticia QA',
            tolerancia='SIN VALIDEZ INDUSTRIAL',criterio='Referencia solo para test',obligatorio=True)],
        aplicacion=dict(marca='Marca',modelo='Modelo',version_equipo='QA SIN VALIDEZ INDUSTRIAL',procedimiento_version='QA v1 NO OPERATIVO'))


def protocolo_pdf():
    from reportlab.pdfgen.canvas import Canvas
    stream=BytesIO();c=Canvas(stream);c.drawString(40,750,'PROTOCOLO FICTICIO QA - SIN VALIDEZ INDUSTRIAL');c.save()
    return SimpleUploadedFile('protocolo-ficticio-qa.pdf',stream.getvalue(),content_type='application/pdf')


def preparar_calibracion(criterios=True,**rango):
    empresa,oficina,planta,tecnico,exp=preparar()
    revisor=usuario(empresa,'planta-cal-independiente','PLANTA')
    diag=aprobar_diagnostico(empresa,oficina,planta,tecnico,exp)
    prop=o.proponer(usuario=oficina,empresa=empresa,expediente_id=exp.pk,diagnostico_id=diag.pk,clave=uuid.uuid4(),
        datos={'alcance':'Alcance FICTICIO QA, no autorización real','presupuesto_referencia':'QA v1'},archivo=presupuesto_qa())
    # Revisión conjunta de ADMINISTRADOR (requisito vigente) antes de registrar la aceptación comercial.
    from . import flujo_services as fs
    fs.revisar_expediente(usuario=planta,empresa=empresa,expediente_id=exp.pk,diagnostico_id=diag.pk,propuesta_id=prop.pk,
        decision='APROBADO',clave=uuid.uuid4())
    o.decidir_comercial(usuario=oficina,empresa=empresa,propuesta_id=prop.pk,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(
        decision='APROBADO',fecha_cliente=timezone.now(),representante_cliente='PERSONA FICTICIA QA',motivo='Fixture aislada, no decisión real',**verificacion_qa()))
    act=o.iniciar_actividad(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,clave=uuid.uuid4(),datos=dict(trabajo='Actividad FICTICIA QA',componentes='Componente QA',inicio=timezone.now()))
    res=o.terminar_actividad(usuario=tecnico,empresa=empresa,actividad_id=act.pk,clave=uuid.uuid4(),datos=dict(fin=timezone.now(),trabajo_realizado='Trabajo ficticio QA',resultado='Sin resultado industrial'))
    o.revisar_actividad(usuario=planta,empresa=empresa,resultado_id=res.pk,clave=uuid.uuid4(),decision='APROBADO',motivo='Solo prueba de software QA')
    p=s.crear_plantilla(usuario=planta,empresa=empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),datos=dict(
        codigo='PROTOCOLO-FICTICIO-QA',titulo='SIN VALIDEZ INDUSTRIAL',tipo_equipo=exp.registro.equipo.tipo_producto,
        definicion=definicion_qa(criterios,**rango),proyecto_id=exp.registro.equipo.proyecto_id))
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

    def _hoja(self,c,accion,mediciones=16,**extra):
        client=Client();client.force_login(extra.pop('usuario',self.tecnico))
        data=dict(op='formato',accion=accion,clave=extra.pop('clave',uuid.uuid4()),version=extra.pop('version',c.version_edicion),
            fecha='2026-11-20T10:00',instrumentos='INSTRUMENTO FICTICIO QA',resultados='Registro ficticio QA',observaciones='Observaciones finales QA',
            motivo_sin_evidencia='Prueba sin fotografías',param_0='1.5',**{f'medicion_{n}':str(n) for n in range(mediciones)})
        data.update(extra)
        return client.post(reverse('masiscam:calibracion_revision',args=[c.pk]),data)

    def test_hoja_de_calibracion_por_secciones_guarda_y_genera_informe_una_sola_vez(self):
        c=self.iniciar();client=Client();client.force_login(self.tecnico)
        html=client.get(reverse('masiscam:calibracion_revision',args=[c.pk])).content.decode()
        for marca in ('A · Datos generales','B · Shims','C · Datos de calibración','D · Cierre','Técnico responsable','Guardar calibración','Generar informe de calibración',
                'Marca','Modelo','Serie','Ratio','Potencia','RPM','Rotación','Factor de servicio'):
            self.assertIn(marca,html,marca)
        self.assertEqual(html.count('name="medicion_'),16)
        self.assertNotIn('Guardar borrador',html); self.assertNotIn('Guardar valor registrado',html)
        # Guardar parcial: conserva lo escrito sin enviar ni generar nada.
        r=self._hoja(c,'guardar',mediciones=3)
        self.assertEqual(r.status_code,302);c.refresh_from_db()
        self.assertEqual(c.mediciones.count(),3);self.assertIsNone(c.enviado_en);self.assertEqual(Decimal(c.parametros['QA-P']),Decimal('1.5'))
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='CAL_MEDICION').count(),3)
        # Generar informe con la hoja incompleta: se guarda lo escrito, pero las validaciones de calibración siguen bloqueando.
        r=self._hoja(c,'informe',mediciones=5)
        self.assertEqual(r.status_code,400);self.assertContains(r,'16 mediciones',status_code=400)
        c.refresh_from_db();self.assertEqual(c.mediciones.count(),5);self.assertIsNone(c.enviado_en);self.assertFalse(c.emisiones.exists())
        # Hoja completa: guarda, envía a la revisión independiente y reserva el informe. Reintentar el mismo envío no duplica.
        clave=uuid.uuid4();version=c.version_edicion
        with patch('masiscam.calibracion_tasks.generar_informe_calibracion.apply_async') as tarea:
            r=self._hoja(c,'informe',clave=clave,version=version)
            self.assertEqual(r.status_code,302);c.refresh_from_db()
            self.assertEqual((c.estado,c.mediciones.count(),c.emisiones.count()),('PENDIENTE_REVISION',16,1));self.assertEqual(tarea.call_count,1)
            eventos=Auditoria.objects.filter(expediente=self.exp,accion__startswith='CAL_').count()
            r=self._hoja(c,'informe',clave=clave,version=version)   # reintento de red o doble clic
            self.assertEqual(r.status_code,302);c.refresh_from_db()
            self.assertEqual(c.emisiones.count(),1);self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion__startswith='CAL_').count(),eventos)
        # La hoja enviada deja de ser editable.
        self.assertNotIn('name="medicion_',client.get(reverse('masiscam:calibracion_revision',args=[c.pk])).content.decode())

    def test_hoja_de_calibracion_no_la_edita_otro_rol_ni_rompe_el_protocolo(self):
        c=self.iniciar()
        for rol in (self.planta,self.oficina):
            self.assertIn(self._hoja(c,'guardar',usuario=rol).status_code,(400,403,404))
        self.assertFalse(c.mediciones.exists())
        # Un valor fuera de la plantilla no crea posiciones arbitrarias.
        r=self._hoja(c,'guardar',medicion_99='1');c.refresh_from_db()
        self.assertEqual(c.mediciones.count(),16)
        self.assertEqual(set(c.mediciones.values_list('punto',flat=True)),{x['codigo'] for x in c.plantilla.definicion['mediciones']})

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
        c=self.enviar();RolMasiscam.objects.filter(perfil__user=self.tecnico).update(rol='ADMIN')
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
        Equipo.objects.filter(pk=self.exp.registro.equipo_id).update(registrado_por=other)  # TALLER responde solo por sus equipos
        t.asignar_tecnico(usuario=self.planta,**self.base,tecnico_id=other.pk,motivo='Reasignación QA',clave=uuid.uuid4())
        q=s.iniciar(usuario=self.planta,**self.base,plantilla_id=self.p.pk,clave=uuid.uuid4(),motivo='Corrección QA con autor nuevo')
        q=completar(self.empresa,other,q);q=s.enviar(usuario=other,empresa=self.empresa,calibracion_id=q.pk,version=q.version_edicion,clave=uuid.uuid4())
        RolMasiscam.objects.filter(perfil__user=self.tecnico).update(rol='ADMIN')
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


VALORES_HOJA=dict(zip(('SHIM-VS','SHIM-VI','SHIM-H','BACKLASH','TOLERANCIA','CAL-TOTAL','MINIMO','MAXIMO','PRES-V','PRES-H'),
    ('0.50','0.55','0.60','0.12','0.05','0.10','0.05','0.20','35','40')))
UNIDADES_QA=dict(longitud='mm',presion='psi')


class HojaBase:
    """Hoja de calibración de TALLER sobre la ficha técnica de la configuración: sin aprobación de ADMINISTRADOR ni protocolo validado."""
    PROTOCOLO={}

    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp,self.revisor,self.p=preparar_calibracion(**self.PROTOCOLO)   # el protocolo heredado no se valida: la hoja no lo necesita
        self.base=dict(empresa=self.empresa,expediente_id=self.exp.pk)

    def abrir(self,**extra):
        return s.iniciar_hoja(usuario=self.tecnico,**self.base,clave=uuid.uuid4(),**extra)

    def llenar(self,c,mediciones=16,parametros=None,usuario=None,**datos):
        params=dict(VALORES_HOJA,_unidades=dict(UNIDADES_QA));params.update(parametros or {})
        return s.guardar_formato(usuario=usuario or self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),
            datos=dict(fecha=timezone.now(),observaciones='Observación de TALLER',parametros=params,**datos),
            mediciones={f'MED-{n:02}':Decimal('0.10')+Decimal(n)/100 for n in range(1,mediciones+1)})

    def finalizar(self,c):
        c.refresh_from_db()
        return s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())

    def generar(self,c,**extra):
        em=s.reservar_informe(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),**extra)
        s.generar_informe(em.pk);em.refresh_from_db();return em

    def texto_pdf(self,em):
        from pypdf import PdfReader
        with em.documento.archivo.open('rb') as f:
            return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(f.read())).pages)


@override_settings(**FLAGS)
class HojaCalibracionTests(HojaBase,TestCase):
    def test_ficha_por_configuracion_sin_validacion_ni_aprobacion(self):
        c=self.abrir();p=c.plantilla
        self.assertTrue(s.es_ficha(p));self.assertEqual((p.version,p.autor_id,p.tipo_equipo),(1,self.tecnico.pk,self.exp.registro.equipo.tipo_producto))
        self.assertFalse(ValidacionPlantillaCalibracion.objects.filter(plantilla=p).exists());self.assertFalse(p.aplicabilidades.exists())
        self.assertEqual(p.definicion['configuracion'],s.configuracion_equipo(self.exp.registro.equipo))
        self.assertEqual([r['nombre'] for r in p.definicion['parametros']],['Shim vertical superior','Shim vertical inferior','Shim horizontal','Backlash','Tolerancia',
            'Calibración total','Mínimo','Máximo','Presión eje vertical','Presión eje horizontal'])
        self.assertEqual([r['nombre'] for r in p.definicion['mediciones']],[f'Medición {n}' for n in range(1,17)])
        self.assertEqual(s.ficha_de(self.exp,self.tecnico).pk,p.pk)   # la misma ficha para la misma configuración
        self.assertEqual(self.abrir().pk,c.pk)   # una hoja en curso se continúa, no se duplica
        # Configuración distinta → ficha distinta; mayúsculas y espacios no cambian la configuración.
        eq=self.exp.registro.equipo;base=s.codigo_ficha(self.empresa,eq);marca=eq.marca
        eq.marca='  '+marca.upper()+'  ';self.assertEqual(s.codigo_ficha(self.empresa,eq),base);eq.marca=marca
        for campo in ('ratio','rotacion','modelo','marca'):
            anterior=getattr(eq,campo);setattr(eq,campo,(anterior or '')+' otra');self.assertNotEqual(s.codigo_ficha(self.empresa,eq),base,campo);setattr(eq,campo,anterior)
        # El prefijo es exclusivo de TALLER: ADMINISTRADOR no puede registrar un protocolo que se haga pasar por ficha.
        with self.assertRaises(ValidationError):
            s.crear_plantilla(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=protocolo_pdf(),datos=dict(codigo='ficha-falsa',titulo='X',
                tipo_equipo=eq.tipo_producto,definicion=definicion_qa(),proyecto_id=eq.proyecto_id))
        self.assertEqual(PlantillaCalibracion.objects.filter(codigo__startswith='FICHA-').count(),1)

    def test_taller_genera_la_hoja_sin_decision_de_administrador_y_sin_afirmar_conformidad(self):
        c=self.llenar(self.abrir());self.assertEqual((c.estado,c.mediciones.count()),('EN_PROCESO',16))
        c=self.finalizar(c)
        self.assertEqual((c.estado,bool(c.enviado_en)),('FINALIZADA',True));self.assertFalse(DecisionCalibracion.objects.exists())
        self.assertEqual(c.parametros['_equipo']['marca'],self.exp.registro.equipo.marca)   # datos del equipo congelados
        self.assertFalse(s.pdf_generado(c))
        em=self.generar(c);self.assertEqual((em.estado,em.version),('GENERADA',1));self.assertTrue(s.pdf_generado(c))
        snap=em.snapshot
        self.assertEqual((snap['plantilla'],snap['estado'],snap['decision']),('hoja-calibracion-v1','FINALIZADA',None))
        self.assertEqual(snap['verificacion']['estado'],'SIN_TOLERANCIA_APROBADA');self.assertIn('No se afirma conformidad técnica',snap['verificacion']['texto'])
        self.assertTrue(all(r['evaluacion']=='Sin tolerancia definida: no se evalúa' for r in snap['mediciones']))
        self.assertEqual(snap['unidades'],UNIDADES_QA);self.assertEqual([r['valor'] for r in snap['parametros']][:3],['0.5','0.55','0.6'])
        texto=self.texto_pdf(em)
        for palabra in ('DATOS GENERALES DE TRANSMISIÓN','DATOS DE SHIMS','DATOS DE CALIBRACIÓN','Presión eje horizontal','Sin tolerancia definida','No es certificado metrológico',
                'Anexos: ninguno registrado'): self.assertIn(palabra,texto,palabra)
        self.assertNotIn('ANEXO 1',texto)   # sin evidencias no hay páginas de anexos
        for prohibido in ('Dentro del rango','Fuera del rango','CONFORME'): self.assertNotIn(prohibido,texto,prohibido)
        # ADMINISTRADOR no decide sobre la hoja y la hoja finalizada no admite cambios.
        with self.assertRaises(ValidationError):
            s.decidir(usuario=self.planta,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),decision='APROBADA',motivo='No aplica',evaluaciones={})
        with self.assertRaises(PermissionDenied): self.llenar(c)
        c.refresh_from_db()
        with self.assertRaises(PermissionDenied),transaction.atomic(): c.parametros={};c.save()
        with self.assertRaises(PermissionDenied),transaction.atomic(): c.mediciones.first().delete()   # el borrado queda prohibido
        self.assertEqual(Calibracion.objects.count(),1)

    def test_pdf_principal_con_formato_fisico_y_anexos_numerados_al_final(self):
        from pypdf import PdfReader
        from .calibracion_pdf import construir_hoja
        c=self.llenar(self.abrir())
        s.evidencia(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),clase='FOTO',leyenda='Vista general tras la calibración',archivo=foto())
        c.refresh_from_db()
        s.evidencia(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),clase='CERTIFICADO',leyenda='Certificado del comparador',archivo=pdf_plano())
        c=self.finalizar(c);em=self.generar(c)
        with em.documento.archivo.open('rb') as f: lector=PdfReader(BytesIO(f.read()))
        paginas=[p.extract_text() for p in lector.pages];self.assertEqual(len(paginas),2)   # hoja principal + una página de anexos
        principal,anexos=paginas
        # 1ª parte: informe principal en el orden del formato físico, con firmas y QR al final
        self.assertIn('HOJA DE CALIBRACIÓN',principal)   # encabezado con logo (se dibuja en una segunda pasada: no entra en el orden del cuerpo)
        orden=['Día','Mes','Año','Hora','Cliente','DATOS GENERALES DE TRANSMISIÓN','DATOS DE SHIMS','DATOS DE CALIBRACIÓN','Mediciones (1 a 16)',
            'OBSERVACIONES','Anexos: 2 adjunto(s)','Firma del técnico responsable','Código de verificación']
        posiciones=[principal.index(x) for x in orden];self.assertEqual(posiciones,sorted(posiciones))
        self.assertIn('Medición 16',principal);self.assertIn('Página 1 de 2',principal);self.assertNotIn('ANEXO 1',principal)
        # 2ª parte: anexos numerados, con título, descripción y evidencia visual
        for texto in ('ANEXOS','ANEXO 1','FOTOGRAFÍA','Vista general tras la calibración','ANEXO 2','RESPALDO DOCUMENTAL','Certificado del comparador','Página 2 de 2'): self.assertIn(texto,anexos,texto)
        self.assertGreaterEqual(len(lector.pages[1].images),1)   # la fotografía va incrustada; el certificado se referencia por su huella
        # Lo que falta se ve y no se inventa: un dato del equipo sin registrar aparece como «No registrado».
        snap=dict(em.snapshot,version=em.version,huella=em.huella);snap['equipo']=dict(snap['equipo'],numero_serie='')
        texto=chr(10).join(p.extract_text() for p in PdfReader(BytesIO(construir_hoja(snap,[]))).pages)
        self.assertIn('No registrado',texto);self.assertIn('datos pendientes o no registrados',texto)

    def test_rangos_unidades_precision_y_campos_obligatorios(self):
        c=self.abrir()
        for parametros in (dict(BACKLASH='-0.1'),{'SHIM-H':'-1'},{'PRES-V':'-5'},dict(MINIMO='0.5',MAXIMO='0.1'),dict(_unidades=dict(longitud='cm',presion='psi')),
                dict(_unidades=dict(longitud='mm',presion='atm')),dict(BACKLASH='0.1234567890123'),dict(BACKLASH='abc'),dict(DESCONOCIDO='1')):
            with self.subTest(parametros=parametros),self.assertRaises(ValidationError):
                self.llenar(c,parametros=parametros)
        c.refresh_from_db();self.assertEqual((c.estado,c.parametros,c.mediciones.count()),('NO_INICIADA',{},0))   # nada inválido se guardó
        for punto,valor in (('MED-17','1'),('MED-01','1.0000000000001'),('MED-01','x')):
            with self.subTest(punto=punto,valor=valor),self.assertRaises(ValidationError):
                s.medir(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),punto=punto,valor=valor)
        self.assertFalse(c.mediciones.exists())
        # Mínimo y máximo negativos sí se admiten (referencias con signo), siempre que mínimo ≤ máximo.
        c=self.llenar(c,mediciones=13,parametros=dict(MINIMO='-0.05',MAXIMO='0.2',**{'PRES-H':None}))
        self.assertEqual((c.estado,c.mediciones.count()),('EN_PROCESO',13))
        # «Guardar avance» admite una hoja parcial; generarla exige todo y dice exactamente qué falta.
        with self.assertRaises(ValidationError) as error: self.finalizar(c)
        mensaje=' '.join(error.exception.messages);self.assertIn('Presión eje horizontal',mensaje);self.assertIn('Mediciones 14, 15, 16',mensaje)
        c.refresh_from_db();self.assertEqual((c.estado,c.enviado_en),('EN_PROCESO',None))
        c=self.llenar(c,parametros=dict(_unidades=dict(longitud='mm',presion='')))
        with self.assertRaisesMessage(ValidationError,'Unidad de presión'): self.finalizar(c)
        c=self.llenar(c);self.assertEqual(Decimal(c.parametros['PRES-H']),Decimal('40'))
        self.assertEqual(self.finalizar(c).estado,'FINALIZADA')

    def test_autoria_idempotencia_versiones_y_guardas_de_base_de_datos(self):
        from django.db import DatabaseError
        otro=usuario(self.empresa,'otro-taller-qa','TALLER');c=self.abrir()
        for intento in (lambda:self.llenar(c,usuario=otro),lambda:s.iniciar_hoja(usuario=otro,**self.base,clave=uuid.uuid4()),
                lambda:s.iniciar_hoja(usuario=self.planta,**self.base,clave=uuid.uuid4())):
            with self.assertRaises((PermissionDenied,ValidationError)): intento()
        eventos=lambda:Auditoria.objects.filter(expediente=self.exp,accion__startswith='CAL_').count()
        clave=uuid.uuid4();version=c.version_edicion
        datos=dict(fecha=timezone.now(),observaciones='Obs',parametros=dict(VALORES_HOJA,_unidades=dict(UNIDADES_QA)))
        medidas={f'MED-{n:02}':Decimal(n) for n in range(1,17)}
        c1=s.guardar_formato(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=version,clave=clave,datos=datos,mediciones=medidas);n1=eventos()
        c2=s.guardar_formato(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=version,clave=clave,datos=datos,mediciones=medidas)   # reintento de red
        self.assertEqual((c1.pk,eventos(),c.mediciones.count()),(c2.pk,n1,16))
        # Finalizar es idempotente y deja un único evento; la misma clave con otro contenido es un conflicto.
        c=Calibracion.objects.get(pk=c.pk);clave_f=uuid.uuid4();v=c.version_edicion
        f1=s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=v,clave=clave_f);n2=eventos()
        f2=s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=v,clave=clave_f)
        self.assertEqual((f1.pk,f1.estado,eventos()),(f2.pk,'FINALIZADA',n2));self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='CAL_FINALIZADA').count(),1)
        with self.assertRaises(ValidationError): s.enviar(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=v+7,clave=clave_f)
        # PDF: una versión por contenido; una versión nueva solo con motivo y la anterior se conserva.
        c=Calibracion.objects.get(pk=c.pk);em=self.generar(c)
        self.assertEqual(s.reservar_informe(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4()).pk,em.pk)
        with self.assertRaises(ValidationError): s.reservar_informe(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),nueva_version=True)
        em2=s.reservar_informe(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,clave=uuid.uuid4(),nueva_version=True,motivo='Reimpresión solicitada')
        self.assertEqual((em2.version,em.pk!=em2.pk,c.emisiones.count()),(2,True,2))
        # Guardas de base de datos (también frente a SQL directo): estados fuera de lista.
        with self.assertRaises(DatabaseError),transaction.atomic(): Calibracion.objects.filter(pk=c.pk).update(estado='OTRA')
        # Un protocolo heredado sigue exigiendo su validación independiente: sin ella la base rechaza la calibración.
        with self.assertRaises(DatabaseError),transaction.atomic():
            Calibracion(expediente=self.exp,diagnostico=c.diagnostico,autorizacion=c.autorizacion,plantilla=self.p,reparacion_snapshot=c.reparacion_snapshot,
                revision=9,tecnico=self.tecnico,clave=uuid.uuid4()).save()

    def test_informe_final_exige_el_pdf_de_la_hoja_en_la_base_de_datos(self):
        from django.db import DatabaseError
        from .models import InformeFinal
        c=self.finalizar(self.llenar(self.abrir()))
        nuevo=lambda:InformeFinal(expediente=self.exp,calibracion=c,version=1,snapshot={},huella='a'*64,estado='BORRADOR',autor=self.tecnico)
        with self.assertRaises(DatabaseError),transaction.atomic(): nuevo().save()   # hoja finalizada sin PDF generado
        self.generar(c)
        with transaction.atomic(): nuevo().save()   # con el tercer documento generado, la base lo acepta
        self.assertEqual(InformeFinal.objects.filter(calibracion=c).count(),1)

    def test_finalizada_exige_enviado_en_en_la_base_de_datos(self):
        from django.db import DatabaseError
        c=self.abrir()   # hoja en borrador: la base no admite FINALIZADA sin haber sido generada por TALLER
        with self.assertRaises(DatabaseError),transaction.atomic(): Calibracion.objects.filter(pk=c.pk).update(estado='FINALIZADA')
        c.refresh_from_db();self.assertEqual(c.estado,'NO_INICIADA')


@override_settings(**FLAGS)
class HojaToleranciasAprobadasTests(HojaBase,TestCase):
    PROTOCOLO=dict(unidad='mm',maximo='0.20')

    def test_tolerancias_aprobadas_verifican_las_mediciones_sin_declarar_conformidad(self):
        empresa,tecnico,exp,revisor,p=self.empresa,self.tecnico,self.exp,self.revisor,self.p   # protocolo ficticio con unidad y rango numéricos (-1 a 0.20 mm)
        self.assertIsNone(s.tolerancias_aprobadas(exp))   # sin validar ni anclar: no se usa
        c=s.iniciar_hoja(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,clave=uuid.uuid4())
        c=s.guardar_formato(usuario=tecnico,empresa=empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),
            datos=dict(fecha=timezone.now(),parametros=dict(VALORES_HOJA,_unidades=dict(UNIDADES_QA))),
            mediciones={f'MED-{n:02}':Decimal('0.10')+Decimal(n)/100 for n in range(1,17)})
        with override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[anchor_qa(p)]):
            validar_qa(empresa,revisor,p)
            self.assertEqual(s.tolerancias_aprobadas(exp).pk,p.pk)
            c=s.enviar(usuario=tecnico,empresa=empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4())
            em=s.reservar_informe(usuario=tecnico,empresa=empresa,calibracion_id=c.pk,clave=uuid.uuid4());snap=em.snapshot
        self.assertEqual(snap['verificacion']['estado'],'TOLERANCIA_APROBADA');self.assertEqual(snap['verificacion']['protocolo'],p.codigo)
        evaluaciones=[r['evaluacion'] for r in snap['mediciones']]   # valores 0.11 … 0.26 frente a un rango aprobado de -1 a 0.20
        self.assertEqual(evaluaciones[:10],['Dentro del rango aprobado']*10);self.assertEqual(evaluaciones[10:],['Fuera del rango aprobado']*6)
        self.assertIn('no sustituye la valoración técnica',snap['verificacion']['texto'])
        self.assertEqual(c.parametros['_tolerancias']['protocolo'],p.codigo)   # las tolerancias usadas quedan congeladas con la hoja
        s.generar_informe(em.pk);em.refresh_from_db();texto=self.texto_pdf(em)   # el PDF muestra la verificación por medición con el criterio aprobado
        self.assertEqual((texto.count('Dentro del rango aprobado'),texto.count('Fuera del rango aprobado')),(10,6));self.assertIn(p.codigo,texto)
        # La verificación solo es aritmética: otra unidad, o límites no numéricos, no se evalúan ni se inventan equivalencias.
        self.assertEqual(s._evaluar('0.1',dict(minimo='0',maximo='1',unidad='in'),'mm'),'Unidad distinta a la aprobada: no se evalúa')
        self.assertEqual(s._evaluar('0.1',dict(minimo='',maximo='1',unidad='mm'),'mm'),'Sin límites numéricos aprobados: no se evalúa')
        self.assertEqual(s._evaluar('0.1',None,'mm'),'Sin tolerancia definida: no se evalúa')

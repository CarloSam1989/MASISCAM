import hashlib
import importlib.util
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO,StringIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection,connections,transaction,IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase,TransactionTestCase,override_settings,Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import Empresa,Perfil
from .models import *
from . import tecnica_services as s
from .recepcion_services import abrir_recepcion
from .recepcion_confirmacion import confirmar_recepcion
from .test_recepcion import fixtures,argumentos,foto


def usuario(empresa,nombre,rol):
    user=get_user_model().objects.create_user(username=nombre,password='QA-only-12345')
    perfil=Perfil.objects.create(user=user,empresa=empresa)
    RolMasiscam.objects.create(perfil=perfil,rol=rol)
    return user


def preparar():
    empresa,oficina,rol,cliente,equipo=fixtures()
    planta=usuario(empresa,'planta-qa','PLANTA')
    tecnico=usuario(empresa,'tecnico-qa','TECNICO')
    exp=abrir_recepcion(**argumentos(empresa,oficina,cliente,equipo,fotografias=[foto()]))
    confirmar_recepcion(usuario=oficina,empresa=empresa,expediente_id=exp.pk,clave=uuid.uuid4(),placa='SIN_PLACA',
        motivo_sin_placa='Equipo de prueba sin placa',motivo_sin_guia='Entrega ficticia sin guía',general_id=exp.recepcion.evidencias.get().pk)
    s.asignar_tecnico(usuario=planta,empresa=empresa,expediente_id=exp.pk,tecnico_id=tecnico.pk,motivo='Asignación QA',clave=uuid.uuid4())
    return empresa,oficina,planta,tecnico,exp


def inspeccion_enviada(empresa,tecnico,exp):
    i=s.crear_revision(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,tipo='INSPECCION',clave=uuid.uuid4())
    i=s.guardar_revision(usuario=tecnico,empresa=empresa,tipo='INSPECCION',revision_id=i.pk,version=i.version_edicion,clave=uuid.uuid4(),
        datos=dict(fecha=timezone.localdate(),estado_fisico='Estado observado QA',condiciones_generales='Condiciones QA',hallazgos='Hallazgos descritos QA',motivo_sin_evidencia='Sin fotos adicionales; pendiente de documentación.'))
    return s.enviar_revision(usuario=tecnico,empresa=empresa,tipo='INSPECCION',revision_id=i.pk,version=i.version_edicion,clave=uuid.uuid4())


def diagnostico_enviado(empresa,tecnico,exp):
    d=s.crear_revision(usuario=tecnico,empresa=empresa,expediente_id=exp.pk,tipo='DIAGNOSTICO',clave=uuid.uuid4())
    d=s.guardar_revision(usuario=tecnico,empresa=empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=d.version_edicion,clave=uuid.uuid4(),datos=dict(
        fecha=timezone.localdate(),fallas='Falla no determinada en esta demostración',causas='Hipótesis no validada',componentes_afectados='Pendientes de identificación',recomendaciones='Continuar evaluación autorizada',conclusiones='Alcance limitado QA',repuestos='SIN_REPUESTOS',motivo_sin_evidencia='Sin evidencia nueva en QA'))
    return s.enviar_revision(usuario=tecnico,empresa=empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=d.version_edicion,clave=uuid.uuid4())


def pdf_plano():
    from reportlab.pdfgen.canvas import Canvas
    stream=BytesIO()
    c=Canvas(stream)
    c.drawString(40,750,'PLANO FICTICIO QA - NO INDUSTRIAL')
    c.save()
    return SimpleUploadedFile('plano-qa.pdf',stream.getvalue(),content_type='application/pdf')


@skipUnless(importlib.util.find_spec('reportlab'),'ReportLab requerido')
@override_settings(MASISCAM_TECNICA_LOCAL=True,MASISCAM_RECEPCION_LOCAL=True,GOOGLE_DRIVE_ENABLED=False)
class TecnicaTests(TestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp=preparar()
        self.base=dict(empresa=self.empresa,expediente_id=self.exp.pk)

    def crear_i(self):
        return s.crear_revision(usuario=self.tecnico,**self.base,tipo='INSPECCION',clave=uuid.uuid4())

    def aprobar(self):
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=diagnostico_enviado(self.empresa,self.tecnico,self.exp)
        decision=s.decidir_diagnostico(usuario=self.planta,empresa=self.empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
        d.refresh_from_db()
        return d,decision

    def referencia(self):
        r=s.guardar_referencia(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(
            codigo='REF-QA',tipo='PLANO',titulo='Plano de prueba',procedencia='Fuente QA sintética',autorizacion_uso='Uso y congelación QA expresamente indicados',proyecto_id=self.exp.registro.equipo.proyecto_id))
        p=s.guardar_parte(usuario=self.planta,empresa=self.empresa,referencia_id=r.pk,clave=uuid.uuid4(),datos=dict(
            posicion='A',descripcion='Componente QA',numero_parte='QA-PARTE',unidad='unidad declarada'))
        s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=r.pk,estado='PUBLICADA',clave=uuid.uuid4())
        s.aplicar_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=r.pk,equipo_id=self.exp.registro.equipo_id,fundamento='Aplicabilidad de prueba declarada',clave=uuid.uuid4())
        r.refresh_from_db()
        return r,ParteReferenciaTecnica.objects.get(pk=p)

    def test_flujo_completo_pdf_inmutable_y_qr(self):
        qr=self.exp.registro.equipo.token_publico
        d,_=self.aprobar()
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
        doc_id=s.generar_informe(em.pk)
        self.assertEqual(s.generar_informe(em.pk),doc_id)
        em.refresh_from_db()
        with em.documento.archivo.open('rb') as f:
            data=f.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(),em.hash_sha256)
        self.assertEqual(em.estado,'GENERADA')
        self.assertFalse(em.documento.publico)
        d.refresh_from_db()
        self.assertEqual(d.estado,'FINALIZADO')
        self.assertEqual(self.exp.registro.equipo.token_publico,qr)
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp,accion='INFORME_INICIAL_GENERADO').count(),1)
        if importlib.util.find_spec('pypdf'):
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(data))
            text='\n'.join(page.extract_text() for page in reader.pages)
            self.assertIn('Este informe no autoriza reparaciones',text)
            self.assertIn(self.exp.numero,text)
            for page in reader.pages:
                self.assertAlmostEqual(float(page.mediabox.width),595.276,places=2)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Documento.objects.filter(pk=doc_id).update(publico=True)

    def test_recepcion_y_asignacion_obligatorias(self):
        exp=abrir_recepcion(**argumentos(self.empresa,self.oficina,self.exp.cliente_contractual,self.exp.registro.equipo))
        with self.assertRaises(PermissionDenied):
            s.crear_revision(usuario=self.tecnico,empresa=self.empresa,expediente_id=exp.pk,tipo='INSPECCION',clave=uuid.uuid4())
        s.asignar_tecnico(usuario=self.planta,empresa=self.empresa,expediente_id=exp.pk,tecnico_id=self.tecnico.pk,motivo='QA',clave=uuid.uuid4())
        with self.assertRaises(ValidationError):
            s.crear_revision(usuario=self.tecnico,empresa=self.empresa,expediente_id=exp.pk,tipo='INSPECCION',clave=uuid.uuid4())

    def test_borrador_incompleto_envio_rechaza_cada_minimo(self):
        i=self.crear_i()
        with self.assertRaises(ValidationError) as exc:
            s.enviar_revision(usuario=self.tecnico,empresa=self.empresa,tipo='INSPECCION',revision_id=i.pk,version=1,clave=uuid.uuid4())
        self.assertIn('estado_fisico',exc.exception.message_dict)
        self.assertIn('fecha',exc.exception.message_dict)
        self.assertIn('motivo_sin_evidencia',exc.exception.message_dict)
        i.refresh_from_db()
        self.assertIsNone(i.enviado_en)

    def test_revision_optimista_idempotencia_y_campos_manipulados(self):
        i=self.crear_i()
        key=uuid.uuid4()
        args=dict(usuario=self.tecnico,empresa=self.empresa,tipo='INSPECCION',revision_id=i.pk,version=1,clave=key,datos={'hallazgos':'QA'})
        s.guardar_revision(**args)
        self.assertEqual(s.guardar_revision(**args).version_edicion,2)
        for changed in ({'clave':uuid.uuid4()},{'datos':{'hallazgos':'Otro'}},{'clave':uuid.uuid4(),'version':2,'datos':{'tecnico_id':self.planta.pk}}):
            with self.assertRaises(ValidationError):
                s.guardar_revision(**{**args,**changed})

    def test_roles_sin_bypass_y_empresa_ajena(self):
        for role in ('OFICINA','GERENCIA','ADMIN','CONSULTA'):
            u=usuario(self.empresa,'rol-'+role,role)
            u.is_superuser=True
            u.save()
            with self.assertRaises(PermissionDenied):
                s.crear_revision(usuario=u,**self.base,tipo='INSPECCION',clave=uuid.uuid4())
            with self.assertRaises(PermissionDenied):
                s.asignar_tecnico(usuario=u,**self.base,tecnico_id=self.tecnico.pk,motivo='QA',clave=uuid.uuid4())
        ajena=Empresa.objects.create(nombre='QA ajena')
        with self.assertRaises(PermissionDenied):
            s.expediente_autorizado(self.planta,ajena,self.exp.pk)

    def test_reasignacion_no_transfiere_autoria(self):
        i=self.crear_i()
        nuevo=usuario(self.empresa,'tecnico-nuevo','TECNICO')
        args=dict(usuario=self.planta,**self.base,tecnico_id=nuevo.pk,motivo='Reasignación QA',clave=uuid.uuid4())
        s.asignar_tecnico(**args)
        self.assertEqual(s.asignar_tecnico(**args).tecnico,nuevo)
        self.assertEqual(AsignacionTecnica.objects.filter(fin__isnull=True).count(),1)
        for user in (self.tecnico,nuevo):
            with self.assertRaises(PermissionDenied):
                s.guardar_revision(usuario=user,empresa=self.empresa,tipo='INSPECCION',revision_id=i.pk,version=1,clave=uuid.uuid4(),datos={})
        revision=s.crear_revision(usuario=self.planta,**self.base,tipo='INSPECCION',clave=uuid.uuid4(),motivo='Continuación por nuevo autor')
        self.assertEqual(revision.tecnico,nuevo)
        self.assertEqual(revision.sustituye,i)

    def test_autoaprobacion_cambio_rol_y_sql(self):
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=diagnostico_enviado(self.empresa,self.tecnico,self.exp)
        rol=RolMasiscam.objects.get(perfil__user=self.tecnico,perfil__empresa=self.empresa)
        rol.rol='PLANTA'
        rol.save()
        with self.assertRaises(PermissionDenied):
            s.decidir_diagnostico(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
        with self.assertRaises(IntegrityError),transaction.atomic():
            DecisionTecnica.objects.create(diagnostico=d,decision='APROBADO',responsable=self.tecnico)

    def test_observar_rechazar_corregir_y_conservar(self):
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=diagnostico_enviado(self.empresa,self.tecnico,self.exp)
        with self.assertRaises(ValidationError):
            s.decidir_diagnostico(usuario=self.planta,empresa=self.empresa,diagnostico_id=d.pk,decision='OBSERVADO',motivo=' ',clave=uuid.uuid4())
        for decision in ('OBSERVADO','RECHAZADO','APROBADO'):
            key=uuid.uuid4()
            args=dict(usuario=self.planta,empresa=self.empresa,diagnostico_id=d.pk,decision=decision,motivo='Revisión QA',clave=key)
            result=s.decidir_diagnostico(**args)
            self.assertEqual(s.decidir_diagnostico(**args).pk,result.pk)
            with self.assertRaises(PermissionDenied),transaction.atomic():
                result.delete()
            if decision!='APROBADO':
                nuevo=s.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Corrección QA')
                self.assertEqual(nuevo.sustituye_id,d.pk)
                d=s.enviar_revision(usuario=self.tecnico,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=nuevo.pk,version=nuevo.version_edicion,clave=uuid.uuid4())
        self.assertEqual(DecisionTecnica.objects.count(),3)

    def test_enviados_inmutables_y_padres_protegidos(self):
        i=inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Inspeccion.objects.filter(pk=i.pk).update(hallazgos='Alterado')
        with self.assertRaises(PermissionDenied),transaction.atomic():
            EvidenciaTecnica.objects.create(inspeccion=i,documento=self.exp.recepcion.evidencias.first().documento,clase='HALLAZGO',leyenda='QA',autor=self.tecnico)
        with self.assertRaises((PermissionDenied,ProtectedError)),transaction.atomic():
            i.delete()

    def test_evidencia_reutilizada_validacion_hash_y_envio(self):
        i=self.crear_i()
        doc=self.exp.recepcion.evidencias.get().documento
        args=dict(usuario=self.tecnico,empresa=self.empresa,tipo='INSPECCION',revision_id=i.pk,version=1,clave=uuid.uuid4(),clase='ESTADO_FISICO',leyenda='Foto de recepción citada',documento_id=doc.pk)
        ev=s.agregar_evidencia(**args)
        self.assertEqual(s.agregar_evidencia(**args).pk,ev.pk)
        self.assertEqual(ev.documento_id,doc.pk)
        i.refresh_from_db()
        with self.assertRaises(ValidationError):
            s.agregar_evidencia(**{**args,'clave':uuid.uuid4(),'version':i.version_edicion,'documento_id':None,'archivo':SimpleUploadedFile('f.png',b'fake',content_type='image/png')})
        with doc.archivo.storage.open(doc.archivo.name,'wb') as f:
            f.write(b'QA CORRUPT')
        with self.assertRaises(ValidationError):
            s.agregar_evidencia(**{**args,'clave':uuid.uuid4(),'version':i.version_edicion})

    def test_biblioteca_versiones_retiro_y_componentes(self):
        ref,parte=self.referencia()
        self.assertIn(ref,s.referencias_visibles(self.tecnico,self.empresa))
        with self.assertRaises(IntegrityError),transaction.atomic():
            ParteReferenciaTecnica.objects.filter(pk=parte.pk).update(descripcion='Cambiar')
        with self.assertRaises(IntegrityError),transaction.atomic():
            ReferenciaTecnica.objects.filter(pk=ref.pk).update(titulo='Cambiar')
        comp=s.guardar_componente(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),datos=dict(codigo='C-QA',descripcion='Componente sin número'))
        self.assertEqual(comp.numero_parte,'')
        new=s.guardar_referencia(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),datos=dict(
            codigo=ref.codigo,tipo='PLANO',titulo='Nueva versión',procedencia='Misma fuente QA',autorizacion_uso='Uso QA autorizado',documento_id=ref.documento_id,sustituye_id=ref.pk))
        self.assertEqual(new.revision,2)
        self.assertEqual(new.partes.count(),1)
        s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,estado='RETIRADA',clave=uuid.uuid4())
        self.assertNotIn(ref,s.referencias_visibles(self.tecnico,self.empresa))

    def test_documento_publico_y_fuentes_ajenas_no_se_admiten(self):
        ref,_=self.referencia()
        with self.assertRaises(IntegrityError),transaction.atomic():
            Documento.objects.filter(pk=ref.documento_id).update(publico=True)
        args=dict(usuario=self.oficina,empresa=self.empresa,clave=uuid.uuid4(),datos={})
        with self.assertRaises(PermissionDenied):
            s.guardar_referencia(**args)
        self.client.force_login(self.tecnico)
        response=self.client.get(reverse('masiscam:documento_privado',args=[ref.documento.proyecto_id,ref.documento_id]))
        self.assertEqual(response.status_code,403)
        r=self.client.get(reverse('masiscam:biblioteca_documento',args=[ref.pk])+'?ver=1')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r['X-Frame-Options'],'SAMEORIGIN')
        b''.join(r.streaming_content)

    def test_biblioteca_entre_proyectos_y_sin_acceso_empresa_ajena(self):
        proyecto=Proyecto.objects.create(empresa=self.empresa,codigo='OTRO-QA',nombre='Origen biblioteca',responsable='QA',fecha_inicio=timezone.localdate(),creado_por=self.planta)
        ref=s.guardar_referencia(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(codigo='OTRA-REF',tipo='PLANO',titulo='Plano compartido autorizado',procedencia='QA',autorizacion_uso='Uso privado compartido autorizado',proyecto_id=proyecto.pk))
        s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,estado='PUBLICADA',clave=uuid.uuid4())
        self.assertNotIn(ref,s.referencias_visibles(self.tecnico,self.empresa))
        s.aplicar_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,equipo_id=self.exp.registro.equipo_id,fundamento='Fuente QA explícita',clave=uuid.uuid4())
        self.assertIn(ref,s.referencias_visibles(self.tecnico,self.empresa))
        self.client.force_login(self.tecnico)
        legacy=self.client.get(reverse('masiscam:proyecto_detalle',args=[proyecto.pk]))
        self.assertNotContains(legacy,'Plano compartido autorizado')
        response=self.client.get(reverse('masiscam:biblioteca_documento',args=[ref.pk]))
        self.assertEqual(response.status_code,200)
        b''.join(response.streaming_content)
        ajena=Empresa.objects.create(nombre='Otra empresa biblioteca')
        outsider=usuario(ajena,'biblioteca-ajena','PLANTA')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse('masiscam:biblioteca_documento',args=[ref.pk])).status_code,404)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Proyecto.objects.filter(pk=proyecto.pk).update(empresa=ajena)

    def test_no_admite_documento_publico_ni_modifica_visibilidad(self):
        proyecto=self.exp.registro.equipo.proyecto
        doc=Documento(proyecto=proyecto,categoria='PLANOS',titulo='Público QA',nombre_original='p.pdf',tipo_mime='application/pdf',tamano=1,hash_sha256='e'*64,subido_por=self.planta,publico=True)
        doc.archivo.save('publico-qa.pdf',pdf_plano(),save=False)
        doc.save()
        with self.assertRaises(PermissionDenied):
            s.guardar_referencia(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),datos=dict(codigo='PUBLICO',tipo='PLANO',titulo='QA',procedencia='QA',autorizacion_uso='Solicitud QA',documento_id=doc.pk))
        doc.refresh_from_db()
        self.assertTrue(doc.publico)

    def test_autor_fuente_no_puede_aprobar_revision_copiada(self):
        i=inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        nuevo=usuario(self.empresa,'otro-autor','TECNICO')
        s.asignar_tecnico(usuario=self.planta,**self.base,tecnico_id=nuevo.pk,motivo='Nuevo autor',clave=uuid.uuid4())
        nueva=s.crear_revision(usuario=self.planta,**self.base,tipo='INSPECCION',clave=uuid.uuid4(),motivo='Revisión de fuente')
        s.enviar_revision(usuario=nuevo,empresa=self.empresa,tipo='INSPECCION',revision_id=nueva.pk,version=1,clave=uuid.uuid4())
        d=diagnostico_enviado(self.empresa,nuevo,self.exp)
        rol=RolMasiscam.objects.get(perfil__user=self.tecnico,perfil__empresa=self.empresa)
        rol.rol='PLANTA'
        rol.save()
        with self.assertRaises(PermissionDenied):
            s.decidir_diagnostico(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
        with self.assertRaises(IntegrityError),transaction.atomic():
            DecisionTecnica.objects.create(diagnostico=d,decision='APROBADO',responsable=self.tecnico)

    def test_comando_recuperacion_solo_lista_por_defecto(self):
        from django.core.management import call_command
        d,_=self.aprobar()
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
        with patch('masiscam.tecnica_tasks.generar_informe_inicial.apply_async') as queue:
            call_command('reintentar_informes_tecnicos',stdout=StringIO())
            queue.assert_not_called()
        self.assertEqual(EmisionInformeTecnico.objects.get(pk=em.pk).estado,'RESERVADA')

    def test_informe_corrupto_no_se_regenera_ni_sincroniza(self):
        from .tecnica_tasks import generar_informe_inicial
        d,_=self.aprobar()
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
        doc=Documento.objects.get(pk=s.generar_informe(em.pk))
        with doc.archivo.storage.open(doc.archivo.name,'wb') as stream:
            stream.write(b'QA archivo alterado')
        with patch('masiscam.services.encolar_drive') as queue:
            with self.assertRaises(ValidationError):
                generar_informe_inicial(em.pk)
            queue.assert_not_called()
        self.assertEqual(EmisionInformeTecnico.objects.count(),1)
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(reverse('masiscam:tecnica_documento',args=[self.exp.pk,doc.pk])).status_code,503)

    def test_cita_manual_versionada_sin_repuestos_y_retiro_historico(self):
        ref=s.guardar_referencia(usuario=self.planta,empresa=self.empresa,clave=uuid.uuid4(),archivo=pdf_plano(),datos=dict(codigo='MANUAL-QA',tipo='MANUAL',titulo='Manual de prueba',procedencia='QA',autorizacion_uso='Uso y congelación autorizados QA',proyecto_id=self.exp.registro.equipo.proyecto_id))
        s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,estado='PUBLICADA',clave=uuid.uuid4())
        s.aplicar_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,equipo_id=self.exp.registro.equipo_id,fundamento='Manual fuente declarado QA',clave=uuid.uuid4())
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=s.crear_revision(usuario=self.tecnico,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4())
        s.agregar_evidencia(usuario=self.tecnico,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=1,clave=uuid.uuid4(),clase='REFERENCIA',leyenda='Manual fuente',referencia_id=ref.pk)
        d.refresh_from_db()
        d=s.guardar_revision(usuario=self.tecnico,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=d.version_edicion,clave=uuid.uuid4(),datos=dict(fecha=timezone.localdate(),fallas='Pendiente',causas='No determinada',componentes_afectados='Pendientes',recomendaciones='Evaluar',conclusiones='Limitado',repuestos='SIN_REPUESTOS'))
        s.enviar_revision(usuario=self.tecnico,empresa=self.empresa,tipo='DIAGNOSTICO',revision_id=d.pk,version=d.version_edicion,clave=uuid.uuid4())
        s.decidir_diagnostico(usuario=self.planta,empresa=self.empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
        s.generar_informe(em.pk)
        self.assertEqual(em.snapshot['evidencias'][0]['referencia']['codigo'],'MANUAL-QA')
        s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,estado='RETIRADA',clave=uuid.uuid4())
        self.assertIn(ref,s.referencias_visibles(self.tecnico,self.empresa))

    def test_fecha_html_iso_y_fuente_sustituida_visible(self):
        d,_=self.aprobar()
        self.client.force_login(self.planta)
        html=self.client.get(reverse('masiscam:tecnica_revision',args=['DIAGNOSTICO',d.pk]))
        self.assertContains(html,f'value="{timezone.localdate().isoformat()}"')
        s.crear_revision(usuario=self.planta,**self.base,tipo='INSPECCION',clave=uuid.uuid4(),motivo='Revisar fuente')
        response=self.client.get(reverse('masiscam:tecnica_expediente',args=[self.exp.pk]))
        self.assertContains(response,'Fuente sustituida')
        self.assertFalse(s.vigente(d))

    def test_piezas_biblioteca_manual_decimal_y_snapshot(self):
        ref,parte=self.referencia()
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=s.crear_revision(usuario=self.tecnico,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4())
        data=dict(origen='BIBLIOTECA',parte_id=parte.pk,cantidad='1.123456',condicion='Evaluación pendiente',accion='Evaluar',unidad='')
        p=s.agregar_pieza(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,version=1,clave=uuid.uuid4(),datos=data)
        self.assertEqual(p.descripcion,parte.descripcion)
        self.assertEqual(p.cantidad,Decimal('1.123456'))
        self.assertEqual(p.snapshot['revision'],1)
        d.refresh_from_db()
        manual=dict(origen='MANUAL',descripcion='Pieza pendiente',cantidad='2',unidad='',motivo_unidad_pendiente='Fuente no indica unidad',condicion='Pendiente identificación',accion='Evaluar con fuente',motivo_manual='Sin catálogo')
        s.agregar_pieza(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,version=d.version_edicion,clave=uuid.uuid4(),datos=manual)
        for value in ('0','-1','1.1234567','NaN','100000000000000'):
            d.refresh_from_db()
            with self.assertRaises(ValidationError):
                s.agregar_pieza(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,version=d.version_edicion,clave=uuid.uuid4(),datos={**manual,'cantidad':value})

    def test_fuente_retirada_o_incompatible_bloquea_seleccion(self):
        ref,parte=self.referencia()
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=s.crear_revision(usuario=self.tecnico,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4())
        s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,estado='RETIRADA',clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            s.agregar_pieza(usuario=self.tecnico,empresa=self.empresa,diagnostico_id=d.pk,version=1,clave=uuid.uuid4(),datos=dict(origen='BIBLIOTECA',parte_id=parte.pk,cantidad='1',condicion='QA',accion='QA'))

    def test_informe_solo_aprobado_y_version_nueva_explicita(self):
        d,_=self.aprobar()
        args=dict(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk)
        em=s.reservar_informe(**args,clave=uuid.uuid4())
        self.assertEqual(s.reservar_informe(**args,clave=uuid.uuid4()).pk,em.pk)
        s.generar_informe(em.pk)
        em2=s.reservar_informe(**args,clave=uuid.uuid4(),nueva_version=True,motivo='Ajuste presentación autorizado QA')
        self.assertEqual(em2.version,2)
        s.generar_informe(em2.pk)
        self.assertEqual(EmisionInformeTecnico.objects.count(),2)
        s.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Revisión nueva')
        with self.assertRaises(ValidationError):
            s.reservar_informe(**args,clave=uuid.uuid4())

    def test_fallos_storage_falso_exito_reintento_y_cola(self):
        from .tecnica_tasks import generar_informe_inicial
        d,_=self.aprobar()
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
        for method,effect in (('save',OSError('QA disco')),('_save',lambda name,content:name)):
            with patch.object(FileSystemStorage,method,side_effect=effect):
                with self.assertRaises((ValidationError,OSError)):
                    generar_informe_inicial(em.pk)
            em.refresh_from_db()
            self.assertEqual(em.estado,'ERROR')
            self.assertIsNone(em.documento_id)
        with override_settings(GOOGLE_DRIVE_ENABLED=True),patch('masiscam.services.encolar_drive',return_value=False):
            doc_id=generar_informe_inicial(em.pk)
        self.assertEqual(generar_informe_inicial(em.pk),doc_id)
        self.assertEqual(EmisionInformeTecnico.objects.count(),1)
        with override_settings(GOOGLE_DRIVE_ENABLED=True), patch('masiscam.services.encolar_drive',return_value=False) as queue, self.captureOnCommitCallbacks(execute=True):
            ref,_=self.referencia()
        queue.assert_called_once()
        self.assertEqual(ref.estado,'PUBLICADA')
        with patch('masiscam.services.encolar_drive',return_value=False) as retry, self.captureOnCommitCallbacks(execute=True):
            s.reintentar_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=ref.pk,clave=uuid.uuid4())
        retry.assert_called_once()

    def test_cambio_revision_durante_render_no_publica(self):
        from .tecnica_pdf import construir_informe
        d,_=self.aprobar()
        em=s.reservar_informe(usuario=self.oficina,empresa=self.empresa,diagnostico_id=d.pk,clave=uuid.uuid4())
        def render(*args):
            pdf=construir_informe(*args)
            s.crear_revision(usuario=self.planta,**self.base,tipo='DIAGNOSTICO',clave=uuid.uuid4(),motivo='Cambió alcance QA')
            return pdf
        with patch('masiscam.tecnica_pdf.construir_informe',side_effect=render):
            with self.assertRaises(ValidationError):
                s.generar_informe(em.pk)
        em.refresh_from_db()
        self.assertIsNone(em.documento_id)

    def test_vistas_csrf_estados_y_no_efectos_get(self):
        i=self.crear_i()
        self.client.force_login(self.tecnico)
        for url in (reverse('masiscam:tecnica_inicio'),reverse('masiscam:tecnica_expediente',args=[self.exp.pk]),reverse('masiscam:tecnica_revision',args=['INSPECCION',i.pk]),reverse('masiscam:biblioteca')):
            self.assertEqual(self.client.get(url).status_code,200)
        strict=Client(enforce_csrf_checks=True)
        strict.force_login(self.tecnico)
        url=reverse('masiscam:tecnica_revision',args=['INSPECCION',i.pk])
        self.assertEqual(strict.post(url,{'op':'guardar'}).status_code,403)
        response=self.client.post(url,{'op':'enviar','clave':uuid.uuid4(),'version':1})
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'obligatorio',status_code=400)
        self.assertEqual(self.client.get(reverse('masiscam:tecnica_informe',args=[1])).status_code,405)
        self.assertEqual(self.client.post(url,{'op':'enviar','clave':uuid.uuid4()}).status_code,400)
        self.assertEqual(self.client.post(url,{'op':'enviar','clave':uuid.uuid4(),'version':'manipulada'}).status_code,400)

    @override_settings(MASISCAM_TECNICA_LOCAL=False)
    def test_funcion_local_desactivada_por_defecto(self):
        self.client.force_login(self.planta)
        self.assertEqual(self.client.get(reverse('masiscam:tecnica_inicio')).status_code,404)
        with self.assertRaises(PermissionDenied):
            s.autorizar_tecnica(self.planta,self.empresa)


@skipUnless(connection.vendor=='postgresql' and importlib.util.find_spec('reportlab'),'Requiere PostgreSQL 16 real y ReportLab')
@override_settings(MASISCAM_TECNICA_LOCAL=True,MASISCAM_RECEPCION_LOCAL=True,GOOGLE_DRIVE_ENABLED=False)
class TecnicaConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.empresa,self.oficina,self.planta,self.tecnico,self.exp=preparar()

    def test_doble_envio_y_decision_conexiones_independientes(self):
        i=inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=diagnostico_enviado(self.empresa,self.tecnico,self.exp)
        barrier,key=Barrier(2),uuid.uuid4()
        def decidir(_):
            connections.close_all()
            try:
                barrier.wait(timeout=20)
                return s.decidir_diagnostico(usuario=get_user_model().objects.get(pk=self.planta.pk),empresa=Empresa.objects.get(pk=self.empresa.pk),diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=key).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(decidir,range(2)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(DecisionTecnica.objects.count(),1)

    def test_reserva_y_generacion_concurrentes(self):
        inspeccion_enviada(self.empresa,self.tecnico,self.exp)
        d=diagnostico_enviado(self.empresa,self.tecnico,self.exp)
        s.decidir_diagnostico(usuario=self.planta,empresa=self.empresa,diagnostico_id=d.pk,decision='APROBADO',motivo='',clave=uuid.uuid4())
        barrier=Barrier(2)
        def reservar(_):
            connections.close_all()
            try:
                barrier.wait(timeout=20)
                return s.reservar_informe(usuario=get_user_model().objects.get(pk=self.oficina.pk),empresa=Empresa.objects.get(pk=self.empresa.pk),diagnostico_id=d.pk,clave=uuid.uuid4()).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(reservar,range(2)))
        self.assertEqual(len(set(ids)),1)
        barrier=Barrier(2)
        def generar(_):
            connections.close_all()
            try:
                barrier.wait(timeout=20)
                return s.generar_informe(ids[0])
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            docs=list(pool.map(generar,range(2)))
        self.assertEqual(len(set(docs)),1)
        self.assertEqual(Auditoria.objects.filter(accion='INFORME_INICIAL_GENERADO').count(),1)

    def test_ediciones_simultaneas_version_esperada(self):
        i=s.crear_revision(usuario=self.tecnico,empresa=self.empresa,expediente_id=self.exp.pk,tipo='INSPECCION',clave=uuid.uuid4())
        barrier=Barrier(2)
        def editar(n):
            connections.close_all()
            try:
                barrier.wait(timeout=20)
                try:
                    s.guardar_revision(usuario=get_user_model().objects.get(pk=self.tecnico.pk),empresa=Empresa.objects.get(pk=self.empresa.pk),tipo='INSPECCION',revision_id=i.pk,version=1,clave=uuid.uuid4(),datos={'hallazgos':str(n)})
                    return 'OK'
                except ValidationError:
                    return 'CONFLICTO'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(editar,range(2)))
        self.assertCountEqual(results,['OK','CONFLICTO'])

"""Office separation and inactive-equipment boundary, synthetic QA only."""
import uuid
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_flujo as qa, tecnica_services as t, flujo_services as s
from .models import Equipo, SolicitudRepuestos, Auditoria
from .recepcion_forms import RecepcionForm
from .recepcion_services import abrir_recepcion
from .test_utils import consumir_respuesta


@override_settings(**qa.FLAGS)
class OficinaTests(TestCase):
    setUp=qa.FlujoTests.setUp
    crear=qa.FlujoTests.crear
    datos=qa.FlujoTests.datos
    recepcion=qa.FlujoTests.recepcion
    preparar_solicitud=qa.FlujoTests.preparar_solicitud
    comercial=qa.FlujoTests.comercial
    envio=qa.FlujoTests.envio

    def test_panel_exclusivo_y_breadcrumb_unico(self):
        self.client.force_login(self.oficina)
        self.assertRedirects(self.client.get(reverse('masiscam:dashboard')),reverse('masiscam:oficina_inicio'))
        for section in ('inicio','solicitudes','cotizaciones','compras','ventas','guias','documentos','cuenta'):
            response=self.client.get(reverse('masiscam:oficina_inicio') if section=='inicio' else reverse('masiscam:oficina_seccion',args=[section]))
            self.assertContains(response,'aria-label="Oficina MASISCAM"')
            self.assertContains(response,'aria-label="Migas de pan"',count=1)
            self.assertContains(response,'<ol class="breadcrumb ',count=1)
            for text in ('Inicio de oficina','Clientes','Cotizaciones','Compras','Ventas','Guías de envío'):
                self.assertContains(response,text)
            for text in ('>Planos técnicos</a>','>Reductores</a>','>Proyectos</a>','>Trabajo técnico</a>','>Calibración y mediciones</a>'):
                self.assertNotContains(response,text)
        for role in ('TALLER','TECNICO','PLANTA','GERENCIA','ADMIN'):
            self.client.force_login(self.usuarios[role])
            self.assertEqual(self.client.get(reverse('masiscam:oficina_inicio')).status_code,403)

    def test_indicadores_reales_tenant_y_compras_ventas_autorizadas(self):
        self.preparar_solicitud()
        self.client.force_login(self.oficina)
        panel=self.client.get(reverse('masiscam:oficina_inicio'))
        metrics={name:value for name,value,_ in panel.context['indicadores']}
        self.assertEqual(metrics['Solicitudes pendientes'],1)
        self.assertEqual(metrics['Compras en proceso'],0)
        self.comercial()
        compra,envio=self.envio()
        panel=self.client.get(reverse('masiscam:oficina_inicio'))
        metrics={name:value for name,value,_ in panel.context['indicadores']}
        self.assertEqual(metrics['Solicitudes pendientes'],0)
        self.assertEqual(metrics['Autorizaciones comerciales pendientes'],0)
        self.assertEqual(metrics['Repuestos pendientes de envío'],0)
        self.assertContains(self.client.get(reverse('masiscam:oficina_seccion',args=['ventas'])),self.exp.numero)
        self.assertContains(self.client.get(reverse('masiscam:oficina_seccion',args=['compras'])),compra.proveedor)
        self.assertContains(self.client.get(reverse('masiscam:oficina_seccion',args=['guias'])),envio.numero_guia)
        self.assertContains(self.client.get(reverse('masiscam:oficina_seccion',args=['ventas'])),'no emite facturas')
        from accounts.models import Empresa
        from .test_tecnica import usuario
        otra=Empresa.objects.create(nombre='Empresa ajena QA')
        ajeno=usuario(otra,'oficina-ajena-nueva','OFICINA')
        self.client.force_login(ajeno)
        response=self.client.get(reverse('masiscam:oficina_inicio'))
        self.assertNotContains(response,self.exp.numero)
        self.assertTrue(all(value==0 for _,value,_ in response.context['indicadores']))

    def test_inactivo_excluido_y_servicios_directos_bloqueados_historia_integra(self):
        self.preparar_solicitud()
        guia=self.exp.recepcion.confirmacion.guia
        before=(guia.hash_sha256,guia.documento.hash_sha256,self.equipo.token_publico)
        listado=reverse('masiscam:producto_listado',args=['reductor'])
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(listado,dict(equipo_id=self.equipo.pk,estado='INACTIVO')).status_code,302)
        self.assertTrue(Auditoria.objects.filter(accion='EQUIPO_ESTADO_CAMBIADO').exists())
        for role in ('TALLER','OFICINA'):
            user=self.usuarios[role];self.client.force_login(user)
            for url in (reverse('masiscam:expedientes'),reverse('masiscam:dashboard'),
                    reverse('masiscam:flujo_bandeja') if role=='OFICINA' else reverse('masiscam:taller_estado')):
                self.assertNotContains(self.client.get(url,follow=True),self.exp.numero)
            for name,args in [('flujo_detalle',[self.exp.pk]),('expediente_detalle',[self.exp.pk]),
                    ('recepcion_documento',[self.exp.pk,guia.documento_id])]:
                self.assertIn(self.client.get(reverse('masiscam:'+name,args=args)).status_code,(403,404))
            form=RecepcionForm(empresa=self.empresa,usuario=user)
            self.assertFalse(form.fields['equipo'].queryset.filter(pk=self.equipo.pk).exists())
            self.assertIn(self.client.get(reverse('masiscam:recepcion_crear')+f'?equipo={self.equipo.pk}',HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code,(403,404))
            with self.assertRaises(PermissionDenied):
                abrir_recepcion(usuario=user,empresa=self.empresa,equipo_id=self.equipo.pk,cliente_id=self.cliente.pk,
                    clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),condicion_ingreso='Intento QA')
        with self.assertRaises(PermissionDenied):
            t.crear_revision(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,tipo='INSPECCION',clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            s.cotizar(usuario=self.oficina,empresa=self.empresa,solicitud_id=self.sol.pk,clave=uuid.uuid4(),referencia='Intento QA')
        with self.assertRaises(PermissionDenied):
            t.crear_revision(usuario=self.usuarios['PLANTA'],empresa=self.empresa,expediente_id=self.exp.pk,tipo='INSPECCION',clave=uuid.uuid4(),motivo='Intento QA')
        self.client.force_login(self.root)
        response=self.client.get(reverse('masiscam:recepcion_documento',args=[self.exp.pk,guia.documento_id]))
        self.assertEqual(response.status_code,200)
        self.assertTrue(consumir_respuesta(response).startswith(b'%PDF'))
        self.equipo.refresh_from_db();guia.refresh_from_db()
        self.assertEqual(before,(guia.hash_sha256,guia.documento.hash_sha256,self.equipo.token_publico))
        self.assertEqual(SolicitudRepuestos.objects.filter(pk=self.sol.pk).count(),1)
        from django.contrib.auth import get_user_model
        recuperacion=get_user_model().objects.create_superuser(username='recuperacion-sin-rol-qa',password='Solo-QA-2026!',email='')
        self.client.force_login(recuperacion)
        self.assertEqual(self.client.post(reverse('accounts:seleccionar_empresa'),{'empresa_id':self.empresa.pk}).status_code,302)
        self.assertEqual(self.client.get(reverse('masiscam:ficha_detalle',args=[self.equipo.pk])).status_code,200)
        self.assertFalse(recuperacion.perfiles.exists())
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(listado,dict(equipo_id=self.equipo.pk,estado='ACTIVO')).status_code,302)
        self.client.force_login(self.taller)
        self.assertContains(self.client.get(reverse('masiscam:taller_inicio')),self.exp.numero)
        self.assertEqual(self.client.get(reverse('masiscam:flujo_detalle',args=[self.exp.pk])).status_code,200)

    def test_oficina_y_taller_no_intercambian_operaciones(self):
        self.preparar_solicitud()
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(reverse('masiscam:taller_inicio')).status_code,403)
        self.assertEqual(self.client.get(reverse('masiscam:ficha_crear')).status_code,403)
        self.oficina.is_superuser=True;self.oficina.is_staff=True;self.oficina.save()
        self.client.force_login(self.oficina)
        for name,args in [('usuarios',[]),('proyectos',[]),('biblioteca',[]),
                ('tecnica_expediente',[self.exp.pk]),('calibracion_expediente',[self.exp.pk])]:
            self.assertEqual(self.client.get(reverse('masiscam:'+name,args=args)).status_code,403)
        self.assertIn(self.client.get(reverse('admin:index')).status_code,(302,403))
        with self.assertRaises(PermissionDenied): t.crear_revision(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,tipo='INSPECCION',clave=uuid.uuid4())
        for role in ('TALLER','OFICINA'):
            with self.assertRaises(PermissionDenied):
                t.decidir_diagnostico(usuario=self.usuarios[role],empresa=self.empresa,diagnostico_id=self.diag.pk,
                    decision='APROBADO',motivo='Intento QA',clave=uuid.uuid4())
        self.client.force_login(self.taller)
        for op in ('cotizar','adquirir','aceptacion','enviar'):
            response=self.client.post(reverse('masiscam:flujo_detalle',args=[self.exp.pk]),dict(op=op,clave=uuid.uuid4()))
            self.assertIn(response.status_code,(400,403))

    def test_sweetalert_local_licencia_y_no_confirmaciones_nativas(self):
        root=settings.BASE_DIR
        vendor=root/'static/vendor/sweetalert2/11.26.25'
        self.assertIn('MIT',(vendor/'LICENSE').read_text())
        self.assertGreater((vendor/'sweetalert2.min.js').stat().st_size,40000)
        files=list((root/'masiscam/templates/masiscam').glob('*.html'))+list((root/'masiscam/static/masiscam/js').glob('*.js'))+list((root/'static/app').glob('*.js'))
        for file in files:
            self.assertNotRegex(file.read_text(encoding='utf-8'),r'\b(?:alert|confirm|prompt)\s*\(')
        self.client.force_login(self.oficina)
        response=self.client.get(reverse('masiscam:oficina_inicio'))
        self.assertContains(response,'/static/vendor/sweetalert2/11.26.25/sweetalert2.min.js')
        self.assertNotContains(response,'cdn.jsdelivr')

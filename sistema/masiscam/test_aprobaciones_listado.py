"""Read-only approvals list: scoped pagination, actual states and responsive navigation."""
import os
import uuid
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .test_oficina_gestion import OficinaFixture, FLAGS
from .models import Auditoria
from . import flujo_services as flujo


class ListadoFixture(OficinaFixture):
    def listado(self):
        return reverse('masiscam:tecnica_inicio')

    def adicionales(self, cantidad):
        from .recepcion_services import abrir_recepcion
        for i in range(cantidad):
            equipo=self.crear('LISTADO-'+str(i))
            abrir_recepcion(usuario=self.taller,empresa=self.empresa,equipo_id=equipo.pk,
                cliente_id=self.cliente.pk,clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),
                condicion_ingreso='QA aislado',fotografias=[])


@override_settings(**FLAGS)
class AprobacionesListadoTests(ListadoFixture,TestCase):
    def test_prioridad_estados_busqueda_filtros_y_decision_versionada(self):
        self.preparar(aprobar_tecnico=False)
        prop=self.cotizar(archivo=None)
        self.adicionales(1)
        self.client.force_login(self.admin)
        response=self.client.get(self.listado())
        self.assertEqual(response.status_code,200)
        rows=list(response.context['pagina'])
        self.assertEqual(rows[0].pk,self.exp.pk)
        self.assertEqual(rows[0].piezas_total,1)
        self.assertTrue(rows[0].solicitud_enviada)
        self.assertEqual(rows[0].responsable,self.taller.username)
        self.assertContains(response,'Pendiente de revisión')
        self.assertEqual(self.client.get(self.listado(),{'estado':'revision'}).context['pagina'].paginator.count,1)
        self.assertEqual(self.client.get(self.listado(),{'q':self.exp.numero}).context['pagina'].paginator.count,1)
        flujo.revisar_expediente(usuario=self.admin,empresa=self.empresa,expediente_id=self.exp.pk,
            diagnostico_id=self.diag.pk,propuesta_id=prop.pk,decision='APROBADO',clave=uuid.uuid4())
        response=self.client.get(self.listado(),{'q':self.exp.numero})
        self.assertEqual(list(response.context['pagina'])[0].revision_pendiente,0)
        self.assertContains(response,'Última decisión · cotización v1')
        self.cotizar(archivo=None,referencia='QA VERSION 2')
        response=self.client.get(self.listado(),{'estado':'revision'})
        self.assertEqual(response.context['pagina'].paginator.count,1)
        self.assertContains(response,'Cotización v2')
        self.assertContains(response,'Última decisión · cotización v1')

    def test_paginacion_y_consultas_constantes_permisos(self):
        self.preparar(aprobar_tecnico=False)
        self.cotizar(archivo=None)
        self.adicionales(10)
        self.client.force_login(self.admin)
        # Warm template/content-type caches before comparing 10 rows with one.
        self.client.get(self.listado())
        with CaptureQueriesContext(connection) as first:
            response=self.client.get(self.listado())
        self.assertEqual(len(response.context['pagina']),10)
        self.assertEqual(response.context['pagina'].paginator.count,11)
        self.assertEqual(list(response.context['pagina'])[0].pk,self.exp.pk)
        with CaptureQueriesContext(connection) as second:
            response2=self.client.get(self.listado(),{'page':2})
        self.assertEqual(len(response2.context['pagina']),1)
        self.assertEqual(len(first),len(second))
        self.assertFalse(set(e.pk for e in response.context['pagina']) & set(e.pk for e in response2.context['pagina']))
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.listado()).context['pagina'].paginator.count,0)
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(self.listado()).status_code,403)
        from accounts.models import Empresa
        from .test_tecnica import usuario
        otra=Empresa.objects.create(nombre='Empresa ajena QA')
        self.client.force_login(usuario(otra,'admin-listado-ajeno','ADMIN'))
        self.assertEqual(self.client.get(self.listado()).context['pagina'].paginator.count,0)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador QA opcional')
@override_settings(**FLAGS)
class AprobacionesListadoBrowserTests(ListadoFixture,StaticLiveServerTestCase):
    def test_escritorio_tablet_movil_filtros_paginacion_y_revisar(self):
        from playwright.sync_api import sync_playwright,expect
        self.preparar(aprobar_tecnico=False)
        self.cotizar(archivo=None)
        self.adicionales(10)
        self.client.force_login(self.admin)
        cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        url=self.live_server_url+self.listado()
        numero=self.exp.numero
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page()
                for width in (1440,768,390):
                    page.set_viewport_size(dict(width=width,height=1000))
                    page.goto(url)
                    expect(page.locator('[data-expediente]')).to_have_count(10)
                    expect(page.locator('form.mc-toolbar')).to_have_count(1)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
                    expect(page.locator('[data-expediente]').first.get_by_role('link',name='Revisar '+numero,exact=True)).to_be_visible()
                    if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                        page.screenshot(path=os.path.join(os.environ['MASISCAM_UI_EVIDENCE_DIR'],f'aprobaciones-{width}.png'),full_page=True)
                    page.get_by_role('link',name='Siguiente',exact=True).click()
                    expect(page.locator('[data-expediente]')).to_have_count(1)
                    page.locator('[name=estado]').select_option('revision')
                    page.get_by_role('button',name='Aplicar',exact=True).click()
                    expect(page.locator('[data-expediente]')).to_have_count(1)
                    expect(page.locator('[data-expediente]').first).to_contain_text(numero)
                    page.locator('[name=q]').fill('NO EXISTE QA')
                    page.get_by_role('button',name='Aplicar',exact=True).click()
                    expect(page.get_by_text('Sin expedientes para estos filtros.',exact=True)).to_be_visible()
                page.goto(url)
                page.get_by_role('link',name='Revisar '+numero,exact=True).click()
                expect(page).to_have_url(self.live_server_url+reverse('masiscam:flujo_detalle',args=[self.exp.pk]))
            finally:
                browser.close()

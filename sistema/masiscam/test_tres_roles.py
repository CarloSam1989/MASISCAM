"""Regresión de roles vigentes reutilizando los recorridos existentes en QA local."""
import os
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import Perfil
from . import test_flujo as flujo, test_plano_trabajo as plano, test_tecnica as tecnica
from .access import rol_masiscam_usuario
from .models import RolMasiscam
from .test_recepcion import fixtures
from .usuario_forms import UsuarioEditarForm


class TresRolesFixture:
    datos = flujo.FlujoTests.datos
    crear = flujo.FlujoTests.crear
    recepcion = flujo.FlujoTests.recepcion
    preparar_solicitud = flujo.FlujoTests.preparar_solicitud
    comercial = flujo.FlujoTests.comercial
    envio = flujo.FlujoTests.envio
    recibir = flujo.FlujoTests.recibir
    cargar = plano.TrabajoFixture.cargar
    componente = plano.TrabajoFixture.componente
    seleccionar = plano.TrabajoFixture.seleccionar

    def setUp(self):
        self.empresa, self.oficina, _, self.cliente, self.historico = fixtures()
        self.admin = tecnica.usuario(self.empresa, 'admin-qa', 'ADMIN')
        self.admin_cierre = tecnica.usuario(self.empresa, 'admin-cierre-qa', 'ADMIN')
        self.taller = tecnica.usuario(self.empresa, 'taller-qa', 'TALLER')
        self.otro = tecnica.usuario(self.empresa, 'otro-taller-qa', 'TALLER')
        self.root = self.admin
        # Alias solo para reutilizar fixtures anteriores; todas las filas tienen roles vigentes.
        self.usuarios = dict(ADMIN=self.admin, TALLER=self.taller, OFICINA=self.oficina,
                             PLANTA=self.admin, GERENCIA=self.admin_cierre)
        self.client.force_login(self.taller)


@override_settings(**flujo.FLAGS)
class TresRolesTests(TresRolesFixture, TestCase):
    test_plano_provisional_envio_y_fotos = plano.PlanoTrabajoTests.test_carga_componentes_seleccion_envio_y_aprobaciones_posteriores
    test_posiciones_versiones_y_publicacion = plano.PlanoTrabajoTests.test_marcado_parcial_versionado_publicacion_y_permisos
    test_plano_permisos = plano.PlanoTrabajoTests.test_permisos_pdf_invalido_fuente_y_componentes_protegidos

    def test_recorrido_completo_con_aprobadores_administradores(self):
        crear_usuario = tecnica.usuario
        def usuario_qa(empresa, nombre, rol):
            return crear_usuario(empresa, nombre, 'ADMIN' if rol == 'PLANTA' else rol)
        with patch.object(tecnica, 'usuario', usuario_qa):
            flujo.FlujoTests.test_recorrido_completo_plano_oficina_reparacion_calibracion_pdf(self)

    def test_asignacion_explicita_y_permisos_tres_roles(self):
        form = UsuarioEditarForm(empresa=self.empresa, instance=self.taller,
                                 asignacion=RolMasiscam.objects.get(perfil__user=self.taller))
        self.assertEqual({k for k, _ in form.fields['rol'].choices if k}, {'ADMIN', 'TALLER', 'OFICINA'})
        from .admin import RolMasiscamAdminForm, MasiscamUserCreationForm
        self.assertEqual({k for k, _ in RolMasiscamAdminForm().fields['rol'].choices if k}, {'ADMIN', 'TALLER', 'OFICINA'})
        self.assertEqual({k for k, _ in MasiscamUserCreationForm().fields['tipo'].choices}, {'ADMIN', 'TALLER', 'OFICINA'})
        for rol, user in [('ADMIN', self.admin), ('TALLER', self.taller), ('OFICINA', self.oficina)]:
            self.client.force_login(user)
            for name in ('usuarios', 'biblioteca_referencia_crear', 'calibracion_plantillas'):
                response = self.client.get(reverse('masiscam:' + name))
                self.assertEqual(response.status_code, 200 if rol == 'ADMIN' else 403, (rol, name))
        user = get_user_model().objects.create_superuser(username='sin-asignacion', email='', password='QA-123456!')
        Perfil.objects.create(user=user, empresa=self.empresa)
        self.client.force_login(user)
        session = self.client.session
        session['empresa_activa_id'] = self.empresa.pk
        session.save()
        self.assertEqual(self.client.get(reverse('masiscam:dashboard')).status_code, 403)
        self.assertIsNone(rol_masiscam_usuario(usuario=self.admin, empresa=None))
        with transaction.atomic(), self.assertRaises(IntegrityError):
            RolMasiscam.objects.filter(perfil__user=self.taller).update(rol='PLANTA')

    def test_separacion_comercial_tecnica_y_reparacion_bloqueada(self):
        import uuid
        from . import flujo_services, operacion_services
        self.preparar_solicitud()
        with self.assertRaises(PermissionDenied):
            flujo_services.cotizar(usuario=self.admin, empresa=self.empresa,
                solicitud_id=self.sol.pk, clave=uuid.uuid4(), referencia='No autorizado')
        with self.assertRaises(ValidationError):
            operacion_services.iniciar_actividad(usuario=self.taller, empresa=self.empresa,
                expediente_id=self.exp.pk, clave=uuid.uuid4(), datos={})
        with self.assertRaises(PermissionDenied):
            tecnica.s.decidir_diagnostico(usuario=self.oficina, empresa=self.empresa,
                diagnostico_id=self.diag.pk, decision='APROBADO', motivo='No autorizado', clave=uuid.uuid4())

    def test_empresa_ajena_y_autor_no_puede_aprobar_tras_cambio_de_rol(self):
        import uuid
        from accounts.models import Empresa
        self.preparar_solicitud()
        ajena = Empresa.objects.create(nombre='Empresa ajena QA')
        for user in (self.admin, self.oficina, self.taller):
            with self.assertRaises(PermissionDenied):
                tecnica.s.expediente_autorizado(user, ajena, self.exp.pk)
        role = RolMasiscam.objects.get(perfil__user=self.taller)
        role.rol = 'ADMIN'; role.save()
        with self.assertRaises(PermissionDenied):
            tecnica.s.decidir_diagnostico(usuario=self.taller, empresa=self.empresa,
                diagnostico_id=self.diag.pk, decision='APROBADO', motivo='Autoaprobación', clave=uuid.uuid4())


@override_settings(**flujo.FLAGS)
class TresRolesMigracionTests(TransactionTestCase):
    def test_0040_conserva_asignaciones_historicas_sin_conversion(self):
        old = [('masiscam', '0039_posiciones_provisionales')]
        new = [('masiscam', '0040_tres_roles_operativos')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            empresa = apps.get_model('accounts', 'Empresa').objects.create(nombre='Histórica QA')
            user = apps.get_model('auth', 'User').objects.create(username='historico-qa')
            perfil = apps.get_model('accounts', 'Perfil').objects.create(user=user, empresa=empresa)
            Role = apps.get_model('masiscam', 'RolMasiscam')
            role = Role.objects.create(perfil=perfil, rol='PLANTA', activo=True)
            before = Role.objects.get(pk=role.pk).__dict__.copy()
        finally:
            MigrationExecutor(connection).migrate(new)
        after = RolMasiscam.objects.get(pk=role.pk)
        self.assertEqual(after.rol, 'PLANTA')
        self.assertEqual(after.perfil_id, before['perfil_id'])
        self.assertTrue(after.activo)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertIsNone(rol_masiscam_usuario(usuario=user, empresa=after.perfil.empresa))
        client = Client(); client.force_login(user)
        self.assertEqual(client.get(reverse('masiscam:dashboard')).status_code, 403)
        admin = tecnica.usuario(after.perfil.empresa, 'admin-asignador-qa', 'ADMIN')
        client.force_login(admin)
        response = client.post(reverse('masiscam:usuario_editar', args=[after.pk]),
            dict(username=user.username, first_name='Histórico QA', email='', rol='TALLER'))
        self.assertEqual(response.status_code, 302, response.content)
        from .models import Auditoria
        event = Auditoria.objects.get(accion='USUARIO_EDITADO', usuario=admin)
        self.assertEqual(event.detalle['rol_anterior'], 'PLANTA')
        self.assertEqual(event.detalle['rol_nuevo'], 'TALLER')
        self.assertEqual(rol_masiscam_usuario(usuario=user, empresa=after.perfil.empresa).rol, 'TALLER')


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS') == '1', 'Navegador QA explícito')
@override_settings(**flujo.FLAGS)
class TresRolesBrowserTests(TresRolesFixture, StaticLiveServerTestCase):
    test_plano_zoom_marcado_persistencia = plano.PlanoTrabajoBrowserTests.test_marcar_con_zoom_desplazar_pulsar_y_recargar
    test_carga_pdf_componentes_envio = plano.PlanoTrabajoBrowserTests.test_taller_carga_pdf_registra_selecciona_y_envia_sin_formulario_tecnico

    def test_navegacion_responsive_tres_roles(self):
        import re
        from playwright.sync_api import sync_playwright, expect
        from pathlib import Path
        roles = [('TALLER', self.taller, 'taller_inicio'), ('ADMIN', self.admin, 'dashboard'),
                 ('OFICINA', self.oficina, 'oficina_inicio')]
        cookies = {}
        for rol, user, _ in roles:
            client = Client(); client.force_login(user)
            cookies[rol] = client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for rol, user, route in roles:
                    context = browser.new_context()
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,
                        value=cookies[rol], url=self.live_server_url)])
                    page = context.new_page()
                    for width in (1440, 768, 390):
                        page.set_viewport_size(dict(width=width, height=1000))
                        response = page.goto(self.live_server_url + reverse('masiscam:' + route))
                        self.assertEqual(response.status, 200, rol)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (rol, width))
                        self.assertEqual(page.evaluate('typeof Swal'), 'function')
                        expect(page.locator('nav[aria-label="Migas de pan"]')).to_have_count(1)
                        if width < 992:
                            page.locator('.app-menu-toggle').click()
                            expect(page.locator('#appMenu')).to_have_class(re.compile(r'\bshow\b'))
                            expect(page.locator('#appMenu')).to_be_visible()
                            expect(page.locator('#appMenu button')).to_be_visible()
                        if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                            page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR']) / f'{rol}-{width}.png'), full_page=True)
                        if rol == 'TALLER':
                            self.assertEqual(page.locator('#appMenu a').all_text_contents(),
                                ['Inicio', 'Equipos', 'Mis trabajos', 'Planos y piezas', 'Mi cuenta'])
                            expect(page.locator('#appMenu button')).to_have_text('Salir')
                        for link in page.locator('#appMenu a').all():
                            href = link.get_attribute('href')
                            check = context.request.get(self.live_server_url + href.split('#')[0])
                            self.assertEqual(check.status, 200, (rol, href))
                    context.close()
            finally:
                browser.close()


@override_settings(**flujo.FLAGS)
class Transicion0040Tests(TresRolesFixture, TransactionTestCase):
    def test_conserva_cuentas_accesos_y_reasignacion_auditada(self):
        from .models import (Auditoria, Documento, Equipo, ExpedienteMantenimiento,
                             AsignacionTecnica)
        from .access import PERMISOS_ROL
        equipo = self.crear('TRANSICION-0040')
        exp = self.recepcion(equipo)
        old = [('masiscam', '0039_posiciones_provisionales')]
        new = [('masiscam', '0040_tres_roles_operativos')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            Role = apps.get_model('masiscam', 'RolMasiscam')
            historicos = {}
            for code in ('PLANTA', 'GERENCIA', 'TECNICO', 'CONSULTA', 'CLIENTE'):
                user = get_user_model().objects.create_user(username='transicion-qa-' + code)
                perfil = Perfil.objects.create(user=user, empresa=self.empresa)
                historicos[code] = Role.objects.create(perfil_id=perfil.pk, rol=code,
                    cliente_id=self.cliente.pk if code == 'CLIENTE' else None)
            models = (get_user_model(), Perfil, RolMasiscam, Equipo,
                      ExpedienteMantenimiento, AsignacionTecnica, Documento, Auditoria)
            before = [list(model.objects.order_by('pk').values()) for model in models]
        finally:
            MigrationExecutor(connection).migrate(new)
        self.assertEqual(before, [list(model.objects.order_by('pk').values()) for model in models])
        for code, row in historicos.items():
            user = get_user_model().objects.get(perfiles__rol_masiscam__pk=row.pk)
            self.assertIsNone(rol_masiscam_usuario(usuario=user, empresa=self.empresa), code)
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('masiscam:dashboard')).status_code, 403, code)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('masiscam:usuarios'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['consulta_pendientes'], 5)
        self.assertTrue(all(row.gestionable for row in response.context['roles']))
        self.assertEqual(rol_masiscam_usuario(usuario=self.admin, empresa=self.empresa).rol, 'ADMIN')
        self.assertIn('usuarios', PERMISOS_ROL['ADMIN'])
        for user in (self.taller, self.oficina):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('masiscam:flujo_detalle', args=[exp.pk])).status_code, 200)
            self.assertEqual(self.client.get(reverse('masiscam:usuarios')).status_code, 403)
        # No guardar sin una selección expresa, aunque la cuenta siga activa.
        self.client.force_login(self.admin)
        row = historicos['PLANTA']
        user = get_user_model().objects.get(perfiles__rol_masiscam__pk=row.pk)
        url = reverse('masiscam:usuario_editar', args=[row.pk])
        data = dict(username=user.username, first_name='Cuenta histórica QA', email='')
        response = self.client.post(url, dict(data, rol=''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('rol', response.context['form'].errors)
        self.assertEqual(RolMasiscam.objects.get(pk=row.pk).rol, 'PLANTA')
        self.assertEqual(Auditoria.objects.count(), len(before[-1]))
        self.assertEqual(self.client.post(url, dict(data, rol='ADMIN')).status_code, 302)
        event = Auditoria.objects.get(accion='USUARIO_EDITADO', objeto_id=str(row.pk))
        self.assertEqual(event.usuario_id, self.admin.pk)
        self.assertEqual(event.empresa_id, self.empresa.pk)
        self.assertEqual(event.detalle, dict(rol_anterior='PLANTA', rol_nuevo='ADMIN', cliente_anterior=None))
        self.assertEqual(RolMasiscam.objects.get(pk=row.pk).perfil_id, row.perfil_id)
        # El cliente se conserva; solo se retira su antigua restricción de acceso,
        # dejando el vínculo previo en la auditoría de la asignación autorizada.
        row = historicos['CLIENTE']
        user = get_user_model().objects.get(perfiles__rol_masiscam__pk=row.pk)
        response = self.client.post(reverse('masiscam:usuario_editar', args=[row.pk]),
            dict(username=user.username, first_name='Cuenta oficina QA', email='', rol='OFICINA'))
        self.assertEqual(response.status_code, 302, response.content)
        event = Auditoria.objects.get(accion='USUARIO_EDITADO', objeto_id=str(row.pk))
        self.assertEqual(event.detalle['cliente_anterior'], self.cliente.pk)
        self.cliente.refresh_from_db()
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('masiscam:oficina_inicio')).status_code, 200)
        self.assertEqual(self.client.get(reverse('masiscam:flujo_detalle', args=[exp.pk])).status_code, 200)

    def test_cuentas_protegidas_y_limite_de_trabajos_historicos(self):
        import uuid
        from accounts.models import Empresa
        from . import recepcion_services
        # Un ADMIN normal conserva Usuarios, pero no puede degradarse ni tomar
        # cuentas privilegiadas o pertenecientes también a otra empresa.
        self.client.force_login(self.admin)
        role = RolMasiscam.objects.get(perfil__user=self.admin)
        response = self.client.post(reverse('masiscam:usuario_editar', args=[role.pk]),
            dict(username=self.admin.username, first_name='Admin QA', email='', rol='TALLER'))
        self.assertEqual(response.status_code, 200)
        role.refresh_from_db(); self.assertEqual(role.rol, 'ADMIN')
        ajena = Empresa.objects.create(nombre='Empresa protegida QA')
        Perfil.objects.create(user=self.otro, empresa=ajena)
        role = RolMasiscam.objects.get(perfil__user=self.otro)
        self.assertEqual(self.client.get(reverse('masiscam:usuario_editar', args=[role.pk])).status_code, 403)
        user = get_user_model().objects.create_superuser(username='protegido-qa', email='', password=None)
        perfil = Perfil.objects.create(user=user, empresa=self.empresa)
        role = RolMasiscam.objects.create(perfil=perfil, rol='ADMIN')
        self.assertEqual(self.client.get(reverse('masiscam:usuario_editar', args=[role.pk])).status_code, 403)
        # Un expediente histórico sin equipo registrado por este TALLER no gana
        # acceso por cambiar solamente el código de rol; requiere revisión previa.
        exp = recepcion_services.abrir_recepcion(usuario=self.oficina, empresa=self.empresa,
            equipo_id=self.historico.pk, cliente_id=self.cliente.pk, clave=uuid.uuid4(),
            tipo='MANTENIMIENTO', fecha=tecnica.timezone.now(), condicion_ingreso='Histórico QA', fotografias=[])
        self.client.force_login(self.taller)
        self.assertEqual(self.client.get(reverse('masiscam:flujo_detalle', args=[exp.pk])).status_code, 403)
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(reverse('masiscam:flujo_detalle', args=[exp.pk])).status_code, 200)

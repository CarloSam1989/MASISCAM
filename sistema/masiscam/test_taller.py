import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Empresa, Perfil
from .access import autorizar_creacion_equipo
from .forms import EquipoForm, FichaEquipoForm
from .models import Equipo, ExpedienteMantenimiento, RolMasiscam
from .recepcion_confirmacion import confirmar_recepcion
from .recepcion_services import abrir_recepcion, reservar_guia
from .test_recepcion import fixtures, foto
from .test_utils import consumir_respuesta


@override_settings(GOOGLE_DRIVE_ENABLED=False, MASISCAM_RECEPCION_LOCAL=True,
    MASISCAM_TECNICA_LOCAL=True, MASISCAM_OPERACION_LOCAL=True,
    MASISCAM_CALIBRACION_LOCAL=True, MASISCAM_CIERRE_LOCAL=True)
class TallerTests(TestCase):
    def setUp(self):
        self.empresa, self.oficina, _, self.cliente, self.historico = fixtures()
        self.usuarios = {'OFICINA': self.oficina}
        for codigo in ('ADMIN', 'TECNICO', 'PLANTA', 'GERENCIA', 'CONSULTA', 'TALLER', 'CLIENTE'):
            user = get_user_model().objects.create_user(username='taller-test-' + codigo, password='Taller-QA-2026!')
            perfil = Perfil.objects.create(user=user, empresa=self.empresa)
            RolMasiscam.objects.create(perfil=perfil, rol=codigo,
                cliente=self.cliente if codigo == 'CLIENTE' else None)
            self.usuarios[codigo] = user
        self.taller = self.usuarios['TALLER']
        self.otro = get_user_model().objects.create_user(username='otro-taller')
        perfil = Perfil.objects.create(user=self.otro, empresa=self.empresa)
        RolMasiscam.objects.create(perfil=perfil, rol='TALLER')
        self.root = get_user_model().objects.create_superuser(username='taller-root', password='Root-QA-2026!', email='')
        perfil = Perfil.objects.create(user=self.root, empresa=self.empresa)
        RolMasiscam.objects.create(perfil=perfil, rol='ADMIN')
        self.client.force_login(self.taller)

    def datos(self, sufijo='1'):
        return dict(cliente=self.cliente.pk, camaronera='Ingreso QA', estacion='E1', sector='S1',
            numero_equipo='TALLER-' + sufijo, marca='Marca QA', modelo='Modelo QA',
            numero_serie='TALLER-SER-' + sufijo, potencia='20 HP',
            consulta_publica_activa='on', registrado_por=self.root.pk, empresa='99999')

    def crear(self, sufijo='1', usuario=None, tipo='REDUCTOR'):
        self.client.force_login(usuario or self.taller)
        response = self.client.post(reverse('masiscam:ficha_crear') + '?tipo=' + tipo,
            self.datos(sufijo), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        return Equipo.objects.get(pk=response.json()['id'])

    def recepcion(self, equipo):
        return abrir_recepcion(usuario=self.taller, empresa=self.empresa, equipo_id=equipo.pk,
            cliente_id=self.cliente.pk, clave=uuid.uuid4(), tipo='MANTENIMIENTO', fecha=timezone.now(),
            condicion_ingreso='Condición sintética QA', fotografias=[foto()])

    def test_creacion_catalogo_autoria_y_fotos_sin_publicacion_ni_cambio_historico(self):
        antes = Equipo.objects.filter(pk=self.historico.pk).values().get()
        for codigo in Equipo.TipoProducto.values:
            data = dict(self.datos(codigo), fotografia_equipo=foto())
            response = self.client.post(reverse('masiscam:ficha_crear') + '?tipo=' + codigo, data)
            self.assertEqual(response.status_code, 302)
            equipo = Equipo.objects.get(numero_serie=data['numero_serie'])
            self.assertEqual(equipo.registrado_por, self.taller)
            self.assertEqual(equipo.tipo_producto, codigo)
            self.assertFalse(equipo.consulta_publica_activa or equipo.visible_publico)
            self.assertTrue(equipo.fotografia_general)
            self.assertEqual(response.url, reverse('masiscam:recepcion_crear') + '?equipo=' + str(equipo.pk))
        self.assertEqual(Equipo.objects.filter(pk=self.historico.pk).values().get(), antes)
        self.assertIsNone(self.historico.registrado_por_id)

    def test_exclusividad_urls_ajax_y_superusuario_sin_equivalencia(self):
        total = Equipo.objects.count()
        for codigo, user in {**self.usuarios, 'ROOT': self.root}.items():
            if codigo == 'TALLER':
                continue
            self.client.force_login(user)
            for url in (reverse('masiscam:ficha_crear'),
                        reverse('masiscam:equipo_crear', args=[self.historico.proyecto_id])):
                with self.subTest(rol=codigo, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url, self.datos(), HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 403)
                    self.assertEqual(self.client.post(url, self.datos(), HTTP_ACCEPT='application/json').status_code, 403)
        self.assertEqual(Equipo.objects.count(), total)
        self.client.force_login(self.taller)
        legacy = reverse('masiscam:equipo_crear', args=[self.historico.proyecto_id])
        self.assertRedirects(self.client.get(legacy), reverse('masiscam:ficha_crear'))
        self.assertEqual(self.client.post(legacy, self.datos()).status_code, 403)

    def test_servicio_y_formularios_no_aceptan_actor_no_autorizado(self):
        from . import tecnica_services, operacion_services, calibracion_services, cierre_services
        for autorizar in (tecnica_services.autorizar_tecnica, operacion_services.autorizar,
                          calibracion_services.autorizar, cierre_services.autorizar):
            with self.assertRaises(PermissionDenied):
                autorizar(self.taller, self.empresa)
        for user in (self.root, self.oficina, self.usuarios['TECNICO']):
            with self.assertRaises(PermissionDenied):
                autorizar_creacion_equipo(usuario=user, empresa=self.empresa)
            form = FichaEquipoForm(self.datos(), empresa=self.empresa)
            self.assertTrue(form.is_valid(), form.errors)
            with self.assertRaises(PermissionDenied):
                form.save(usuario=user)
            form = EquipoForm(empresa=self.empresa, usuario=user,
                instance=Equipo(proyecto=self.historico.proyecto))
            with self.assertRaises(PermissionDenied):
                form.save(commit=False)
        form = EquipoForm(empresa=self.empresa, usuario=self.taller, instance=self.historico)
        with self.assertRaises(PermissionDenied):
            form.save(commit=False)

    def test_menu_login_cuenta_y_busqueda_ruc_solo_empresa(self):
        response = self.client.get(reverse('masiscam:taller_inicio'))
        for vista in ('taller_inicio', 'ficha_crear', 'expedientes', 'taller_estado', 'taller_cuenta'):
            self.assertContains(response, 'href="' + reverse('masiscam:' + vista) + '"')
        for vista in ('usuarios', 'clientes', 'proyectos', 'biblioteca', 'tecnica_inicio', 'operacion_inicio', 'calibracion_inicio', 'cierre_dashboard'):
            self.assertNotContains(response, 'href="' + reverse('masiscam:' + vista) + '"')
            self.assertEqual(self.client.get(reverse('masiscam:' + vista)).status_code, 403)
        from .models import Cliente
        otra = Empresa.objects.create(nombre='Ajena QA')
        externo = Cliente.objects.create(empresa=otra, nombre_comercial='No visible', ruc=self.cliente.ruc)
        response = self.client.get(reverse('masiscam:clientes'), {'formato': 'json', 'ruc': self.cliente.ruc})
        self.assertEqual([c['id'] for c in response.json()['clientes']], [self.cliente.pk])
        self.assertNotIn(externo.pk, [c['id'] for c in response.json()['clientes']])
        total = Equipo.objects.count()
        response = self.client.post(reverse('masiscam:ficha_crear'),
            dict(self.datos(), cliente=externo.pk), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Equipo.objects.count(), total)
        for vista, args in [('cliente_crear', []), ('cliente_editar', [self.cliente.pk]),
                            ('cliente_detalle', [self.cliente.pk]), ('usuario_crear', [])]:
            url = reverse('masiscam:' + vista, args=args)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertIn(self.client.post(url, {}).status_code, (403, 405))
        self.assertContains(self.client.get(reverse('masiscam:taller_cuenta')), self.taller.username)
        self.client.logout()
        response = self.client.post(reverse('accounts:login'), {
            'username': self.taller.username, 'password': 'Taller-QA-2026!', 'next': reverse('masiscam:usuarios')})
        self.assertRedirects(response, reverse('masiscam:taller_inicio'))

    def test_recepcion_confirmacion_pdf_estado_y_controles_de_objeto(self):
        equipo = self.crear()
        otro_equipo = self.crear('2', usuario=self.otro)
        self.client.force_login(self.taller)
        exp = self.recepcion(equipo)
        with self.assertRaises(PermissionDenied):
            self.recepcion(otro_equipo)
        confirmado = confirmar_recepcion(usuario=self.taller, empresa=self.empresa,
            expediente_id=exp.pk, clave=uuid.uuid4(), placa='SIN_PLACA',
            motivo_sin_placa='Sin placa QA', motivo_sin_guia='Sin remisión QA',
            general_id=exp.recepcion.evidencias.get().pk)
        documento = confirmado.guia.documento
        url = reverse('masiscam:recepcion_documento', args=[exp.pk, documento.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(consumir_respuesta(response).startswith(b'%PDF-'))
        self.assertContains(self.client.get(reverse('masiscam:expedientes')), exp.numero)
        self.assertContains(self.client.get(reverse('masiscam:taller_estado')), exp.numero)
        self.client.force_login(self.otro)
        for vista, args in [('expediente_detalle', [exp.pk]), ('recepcion_documento', [exp.pk, documento.pk])]:
            self.assertEqual(self.client.get(reverse('masiscam:' + vista, args=args)).status_code, 404)
        self.assertNotContains(self.client.get(reverse('masiscam:expedientes')), exp.numero)
        with self.assertRaises(PermissionDenied):
            reservar_guia(usuario=self.otro, empresa=self.empresa, expediente_id=exp.pk, clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            confirmar_recepcion(usuario=self.otro, empresa=self.empresa, expediente_id=exp.pk,
                clave=uuid.uuid4(), placa='SIN_PLACA')

    def test_formulario_recepcion_y_fotos_no_exponen_equipos_ajenos(self):
        equipo = self.crear()
        otro_equipo = self.crear('2', usuario=self.otro)
        self.client.force_login(self.taller)
        response = self.client.get(reverse('masiscam:recepcion_crear'), {'equipo': equipo.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['form'].fields['equipo'].queryset), [equipo])
        self.assertEqual(self.client.get(reverse('masiscam:recepcion_crear'), {'equipo': otro_equipo.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse('masiscam:equipo_foto_privada', args=[otro_equipo.pk, 'general'])).status_code, 404)
        for vista, args in [('ficha_editar', [equipo.pk]), ('ficha_detalle', [equipo.pk]),
                            ('registro_crear', [equipo.pk]), ('equipo_informe', [equipo.pk])]:
            url = reverse('masiscam:' + vista, args=args)
            self.assertIn(self.client.get(url).status_code, (403, 405))
            self.assertIn(self.client.post(url, {}).status_code, (403, 405))

    def test_ingreso_taller_continua_con_tecnico_asignado_y_planta_independiente(self):
        from . import tecnica_services as tecnica
        from .test_tecnica import diagnostico_enviado, inspeccion_enviada
        equipo = self.crear()
        exp = self.recepcion(equipo)
        confirmar_recepcion(usuario=self.taller, empresa=self.empresa, expediente_id=exp.pk,
            clave=uuid.uuid4(), placa='SIN_PLACA', motivo_sin_placa='Ausencia QA',
            motivo_sin_guia='Ausencia QA', general_id=exp.recepcion.evidencias.get().pk)
        tecnico, planta = self.usuarios['TECNICO'], self.usuarios['PLANTA']
        tecnica.asignar_tecnico(usuario=planta, empresa=self.empresa, expediente_id=exp.pk,
            tecnico_id=tecnico.pk, motivo='Asignación expresa QA', clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            tecnica.crear_revision(usuario=self.taller, empresa=self.empresa,
                expediente_id=exp.pk, tipo='INSPECCION', clave=uuid.uuid4())
        inspeccion_enviada(self.empresa, tecnico, exp)
        diagnostico = diagnostico_enviado(self.empresa, tecnico, exp)
        with self.assertRaises(PermissionDenied):
            tecnica.decidir_diagnostico(usuario=self.taller, empresa=self.empresa,
                diagnostico_id=diagnostico.pk, decision='APROBADO', motivo='Intento QA', clave=uuid.uuid4())
        tecnica.decidir_diagnostico(usuario=planta, empresa=self.empresa,
            diagnostico_id=diagnostico.pk, decision='APROBADO', motivo='', clave=uuid.uuid4())
        diagnostico.refresh_from_db()
        self.assertEqual(diagnostico.estado, 'APROBADO')
        response = self.client.get(reverse('masiscam:taller_estado'))
        self.assertContains(response, exp.numero)
        self.assertNotContains(response, diagnostico.fallas)

    def test_revocacion_roles_csrf_y_admin_tecnico(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.taller)
        self.assertEqual(csrf.post(reverse('masiscam:ficha_crear'), self.datos()).status_code, 403)
        rol = self.taller.perfiles.get().rol_masiscam
        rol.activo = False
        rol.save(update_fields=['activo'])
        self.assertIn(self.client.get(reverse('masiscam:taller_inicio')).status_code, (302, 403))
        rol.activo = True
        rol.save(update_fields=['activo'])
        self.taller.is_staff = self.taller.is_superuser = True
        self.taller.save(update_fields=['is_staff', 'is_superuser'])
        self.client.force_login(self.taller)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 302)
        self.assertEqual(self.client.get(reverse('masiscam:usuarios')).status_code, 403)

    def test_roles_historicos_sin_conversion_y_alta_explicita(self):
        self.assertEqual(self.oficina.perfiles.get().rol_masiscam.rol, 'OFICINA')
        self.assertEqual(self.usuarios['TECNICO'].perfiles.get().rol_masiscam.rol, 'TECNICO')
        self.assertFalse(Equipo.objects.filter(registrado_por__isnull=False).exists())
        self.client.force_login(self.usuarios['ADMIN'])
        response = self.client.post(reverse('masiscam:usuario_crear'), {
            'username': 'nuevo-taller', 'first_name': 'Taller nuevo', 'rol': 'TALLER',
            'password1': 'Taller-Seguro-2026!', 'password2': 'Taller-Seguro-2026!', 'cliente': 'invalido'})
        self.assertRedirects(response, reverse('masiscam:usuarios'))
        creado = get_user_model().objects.get(username='nuevo-taller')
        self.assertEqual(creado.perfiles.get().rol_masiscam.rol, 'TALLER')
        self.assertFalse(creado.is_staff or creado.is_superuser)

    def test_admin_recuperacion_conservada_sin_alta_para_superusuario_operativo(self):
        self.client.force_login(self.root)
        url = reverse('admin:masiscam_equipo_add')
        self.assertEqual(self.client.get(url).status_code, 200)
        rol = self.root.perfiles.get().rol_masiscam
        for codigo in ('TECNICO', 'OFICINA', 'PLANTA', 'GERENCIA', 'CONSULTA'):
            rol.rol = codigo
            rol.save(update_fields=['rol'])
            with self.subTest(rol=codigo):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {}).status_code, 403)


class TallerMigrationTests(TransactionTestCase):
    def test_upgrade_conserva_equipos_y_roles_sin_asignar_taller(self):
        from datetime import date
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        anterior = [('masiscam', '0029_integridad_resultados_reparacion')]
        actual = [('masiscam', '0030_rol_taller_ingreso')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(anterior)
            apps = executor.loader.project_state(anterior).apps
            empresa = apps.get_model('accounts', 'Empresa').objects.create(nombre='Histórica QA')
            user = apps.get_model('auth', 'User').objects.create(username='historico-taller-qa')
            perfil = apps.get_model('accounts', 'Perfil').objects.create(user=user, empresa=empresa)
            roles = apps.get_model('masiscam', 'RolMasiscam')
            rol = roles.objects.create(perfil=perfil, rol='TECNICO')
            proyecto = apps.get_model('masiscam', 'Proyecto').objects.create(empresa=empresa,
                codigo='HIST-TALLER', nombre='Histórico QA', responsable='QA',
                fecha_inicio=date(2020, 1, 1), creado_por=user)
            equipos = apps.get_model('masiscam', 'Equipo')
            equipo = equipos.objects.create(proyecto=proyecto, nombre='HIST-1', numero_serie='HIST-SERIE')
            antes = equipos.objects.values().get(pk=equipo.pk)
            rol_antes = roles.objects.values().get(pk=rol.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(actual)
            apps = executor.loader.project_state(actual).apps
            equipos = apps.get_model('masiscam', 'Equipo')
            self.assertEqual(equipos.objects.values(*antes.keys()).get(pk=equipo.pk), antes)
            self.assertIsNone(equipos.objects.get(pk=equipo.pk).registrado_por_id)
            roles = apps.get_model('masiscam', 'RolMasiscam')
            self.assertEqual(roles.objects.values().get(pk=rol.pk), rol_antes)
            self.assertFalse(roles.objects.filter(rol='TALLER').exists())
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

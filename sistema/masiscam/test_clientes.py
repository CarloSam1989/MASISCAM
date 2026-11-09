import json
from datetime import date

from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from accounts.models import Empresa, Perfil
from django.contrib.auth.models import User as Usuario
from . import views
from .forms import ClienteForm, FichaEquipoForm
from .models import Cliente, Equipo, Proyecto, RolMasiscam


class ClientesReductoresTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Empresa A", direccion="A")
        cls.otra = Empresa.objects.create(nombre="Empresa B", direccion="B")
        cls.taller = Usuario.objects.create_user(username="taller-clientes")
        taller_perfil = Perfil.objects.create(user=cls.taller, empresa=cls.empresa)
        RolMasiscam.objects.create(perfil=taller_perfil, rol="TALLER")
        otro_perfil = Perfil.objects.create(user=cls.taller, empresa=cls.otra)
        RolMasiscam.objects.create(perfil=otro_perfil, rol="TALLER")
        cls.usuario = Usuario.objects.create_user(username="cliente-test")
        perfil = Perfil.objects.create(user=cls.usuario, empresa=cls.empresa)
        RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.ADMINISTRADOR)
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre_comercial="Camaronera Norte", ruc="0991234567001", razon_social="Cliente técnico S.A.", correo="cliente@example.com", telefono="0999999999")
        cls.ajeno = Cliente.objects.create(empresa=cls.otra, nombre_comercial="Ajeno", ruc=cls.cliente.ruc, razon_social="Otra empresa")
        cls.proyecto = Proyecto.objects.create(empresa=cls.empresa, codigo="ANTIGUO", nombre="Camaronera antigua", razon_social="Razón anterior", identificacion_cliente="0012345678001", cliente="Cliente anterior", responsable="R", fecha_inicio=date(2026, 1, 1), creado_por=cls.usuario)
        cls.antiguo = Equipo.objects.create(proyecto=cls.proyecto, nombre="ANT-1", ubicacion="E", sector="S", marca="M", modelo="X", numero_serie="ANT-S", potencia_hp="20 HP")

    def request(self, nombre, datos=None, pk=None, post=False, json_response=False):
        url = reverse("masiscam:" + nombre, args=[pk] if pk else [])
        request = (RequestFactory().post if post else RequestFactory().get)(url, datos or {}, HTTP_ACCEPT="application/json" if json_response else "text/html")
        request.user = self.taller if nombre == "ficha_crear" else self.usuario
        request.empresa_activa = self.empresa
        request.session = {}
        request._messages = FallbackStorage(request)
        request.resolver_match = resolve(url)
        return request

    def datos_reductor(self, **cambios):
        datos = dict(cliente=self.cliente.pk, camaronera="Camaronera nueva", estacion="E1", sector="S1", numero_equipo="RED-1", marca="Marca", modelo="M1", numero_serie="SER-1", potencia="20 HP", consulta_publica_activa="on")
        datos.update(cambios)
        return datos

    def test_busqueda_ruc_solo_empresa_activa(self):
        response = views.clientes(self.request("clientes", {"formato": "json", "ruc": "099"}))
        datos = json.loads(response.content)["clientes"]
        self.assertEqual([c["id"] for c in datos], [self.cliente.pk])
        self.assertEqual(datos[0]["correo"], self.cliente.correo)
        self.assertEqual(json.loads(views.clientes(self.request("clientes", {"formato": "json", "ruc": "123"})).content)["clientes"], [])

    def test_crear_modal_y_reutilizar_ruc_sin_duplicar(self):
        datos = dict(nombre_comercial="Nuevo", ruc="0011122233001", razon_social="Nuevo S.A.", correo="nuevo@example.com", telefono="123")
        response = views.cliente_crear(self.request("cliente_crear", datos, post=True, json_response=True))
        self.assertEqual(response.status_code, 201)
        creado = json.loads(response.content)["cliente"]
        response = views.cliente_crear(self.request("cliente_crear", datos, post=True, json_response=True))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.content)["creado"])
        self.assertEqual(json.loads(response.content)["cliente"]["id"], creado["id"])
        self.assertEqual(Cliente.objects.filter(empresa=self.empresa, ruc=datos["ruc"]).count(), 1)

    def test_duplicado_y_validacion_correo(self):
        form = ClienteForm(dict(nombre_comercial="Otro", ruc=self.cliente.ruc, razon_social="Otro", correo="incorrecto"), empresa=self.empresa)
        self.assertFalse(form.is_valid())
        self.assertIn("ruc", form.errors)
        self.assertIn("correo", form.errors)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cliente.objects.create(empresa=self.empresa, nombre_comercial="Duplicado", ruc=self.cliente.ruc, razon_social="Otro")

    def test_editar_cliente_y_rechazar_detalle_ajeno(self):
        datos = dict(nombre_comercial="Nombre actualizado", ruc=self.cliente.ruc, razon_social="Razón actualizada", correo="actual@example.com", telefono="321")
        response = views.cliente_editar(self.request("cliente_editar", datos, pk=self.cliente.pk, post=True), self.cliente.pk)
        self.assertEqual(response.status_code, 302)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.correo, "actual@example.com")
        self.assertContains(views.cliente_detalle(self.request("cliente_detalle", pk=self.cliente.pk), self.cliente.pk), "Nombre actualizado")
        with self.assertRaises(Http404):
            views.cliente_detalle(self.request("cliente_detalle", pk=self.ajeno.pk), self.ajeno.pk)

    def test_guardar_reductor_cliente_y_precarga_sin_copiar(self):
        response = views.ficha_crear(self.request("ficha_crear", self.datos_reductor(), post=True))
        self.assertEqual(response.status_code, 302)
        equipo = Equipo.objects.get(nombre="RED-1")
        self.assertEqual(equipo.cliente_id, self.cliente.pk)
        self.assertEqual(equipo.proyecto.razon_social, "")
        self.assertEqual(equipo.proyecto.identificacion_cliente, "")
        form = FichaEquipoForm(empresa=self.empresa, equipo=equipo)
        self.assertEqual(form.cliente_inicial["id"], self.cliente.pk)
        self.assertEqual(form["cliente"].value(), self.cliente.pk)
        self.assertFalse(form.permite_sin_cliente)
        self.cliente.razon_social = "Razón actualizada"
        self.cliente.save()
        equipo.refresh_from_db()
        self.assertEqual(equipo.razon_social_cliente, "Razón actualizada")
        response = views.ficha_detalle(self.request("ficha_detalle", pk=equipo.pk), equipo.pk)
        self.assertContains(response, "Razón actualizada")
        response = views.equipo_publico(self.request("equipo_publico", pk=equipo.token_publico), equipo.token_publico)
        self.assertContains(response, "Razón actualizada")
        self.assertContains(response, self.cliente.ruc)

    def test_cliente_ajeno_y_nuevo_sin_cliente_rechazados(self):
        for cliente in (self.ajeno.pk, ""):
            form = FichaEquipoForm(self.datos_reductor(cliente=cliente, razon_social="Manual"), empresa=self.empresa)
            self.assertFalse(form.is_valid())
            self.assertIn("cliente", form.errors)
            self.assertIsNone(form.cliente_inicial)

    def test_legacy_conserva_datos_y_tokens(self):
        token = self.antiguo.token_publico
        form = FichaEquipoForm(empresa=self.empresa, equipo=self.antiguo)
        self.assertTrue(form.permite_sin_cliente)
        datos = {name: form[name].value() for name in form.fields if name not in ("fotografia_equipo", "fotografia_placa")}
        datos["marca"] = "Marca nueva"
        form = FichaEquipoForm(datos, empresa=self.empresa, equipo=self.antiguo)
        self.assertTrue(form.is_valid(), form.errors)
        equipo = form.save(usuario=self.usuario if form.equipo else self.taller)
        self.assertIsNone(equipo.cliente_id)
        self.assertEqual(equipo.token_publico, token)
        self.assertEqual(equipo.razon_social_cliente, "Razón anterior")
        self.assertEqual(equipo.ruc_cliente, "0012345678001")

    def test_dashboard_card_y_listado(self):
        response = views.dashboard(self.request("dashboard"))
        self.assertContains(response, "Reductores")
        self.assertContains(response, reverse("masiscam:producto_listado", args=["reductor"]))
        request = RequestFactory().get(reverse("masiscam:producto_listado", args=["reductor"]))
        request.user, request.empresa_activa, request.session = self.usuario, self.empresa, {}
        request.resolver_match = resolve(request.path)
        self.assertContains(views.producto_listado(request, "reductor"), "ANT-1")

    def test_productos_por_cliente_y_tipo(self):
        asociado = Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="ASOCIADO", numero_serie="ASOC")
        Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, tipo_producto="BOMBA", nombre="BOMBA-1", numero_serie="B1")
        categorias = views._productos(self.cliente.equipos.all(), self.cliente)
        self.assertEqual([(p["nombre"], p["total"]) for p in categorias], [("Reductores", 1), ("Bombas", 1)])
        response = views.cliente_detalle(self.request("cliente_detalle", pk=self.cliente.pk), self.cliente.pk)
        self.assertContains(response, categorias[0]["url"])
        self.assertContains(response, "Bombas")
        for tipo, esperado, excluido in [("reductor", "ASOCIADO", "BOMBA-1"), ("bomba", "BOMBA-1", "ASOCIADO")]:
            request = RequestFactory().get(reverse("masiscam:producto_listado", args=[tipo]), {"cliente": self.cliente.pk})
            request.user, request.empresa_activa, request.session = self.usuario, self.empresa, {}
            request.resolver_match = resolve(request.path)
            response = views.producto_listado(request, tipo)
            self.assertContains(response, esperado)
            self.assertNotContains(response, excluido)
            self.assertNotContains(response, "ANT-1")
            self.assertNotContains(response, self.ajeno.nombre_comercial)
        self.assertContains(views.dashboard(self.request("dashboard")), "&#9881;")
        for valor in [str(self.ajeno.pk), "invalido", "9" * 30]:
            request = RequestFactory().get("/masiscam/productos/reductor/", {"cliente": valor})
            request.user, request.empresa_activa, request.session = self.usuario, self.empresa, {}
            with self.assertRaises(Http404):
                views.producto_listado(request, "reductor")

    def test_aceite_creacion_precarga_edicion_y_distribucion(self):
        crear = views.ficha_crear(self.request("ficha_crear"))
        self.assertContains(crear, 'name="tipo_aceite"')
        self.assertContains(crear, 'name="numero_equipo"')
        form = FichaEquipoForm(self.datos_reductor(tipo_aceite="ISO VG 220"), empresa=self.empresa)
        self.assertTrue(form.is_valid(), form.errors)
        equipo = form.save(usuario=self.usuario if form.equipo else self.taller)
        self.assertEqual(equipo.tipo_aceite, "ISO VG 220")
        inicial = FichaEquipoForm(empresa=self.empresa, equipo=equipo)
        self.assertEqual(inicial["tipo_aceite"].value(), "ISO VG 220")
        editar = views.ficha_editar(self.request("ficha_editar", pk=equipo.pk), equipo.pk)
        self.assertContains(editar, 'value="ISO VG 220"')
        form = FichaEquipoForm(self.datos_reductor(tipo_aceite="ISO VG 320"), empresa=self.empresa, equipo=equipo)
        self.assertTrue(form.is_valid(), form.errors)
        form.save(usuario=self.usuario if form.equipo else self.taller)
        equipo.refresh_from_db()
        self.assertEqual(equipo.tipo_aceite, "ISO VG 320")
        self.assertEqual(equipo.nombre, "RED-1")
        for respuesta in (
            views.ficha_detalle(self.request("ficha_detalle", pk=equipo.pk), equipo.pk),
            views.equipo_publico(self.request("equipo_publico", pk=equipo.token_publico), equipo.token_publico),
        ):
            self.assertContains(respuesta, "ISO VG 320")
            self.assertContains(respuesta, "INFORMACI\u00d3N DEL REDUCTOR")
            self.assertNotContains(respuesta, "Fabricante")
            self.assertNotContains(respuesta, "Informaci\u00f3n t\u00e9cnica")
            html = respuesta.content.decode()
            self.assertLess(html.index("<dt>N\u00famero de equipo</dt>"), html.index("INFORMACI\u00d3N DEL REDUCTOR"))

    def test_etiqueta_ratio_y_enlace_en_nueva_pestana(self):
        from django.template.loader import render_to_string
        self.antiguo.ratio = "7:2"
        html = render_to_string("masiscam/equipo_etiqueta.html", {"equipo": self.antiguo, "qr_src": "QR_EXISTENTE"})
        self.assertIn("<dt>Ratio</dt><dd>7:2</dd>", html)
        self.assertNotIn("Potencia", html)
        self.assertIn('src="QR_EXISTENTE"', html)
        self.assertIn("size: 100mm 100mm", html)
        respuesta = views.ficha_detalle(self.request("ficha_detalle", pk=self.antiguo.pk), self.antiguo.pk)
        url = reverse("masiscam:equipo_etiqueta", args=[self.antiguo.pk])
        self.assertContains(respuesta, f'href="{url}" target="_blank" rel="noopener"')

    def test_tipos_registro_nuevos_y_anteriores(self):
        import uuid
        from .forms import RegistroEquipoForm
        from .models import RegistroEquipo
        esperados = [("NUEVO", "Nuevo"), ("ASISTENCIA", "Asistencia"), ("GARANTIA", "Garant\u00eda"), ("MANTENIMIENTO", "Mantenimiento"), ("REPARACION", "Reparaci\u00f3n")]
        self.assertEqual(list(RegistroEquipo.Tipo.choices), esperados)
        opciones = list(RegistroEquipoForm().fields["tipo"].choices)
        self.assertEqual(opciones[1:], esperados)
        for valor, etiqueta in esperados:
            form = RegistroEquipoForm({"tipo": valor, "fecha": "2026-09-16", "clave_creacion": str(uuid.uuid4())})
            self.assertTrue(form.is_valid(), form.errors)
            registro = form.save(commit=False)
            registro.equipo = self.antiguo
            registro.full_clean()
            registro.save()
            registro.refresh_from_db()
            self.assertEqual(registro.get_tipo_display(), etiqueta)
        self.assertEqual(self.antiguo.registros.count(), 5)

    def test_orden_informacion_general_en_informe(self):
        from django.template.loader import render_to_string
        html = render_to_string("masiscam/_identificacion_datos.html", {"equipo": self.antiguo})
        campos = ["Raz\u00f3n social", "Camaronera", "RUC", "Sector", "Estaci\u00f3n", "N\u00famero de equipo"]
        posiciones = [html.index(f"<dt>{campo}</dt>") for campo in campos]
        self.assertEqual(posiciones, sorted(posiciones))
        self.assertIn("INFORMACI\u00d3N DEL REDUCTOR", html)

    def test_editar_camaronera_aisla_reductor_por_pk(self):
        for legacy, destino_existente in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(legacy=legacy, destino_existente=destino_existente):
                sufijo = f"{legacy}-{destino_existente}"
                origen = Proyecto.objects.create(empresa=self.empresa, codigo="ORIGEN-" + sufijo, nombre="LOS TIGRES", razon_social="Razon original", identificacion_cliente="001", cliente="Original", responsable="R", fecha_inicio=date(2026, 1, 1), creado_por=self.usuario)
                destino = None
                if destino_existente:
                    destino = Proyecto.objects.create(empresa=self.empresa, codigo="DESTINO-" + sufijo, nombre="LOS TIGRES 2", razon_social="No cambiar", cliente="Destino", responsable="R", fecha_inicio=date(2026, 1, 1), creado_por=self.usuario)
                equipos = [Equipo.objects.create(proyecto=origen, cliente=None if legacy else self.cliente, nombre=f"RED-{i}-{sufijo}", ubicacion="E", sector="S", marca="M", modelo="X", numero_serie=f"SER-{i}-{sufijo}", potencia_hp="20 HP") for i in (1, 2)]
                reductor1, reductor2 = equipos
                antes_proyectos = {p.pk: Proyecto.objects.filter(pk=p.pk).values().get() for p in (origen, destino) if p}
                antes_segundo = Equipo.objects.filter(pk=reductor2.pk).values().get()
                form = FichaEquipoForm(empresa=self.empresa, equipo=reductor1)
                datos = {nombre: form[nombre].value() for nombre in form.fields if nombre not in ("fotografia_equipo", "fotografia_placa")}
                datos = {nombre: valor for nombre, valor in datos.items() if valor is not None and valor is not False}
                datos["camaronera"] = "LOS TIGRES 2"
                respuesta = views.ficha_editar(self.request("ficha_editar", datos, pk=reductor1.pk, post=True), reductor1.pk)
                self.assertEqual(respuesta.status_code, 302)
                reductor1.refresh_from_db()
                reductor2.refresh_from_db()
                self.assertEqual(reductor1.proyecto.nombre, "LOS TIGRES 2")
                self.assertEqual(reductor2.proyecto.nombre, "LOS TIGRES")
                self.assertNotEqual(reductor1.proyecto_id, origen.pk)
                self.assertEqual(Equipo.objects.filter(pk=reductor2.pk).values().get(), antes_segundo)
                for pk, antes in antes_proyectos.items():
                    self.assertEqual(Proyecto.objects.filter(pk=pk).values().get(), antes)
                precarga = FichaEquipoForm(empresa=self.empresa, equipo=reductor1)
                self.assertEqual(precarga["camaronera"].value(), "LOS TIGRES 2")
                # Limpiar los nombres para aislar cada variante sin modificar sus equipos.
                Proyecto.objects.filter(empresa=self.empresa, nombre__in=["LOS TIGRES", "LOS TIGRES 2"]).delete()

class ClientesRBACTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_cliente_access import ClienteAccessTests
        from .models import RegistroEquipo, ExpedienteMantenimiento, AsignacionTecnica
        ClienteAccessTests.setUpTestData.__func__(cls)
        cls.roles = {}
        for codigo in ('OFICINA', 'TECNICO', 'PLANTA', 'GERENCIA', 'CONSULTA'):
            user = Usuario.objects.create_user(username='rbac-' + codigo)
            perfil = Perfil.objects.create(user=user, empresa=cls.empresa)
            cls.roles[codigo] = RolMasiscam.objects.create(perfil=perfil, rol=codigo)
        cls.roles['ADMIN'] = cls.admin.perfiles.get().rol_masiscam
        cls.roles['CLIENTE'] = cls.cliente_user.perfiles.get().rol_masiscam
        registro = RegistroEquipo.objects.create(equipo=cls.equipo, tipo='MANTENIMIENTO', fecha=date.today())
        cls.exp = ExpedienteMantenimiento.objects.create(registro=registro, empresa=cls.empresa,
            cliente_contractual=cls.cliente, correlativo=1, numero='EXP-RBAC-1',
            huella_solicitud='a' * 64, creado_por=cls.admin)
        cls.asignacion = AsignacionTecnica.objects.create(expediente=cls.exp,
            tecnico=cls.roles['TECNICO'].perfil.user, asignado_por=cls.roles['PLANTA'].perfil.user, motivo='QA')
        cls.externa = Empresa.objects.create(nombre='RBAC Externa')
        cls.cliente_externo = Cliente.objects.create(empresa=cls.externa, nombre_comercial='Externo RBAC', ruc='1234567890001')

    def login_rol(self, codigo):
        self.client.force_login(self.roles[codigo].perfil.user)

    def test_matriz_escritura_html_ajax_y_botones(self):
        for codigo in self.roles:
            with self.subTest(rol=codigo):
                self.login_rol(codigo)
                allowed = codigo in {'ADMIN', 'OFICINA'}
                for vista, args in [('cliente_crear', []), ('cliente_editar', [self.cliente.pk])]:
                    url = reverse('masiscam:' + vista, args=args)
                    self.assertEqual(self.client.get(url).status_code, 200 if allowed else 403)
                    self.assertEqual(self.client.post(url, {}, HTTP_ACCEPT='application/json').status_code, 400 if allowed else 403)
                    self.assertEqual(self.client.post(url, {}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 400 if allowed else 403)
                if codigo != 'CLIENTE':
                    response = self.client.get(reverse('masiscam:clientes'))
                    self.assertEqual(response.status_code, 200)
                    url = reverse('masiscam:cliente_crear')
                    (self.assertContains if allowed else self.assertNotContains)(response, 'href="' + url + '"')
                    response = self.client.get(reverse('masiscam:cliente_detalle', args=[self.cliente.pk]))
                    (self.assertContains if allowed else self.assertNotContains)(response, 'href="' + reverse('masiscam:cliente_editar', args=[self.cliente.pk]) + '"')

    def test_oficina_alta_edicion_aislamiento_y_sin_privilegios_tecnicos(self):
        self.login_rol('OFICINA')
        data = dict(nombre_comercial='Alta oficina', ruc='1234567890002', razon_social='QA', correo='qa@example.com')
        response = self.client.post(reverse('masiscam:cliente_crear'), dict(data, empresa=self.externa.pk), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 201)
        nuevo = Cliente.objects.get(ruc=data['ruc'])
        self.assertEqual(nuevo.empresa_id, self.empresa.pk)
        data['nombre_comercial'] = 'Editado oficina'
        self.assertEqual(self.client.post(reverse('masiscam:cliente_editar', args=[nuevo.pk]), data).status_code, 302)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(reverse('masiscam:cliente_editar', args=[self.cliente_externo.pk]), data).status_code, 404)
        from .access import PERMISOS_ROL
        self.assertEqual(PERMISOS_ROL['OFICINA'], {'ver', 'clientes_crear', 'clientes_editar'})
        self.assertEqual(self.client.get(reverse('masiscam:usuario_crear')).status_code, 403)

    def test_tecnico_lectura_asignada_ajax_selectores_y_revocacion(self):
        from django.utils import timezone
        from .forms import EquipoForm
        self.login_rol('TECNICO')
        url = reverse('masiscam:clientes')
        response = self.client.get(url)
        self.assertContains(response, self.cliente.nombre_comercial)
        self.assertNotContains(response, self.otro_cliente.nombre_comercial)
        response = self.client.get(url, {'formato': 'json', 'ruc': '099'})
        self.assertEqual([c['id'] for c in response.json()['clientes']], [self.cliente.pk])
        self.assertEqual(self.client.get(reverse('masiscam:cliente_detalle', args=[self.otro_cliente.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('masiscam:producto_listado', args=['reductor']), {'cliente': self.otro_cliente.pk}).status_code, 404)
        for form in (FichaEquipoForm(empresa=self.empresa, usuario=self.roles['TECNICO'].perfil.user),
                     EquipoForm(empresa=self.empresa, usuario=self.roles['TECNICO'].perfil.user)):
            self.assertEqual(list(form.fields['cliente'].queryset), [self.cliente])
        response = self.client.get(reverse('masiscam:ficha_crear'))
        self.assertEqual(response.status_code, 403)
        self.asignacion.fin = timezone.now()
        self.asignacion.save(update_fields=['fin'])
        self.assertEqual(self.client.get(url, {'formato': 'json', 'ruc': '099'}).json()['clientes'], [])
        self.assertEqual(self.client.get(reverse('masiscam:cliente_detalle', args=[self.cliente.pk])).status_code, 404)

    def test_sesion_rol_cambiado_y_post_no_modifican_clientes(self):
        self.login_rol('OFICINA')
        rol = self.roles['OFICINA']
        rol.rol = 'TECNICO'
        rol.save(update_fields=['rol'])
        antes = Cliente.objects.count()
        self.assertEqual(self.client.post(reverse('masiscam:cliente_crear'), {'nombre_comercial': 'No autorizado'}).status_code, 403)
        self.assertEqual(Cliente.objects.count(), antes)
        self.assertEqual(self.client.get(reverse('masiscam:cliente_detalle', args=[self.cliente.pk])).status_code, 404)


    def test_admin_django_no_elude_permiso_tecnico(self):
        from django.contrib.auth.models import Permission
        user = self.roles['TECNICO'].perfil.user
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='masiscam', content_type__model='cliente'))
        self.client.force_login(user)
        for vista, args in [('admin:masiscam_cliente_add', []),
                            ('admin:masiscam_cliente_change', [self.cliente.pk]),
                            ('admin:masiscam_cliente_delete', [self.cliente.pk])]:
            url = reverse(vista, args=args)
            self.assertRedirects(self.client.get(url), reverse('admin:login') + '?next=' + url, fetch_redirect_response=False)
            self.assertRedirects(self.client.post(url, {'nombre_comercial': 'Bypass'}), reverse('admin:login') + '?next=' + url, fetch_redirect_response=False)
        response = self.client.get(reverse('admin:masiscam_cliente_changelist'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('admin:login')))

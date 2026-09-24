from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Empresa, Perfil
from .models import Auditoria, RolMasiscam


class UsuariosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_cliente_access import ClienteAccessTests
        ClienteAccessTests.setUpTestData.__func__(cls)
        cls.tecnico = get_user_model().objects.create_user(username="tecnico-usuarios", password="Tecnico-789!")
        perfil = Perfil.objects.create(user=cls.tecnico, empresa=cls.empresa)
        cls.rol_tecnico = RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.TECNICO)

    def setUp(self):
        self.client.force_login(self.admin)

    def crear(self, **changes):
        datos = {"username": "nuevo-admin", "first_name": "Nuevo Administrador", "email": "",
                 "password1": "Inicial-Segura-847!", "password2": "Inicial-Segura-847!"}
        datos.update(changes)
        return self.client.post(reverse("masiscam:usuario_crear"), datos)

    def test_crear_admin_login_y_sin_privilegios_django(self):
        response = self.crear(is_staff="1", is_superuser="1", rol="CLIENTE", empresa="999")
        self.assertRedirects(response, reverse("masiscam:usuarios"))
        usuario = get_user_model().objects.get(username="nuevo-admin")
        self.assertTrue(usuario.check_password("Inicial-Segura-847!"))
        self.assertNotEqual(usuario.password, "Inicial-Segura-847!")
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        rol = usuario.perfiles.get().rol_masiscam
        self.assertEqual(rol.rol, RolMasiscam.Rol.ADMINISTRADOR)
        self.assertEqual(rol.perfil.empresa, self.empresa)
        self.assertIsNone(rol.cliente_id)
        self.client.logout()
        response = self.client.post(reverse("accounts:login"), {
            "username": usuario.username, "password": "Inicial-Segura-847!",
        })
        self.assertRedirects(response, reverse("masiscam:dashboard"))
        self.assertEqual(self.client.get(reverse("masiscam:usuarios")).status_code, 200)

    def test_duplicados_confirmacion_y_validadores_no_crean_cuentas(self):
        total = get_user_model().objects.count()
        for cambios in [{"username": self.admin.username.upper()}, {"password2": "Distinta-835!"},
                        {"password1": "123", "password2": "123"}, {"first_name": ""}]:
            with self.subTest(cambios=list(cambios)):
                response = self.crear(**cambios)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(get_user_model().objects.count(), total)

    def test_cambio_clave_invalida_anterior_y_sesion_sin_filtrar_secretos(self):
        otro = Client()
        self.assertTrue(otro.login(username=self.tecnico.username, password="Tecnico-789!"))
        url = reverse("masiscam:usuario_clave", args=[self.rol_tecnico.pk])
        response = self.client.get(url)
        self.assertNotContains(response, self.tecnico.password)
        self.assertNotContains(response, "Tecnico-789!")
        response = self.client.post(url, {"new_password1": "Nueva-Segura-982!", "new_password2": "Distinta-985!"})
        self.assertTrue(response.context["form"].errors)
        self.assertNotContains(response, 'value="Nueva-Segura-982!"')
        self.tecnico.refresh_from_db()
        self.assertTrue(self.tecnico.check_password("Tecnico-789!"))
        response = self.client.post(url, {"new_password1": "Nueva-Segura-982!", "new_password2": "Nueva-Segura-982!"})
        self.assertRedirects(response, reverse("masiscam:usuarios"))
        self.tecnico.refresh_from_db()
        self.assertTrue(self.tecnico.check_password("Nueva-Segura-982!"))
        self.assertFalse(self.tecnico.check_password("Tecnico-789!"))
        self.assertEqual(otro.get(reverse("masiscam:dashboard")).status_code, 302)
        self.assertFalse(otro.login(username=self.tecnico.username, password="Tecnico-789!"))
        self.assertTrue(otro.login(username=self.tecnico.username, password="Nueva-Segura-982!"))
        self.assertNotIn("Nueva-Segura-982!", str(list(Auditoria.objects.values())))

    def test_roles_sin_permiso_403_y_sin_menu(self):
        rutas = [reverse("masiscam:usuarios"), reverse("masiscam:usuario_crear"),
                 reverse("masiscam:usuario_editar", args=[self.rol_tecnico.pk]),
                 reverse("masiscam:usuario_clave", args=[self.rol_tecnico.pk])]
        for usuario in (self.cliente_user, self.tecnico):
            self.client.force_login(usuario)
            for url in rutas:
                self.assertEqual(self.client.get(url).status_code, 403)
            for url in rutas[1:] + [reverse("masiscam:usuario_estado", args=[self.rol_tecnico.pk])]:
                self.assertEqual(self.client.post(url, {"rol": "ADMIN", "activo": "1"}).status_code, 403)
            self.assertNotContains(self.client.get(reverse("masiscam:dashboard")), 'href="' + rutas[0] + '"')
        RolMasiscam.objects.filter(pk=self.rol_tecnico.pk).update(rol=RolMasiscam.Rol.CONSULTA)
        self.assertEqual(self.client.get(rutas[0]).status_code, 403)
        self.assertEqual(self.crear().status_code, 403)

    def test_activar_desactivar_bloquea_login_y_sesion(self):
        otro = Client()
        self.assertTrue(otro.login(username=self.tecnico.username, password="Tecnico-789!"))
        url = reverse("masiscam:usuario_estado", args=[self.rol_tecnico.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url, {"activo": "0"}), reverse("masiscam:usuarios"))
        self.assertEqual(otro.get(reverse("masiscam:dashboard")).status_code, 302)
        self.assertFalse(otro.login(username=self.tecnico.username, password="Tecnico-789!"))
        self.assertRedirects(self.client.post(url, {"activo": "1"}), reverse("masiscam:usuarios"))
        self.assertTrue(otro.login(username=self.tecnico.username, password="Tecnico-789!"))
        self.assertEqual(otro.get(reverse("masiscam:dashboard")).status_code, 200)
        propia = self.admin.perfiles.get().rol_masiscam
        self.client.post(reverse("masiscam:usuario_estado", args=[propia.pk]), {"activo": "0"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_editar_solo_datos_permitidos(self):
        response = self.client.post(reverse("masiscam:usuario_editar", args=[self.rol_tecnico.pk]), {
            "username": "tecnico-editado", "first_name": "Nombre editado", "email": "correo@example.com",
            "rol": "ADMIN", "is_staff": "1", "is_superuser": "1", "password": "texto-plano",
        })
        self.assertRedirects(response, reverse("masiscam:usuarios"))
        self.tecnico.refresh_from_db()
        self.rol_tecnico.refresh_from_db()
        self.assertEqual(self.tecnico.first_name, "Nombre editado")
        self.assertEqual(self.tecnico.email, "correo@example.com")
        self.assertEqual(self.tecnico.username, "tecnico-editado")
        self.assertEqual(self.rol_tecnico.rol, RolMasiscam.Rol.TECNICO)
        self.assertFalse(self.tecnico.is_superuser)
        self.assertFalse(self.tecnico.is_staff)
        self.assertTrue(self.tecnico.check_password("Tecnico-789!"))

    def test_aislamiento_empresa_superusuarios_y_cuentas_compartidas(self):
        empresa = Empresa.objects.create(nombre="Otra empresa")
        externo = get_user_model().objects.create_user(username="externo")
        perfil = Perfil.objects.create(user=externo, empresa=empresa)
        rol = RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.ADMINISTRADOR)
        self.assertNotContains(self.client.get(reverse("masiscam:usuarios")), "externo")
        for vista in ("usuario_editar", "usuario_clave", "usuario_estado"):
            self.assertEqual(self.client.post(reverse("masiscam:" + vista, args=[rol.pk])).status_code, 404)
        for protegido in ("superuser", "staff", "compartido"):
            self.tecnico.is_superuser = protegido == "superuser"
            self.tecnico.is_staff = protegido == "staff"
            self.tecnico.save()
            if protegido == "compartido":
                Perfil.objects.create(user=self.tecnico, empresa=empresa)
            for vista in ("usuario_editar", "usuario_clave", "usuario_estado"):
                self.assertEqual(self.client.post(reverse("masiscam:" + vista, args=[self.rol_tecnico.pk])).status_code, 403)

    def test_cambio_clave_propia_conserva_sesion(self):
        rol = self.admin.perfiles.get().rol_masiscam
        response = self.client.post(reverse("masiscam:usuario_clave", args=[rol.pk]), {
            "new_password1": "Propia-Segura-847!", "new_password2": "Propia-Segura-847!",
        })
        self.assertRedirects(response, reverse("masiscam:usuarios"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Propia-Segura-847!"))

    def test_csrf_y_menu_admin(self):
        response = self.client.get(reverse("masiscam:dashboard"))
        self.assertContains(response, 'href="' + reverse("masiscam:usuarios") + '"')
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        for vista, args in [("usuario_crear", []), ("usuario_editar", [self.rol_tecnico.pk]),
                            ("usuario_clave", [self.rol_tecnico.pk]), ("usuario_estado", [self.rol_tecnico.pk])]:
            self.assertEqual(cliente.post(reverse("masiscam:" + vista, args=args)).status_code, 403)

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Empresa
from .models import Cliente, Equipo, RolMasiscam
from . import test_cliente_access


class UsuarioAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_cliente_access.ClienteAccessTests.setUpTestData.__func__(cls)
        cls.superuser = get_user_model().objects.create_superuser(
            username="root-test", password="Root-test-123", email="root@example.com",
        )

    def setUp(self):
        self.client.force_login(self.superuser)
        self.url = reverse("admin:auth_user_add")
        self.data = dict(username="nuevo", password1="Clave-segura-456!",
                         password2="Clave-segura-456!", usable_password="true")

    def test_administrativo(self):
        response = self.client.post(self.url, dict(self.data, tipo="ADMIN", empresa=self.empresa.pk))
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="nuevo")
        self.assertTrue(user.check_password(self.data["password1"]))
        self.assertFalse(user.is_staff or user.is_superuser)
        rol = user.perfiles.get().rol_masiscam
        self.assertEqual(rol.rol, RolMasiscam.Rol.ADMINISTRADOR)
        self.assertIsNone(rol.cliente)
        test_cliente_access.ClienteAccessTests.login_as(self, user)
        self.assertEqual(self.client.get(reverse("masiscam:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("masiscam:cliente_editar", args=[self.cliente.pk])).status_code, 200)

    def test_cliente_creado_solo_consulta_sus_productos(self):
        response = self.client.post(self.url, dict(self.data, tipo="CLIENTE", cliente=self.otro_cliente.pk))
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="nuevo")
        self.assertEqual(user.perfiles.get().rol_masiscam.cliente, self.otro_cliente)
        self.assertFalse(user.is_staff or user.is_superuser)
        Equipo.objects.create(proyecto=self.otro_proyecto, cliente=self.otro_cliente, nombre="OTRO-PROPIO")
        test_cliente_access.ClienteAccessTests.login_as(self, user)
        response = self.client.get(reverse("masiscam:cliente_productos"))
        self.assertContains(response, "OTRO-PROPIO")
        self.assertContains(response, self.equipo_ajeno.nombre)
        self.assertNotContains(response, self.equipo.nombre)
        for vista in ("ficha_detalle", "equipo_informe"):
            self.assertEqual(self.client.get(reverse("masiscam:" + vista, args=[self.equipo_ajeno.pk])).status_code, 200)
            self.assertEqual(self.client.get(reverse("masiscam:" + vista, args=[self.equipo.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("masiscam:dashboard")).status_code, 403)
        self.assertEqual(self.client.post(reverse("masiscam:cliente_editar", args=[self.otro_cliente.pk]), {}).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_rechaza_vinculos_invalidos_sin_crear_usuario(self):
        otra_empresa = Empresa.objects.create(nombre="Otra")
        inactiva = Empresa.objects.create(nombre="Inactiva", activa=False)
        cliente_inactivo = Cliente.objects.create(empresa=inactiva, nombre_comercial="Inactivo", ruc="1")
        for extra in (
            dict(tipo="CLIENTE"),
            dict(tipo="CLIENTE", cliente=self.cliente.pk),
            dict(tipo="CLIENTE", cliente=self.otro_cliente.pk, empresa=otra_empresa.pk),
            dict(tipo="CLIENTE", cliente=cliente_inactivo.pk),
            dict(tipo="ADMIN"),
            dict(tipo="ADMIN", empresa=self.empresa.pk, cliente=self.otro_cliente.pk),
            dict(tipo="SUPERUSER", empresa=self.empresa.pk),
        ):
            with self.subTest(extra=extra):
                response = self.client.post(self.url, dict(self.data, **extra))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
                self.assertFalse(get_user_model().objects.filter(username="nuevo").exists())

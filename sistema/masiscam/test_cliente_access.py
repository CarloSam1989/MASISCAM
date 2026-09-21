from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Empresa, Perfil

from .models import Cliente, Documento, Equipo, Proyecto, RolMasiscam


class ClienteAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.empresa = Empresa.objects.create(nombre="Empresa cliente")
        cls.admin = User.objects.create_user(username="admin-interno", password="Admin-Pass-123")
        admin_perfil = Perfil.objects.create(user=cls.admin, empresa=cls.empresa)
        RolMasiscam.objects.create(perfil=admin_perfil, rol=RolMasiscam.Rol.ADMINISTRADOR)
        cls.cliente_user = User.objects.create_user(username="0991234567001", password="Cliente-Pass-123")
        cliente_perfil = Perfil.objects.create(user=cls.cliente_user, empresa=cls.empresa)
        cls.cliente = Cliente.objects.create(
            empresa=cls.empresa,
            nombre_comercial="Cliente autorizado",
            razon_social="Cliente autorizado S.A.",
            ruc="0991234567001",
        )
        cls.otro_cliente = Cliente.objects.create(
            empresa=cls.empresa,
            nombre_comercial="Cliente ajeno",
            razon_social="Cliente ajeno S.A.",
            ruc="0991234567002",
        )
        RolMasiscam.objects.create(perfil=cliente_perfil, rol=RolMasiscam.Rol.CLIENTE, cliente=cls.cliente)
        cls.proyecto = Proyecto.objects.create(
            empresa=cls.empresa,
            codigo="CLI-1",
            nombre="Proyecto cliente",
            cliente="Cliente autorizado",
            responsable="Responsable",
            fecha_inicio=date.today(),
            creado_por=cls.admin,
        )
        cls.otro_proyecto = Proyecto.objects.create(
            empresa=cls.empresa,
            codigo="CLI-2",
            nombre="Proyecto ajeno",
            cliente="Cliente ajeno",
            responsable="Responsable",
            fecha_inicio=date.today(),
            creado_por=cls.admin,
        )
        cls.equipo = Equipo.objects.create(
            proyecto=cls.proyecto,
            cliente=cls.cliente,
            nombre="EQ-CLIENTE",
            numero_serie="SER-CLIENTE",
        )
        cls.equipo_ajeno = Equipo.objects.create(
            proyecto=cls.otro_proyecto,
            cliente=cls.otro_cliente,
            nombre="EQ-AJENO",
            numero_serie="SER-AJENO",
        )
        cls.documento_ajeno = Documento.objects.create(
            proyecto=cls.otro_proyecto,
            equipo=cls.equipo_ajeno,
            categoria=Documento.Categoria.MANUALES,
            titulo="Documento ajeno",
            archivo="ajeno.pdf",
            nombre_original="ajeno.pdf",
            tipo_mime="application/pdf",
            hash_sha256="a" * 64,
            subido_por=cls.admin,
        )

    def login_as(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["empresa_activa_id"] = self.empresa.pk
        session.save()

    def test_crear_usuario_cliente_desde_cliente(self):
        self.login_as(self.admin)
        response = self.client.post(reverse("masiscam:cliente_usuario_crear", args=[self.otro_cliente.pk]))
        self.assertRedirects(response, reverse("masiscam:cliente_detalle", args=[self.otro_cliente.pk]))
        user = get_user_model().objects.get(username=self.otro_cliente.ruc)
        self.assertTrue(user.check_password(self.otro_cliente.ruc))
        self.assertEqual(user.perfiles.get().rol_masiscam.cliente_id, self.otro_cliente.pk)
        self.client.post(reverse("masiscam:cliente_usuario_crear", args=[self.otro_cliente.pk]))
        self.assertEqual(get_user_model().objects.filter(username=self.otro_cliente.ruc).count(), 1)

    def test_cliente_con_un_producto_va_directo_a_ficha(self):
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:cliente_productos"))
        self.assertRedirects(response, reverse("masiscam:ficha_detalle", args=[self.equipo.pk]))

    def test_cliente_con_varios_productos_ve_solo_los_suyos(self):
        segundo = Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="EQ-CLIENTE-2")
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:cliente_productos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.equipo.nombre)
        self.assertContains(response, segundo.nombre)
        self.assertNotContains(response, self.equipo_ajeno.nombre)

    def test_cliente_bloquea_producto_ajeno_por_url(self):
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:ficha_detalle", args=[self.equipo_ajeno.pk]))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse("masiscam:equipo_informe", args=[self.equipo_ajeno.pk]))
        self.assertEqual(response.status_code, 403)

    def test_cliente_bloquea_documento_ajeno(self):
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:documento_privado", args=[self.otro_proyecto.pk, self.documento_ajeno.pk]))
        self.assertEqual(response.status_code, 404)

    def test_qr_protegido_redirige_a_login_y_retorna(self):
        destino = reverse("masiscam:ficha_detalle", args=[self.equipo.pk])
        response = self.client.get(destino)
        self.assertRedirects(response, reverse("accounts:login") + "?next=" + destino, fetch_redirect_response=False)
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.cliente_user.username, "password": "Cliente-Pass-123", "next": destino},
        )
        self.assertRedirects(response, destino, fetch_redirect_response=False)
        self.assertEqual(self.client.get(destino).status_code, 200)

    def test_usuario_interno_conserva_acceso(self):
        self.login_as(self.admin)
        response = self.client.get(reverse("masiscam:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reductores")

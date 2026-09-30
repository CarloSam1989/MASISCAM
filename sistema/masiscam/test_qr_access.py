from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Perfil
from . import test_cliente_access
from .models import Equipo, RolMasiscam


@override_settings(DEBUG=False, GOOGLE_DRIVE_ENABLED=False)
class QrAccessTests(TestCase):
    setUpTestData = classmethod(test_cliente_access.ClienteAccessTests.setUpTestData.__func__)

    def setUp(self):
        self.url = reverse("masiscam:equipo_publico", args=[self.equipo.token_publico])
        self.login_url = reverse("accounts:login")
        self.denegado = "Este equipo no está asignado a su cliente."

    def test_qr_sin_sesion_login_next_y_retorno_dueno(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, self.login_url + "?next=" + self.url)
        self.assertContains(self.client.get(response.url), f'value="{self.url}"')
        response = self.client.post(self.login_url, {
            "username": self.cliente_user.username, "password": "Cliente-Pass-123", "next": self.url,
        }, follow=True)
        self.assertEqual(response.redirect_chain, [(self.url, 302)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["equipo"].pk, self.equipo.pk)
        self.assertTrue(response.context["privado"])
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_login_cliente_incorrecto_mensaje_403(self):
        usuario = get_user_model().objects.create_user(username="otro-qr", password="Otra-Clave-123")
        perfil = Perfil.objects.create(user=usuario, empresa=self.empresa)
        RolMasiscam.objects.create(perfil=perfil, rol="CLIENTE", cliente=self.otro_cliente)
        response = self.client.post(self.login_url, {
            "username": usuario.username, "password": "Otra-Clave-123", "next": self.url,
        }, follow=True)
        self.assertContains(response, self.denegado, status_code=403)
        self.assertNotContains(response, self.equipo.numero_serie, status_code=403)

    def test_cambio_manual_id_token_y_ruta_legacy(self):
        self.client.force_login(self.cliente_user)
        for vista in ("ficha_detalle", "equipo_informe"):
            self.assertContains(self.client.get(reverse("masiscam:" + vista, args=[self.equipo_ajeno.pk])), self.denegado, status_code=403)
        for namespace in ("masiscam", "legacy_public"):
            url = reverse(namespace + ":equipo_publico", args=[self.equipo_ajeno.token_publico])
            self.assertContains(self.client.get(url), self.denegado, status_code=403)
        self.client.logout()
        legacy = reverse("legacy_public:equipo_publico", args=[self.equipo.token_publico])
        self.assertRedirects(self.client.get(legacy), self.login_url + "?next=" + legacy)

    def test_admin_e_interno_acceso_normal_sin_regenerar_qr(self):
        token = self.equipo.token_publico
        for rol in ("ADMIN", "TECNICO"):
            RolMasiscam.objects.filter(perfil__user=self.admin).update(rol=rol)
            self.client.force_login(self.admin)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["equipo"].pk, self.equipo.pk)
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.token_publico, token)

    def test_equipo_sin_cliente_bloqueado(self):
        Equipo.objects.filter(pk=self.equipo.pk).update(cliente=None)
        self.client.force_login(self.cliente_user)
        self.assertContains(self.client.get(self.url), self.denegado, status_code=403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), "Este equipo no tiene un cliente asignado.", status_code=403)

    def test_next_se_conserva_tras_clave_incorrecta(self):
        self.client.post(self.login_url, {"username": self.cliente_user.username, "password": "incorrecta", "next": self.url})
        response = self.client.post(self.login_url, {"username": self.cliente_user.username, "password": "Cliente-Pass-123"})
        self.assertRedirects(response, self.url)

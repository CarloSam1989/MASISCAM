from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Empresa, Perfil
from masiscam.models import RolMasiscam


class LoginFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "test-login-password"
        cls.user = get_user_model().objects.create_user(username="admin", password=cls.password)
        cls.empresa = Empresa.objects.create(nombre="MASISCAM")
        perfil = Perfil.objects.create(user=cls.user, empresa=cls.empresa, activo=True)
        RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.ADMINISTRADOR, activo=True)

    def test_login_page_and_password_toggle(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'action="/app/cuentas/login/"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'type="button"')
        self.assertContains(response, 'id="toggle-password"')
        self.assertContains(response, 'aria-label="Mostrar contraseña"')
        self.assertContains(response, 'aria-pressed="false"')
        self.assertContains(response, 'class="password-wrapper"')
        self.assertNotContains(response, 'type="checkbox"')
        self.assertContains(response, 'static/app/login.js')

    def test_invalid_credentials_show_error_and_do_not_authenticate(self):
        response = self.client.post(reverse("accounts:login"), {"username": "admin", "password": "wrong-password"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "Usuario o contraseña incorrectos.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_valid_credentials_keep_session_and_open_dashboard(self):
        response = self.client.post(reverse("accounts:login"), {"username": "admin", "password": self.password})
        self.assertRedirects(response, "/app/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        self.assertEqual(self.client.get("/app/").status_code, 200)
        self.assertEqual(self.client.session.get("empresa_activa_id"), self.empresa.pk)

    def test_safe_next_is_preserved(self):
        response = self.client.post(reverse("accounts:login"), {"username": "admin", "password": self.password, "next": "/app/proyectos/"})
        self.assertRedirects(response, "/app/proyectos/", fetch_redirect_response=False)

    def test_safe_next_survives_invalid_login_retry(self):
        response = self.client.post(reverse("accounts:login"), {"username": "admin", "password": "incorrecta", "next": "/app/proyectos/"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("accounts:login"), {"username": "admin", "password": self.password})
        self.assertRedirects(response, "/app/proyectos/", fetch_redirect_response=False)

    def test_external_next_is_rejected(self):
        response = self.client.post(reverse("accounts:login"), {"username": "admin", "password": self.password, "next": "https://evil.example/"})
        self.assertRedirects(response, "/app/", fetch_redirect_response=False)
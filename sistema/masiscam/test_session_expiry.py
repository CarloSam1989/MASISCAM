from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import test_cliente_access


@override_settings(GOOGLE_DRIVE_ENABLED=False)
class SessionExpiryTests(TestCase):
    setUpTestData = classmethod(test_cliente_access.ClienteAccessTests.setUpTestData.__func__)

    def test_ocho_horas_absolutas_admin_cliente_y_retorno_qr(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 28800)
        self.assertFalse(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)
        inicio = timezone.now().replace(microsecond=0)
        qr = reverse("masiscam:equipo_publico", args=[self.equipo.token_publico])
        login = reverse("accounts:login")
        for usuario, clave in ((self.admin, "Admin-Pass-123"), (self.cliente_user, "Cliente-Pass-123")):
            with self.subTest(rol=usuario.username):
                client = Client()
                with patch("django.utils.timezone.now", return_value=inicio):
                    response = client.post(login, {"username": usuario.username, "password": clave})
                    self.assertEqual(response.status_code, 302)
                    key = client.session.session_key
                    limite = inicio + timedelta(hours=8)
                    self.assertEqual(Session.objects.get(session_key=key).expire_date, limite)
                    self.assertEqual(int(response.cookies[settings.SESSION_COOKIE_NAME]["max-age"]), 28800)
                for avance in (timedelta(hours=4), timedelta(hours=8, seconds=-1)):
                    with patch("django.utils.timezone.now", return_value=inicio + avance):
                        self.assertEqual(client.get(qr).status_code, 200)
                        self.assertEqual(Session.objects.get(session_key=key).expire_date, limite)
                with patch("django.utils.timezone.now", return_value=limite):
                    self.assertRedirects(client.get(qr), login + "?next=" + qr)
                    response = client.get(reverse("masiscam:dashboard"), follow=True)
                    self.assertEqual(response.resolver_match.view_name, "accounts:login")
                    response = client.post(login, {"username": usuario.username, "password": clave, "next": qr}, follow=True)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["equipo"].pk, self.equipo.pk)
                    self.assertEqual(client.session.get_expiry_date(), limite + timedelta(hours=8))

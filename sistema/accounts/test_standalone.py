import io
import json
import tempfile
import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.management import call_command

from accounts.models import Empresa, Perfil
from masiscam.models import Cliente, Equipo, Proyecto, RegistroEquipo, RolMasiscam, Documento


class StandaloneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="MASISCAM.ADMIN", password="Clave-Prueba-123")
        cls.empresa = Empresa.objects.create(nombre="Empresa propia")
        cls.otra = Empresa.objects.create(nombre="Otra empresa")
        cls.perfil = Perfil.objects.create(user=cls.user, empresa=cls.empresa)
        cls.rol = RolMasiscam.objects.create(perfil=cls.perfil, rol=RolMasiscam.Rol.ADMINISTRADOR)
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre_comercial="Camaronera", razon_social="EMPRESA CAMARONERA XYZ", ruc="0991234567001")

    def login(self):
        return self.client.post(reverse("accounts:login"), {"username": self.user.username, "password": "Clave-Prueba-123", "next": "https://ejemplo.invalid/"})

    def crear(self, nombre="RED-1", **extra):
        datos = dict(cliente=self.cliente.pk, camaronera="LOS TIGRES", estacion="E1", sector="S1", numero_equipo=nombre, marca="Marca", modelo="M1", numero_serie=nombre + "-SER", potencia="20 HP", ratio="7:2", tipo_aceite="ISO VG 220", consulta_publica_activa="on")
        datos.update(extra)
        with patch("masiscam.signals.encolar_carpeta_equipo"):
            respuesta = self.client.post(reverse("masiscam:ficha_crear"), datos)
        self.assertEqual(respuesta.status_code, 302)
        return Equipo.objects.get(nombre=nombre), datos

    def test_web_adjunta_y_rutas_publicas_sin_login(self):
        self.assertEqual(reverse("web:home"), "/")
        web = self.client.get("/")
        self.assertContains(web, "/static/web/styles-v2.css")
        self.assertContains(web, "/static/web/assets/masiscam-logo.png")
        self.assertContains(web, 'action="/consulta/"')
        self.assertContains(web, 'name="code"')
        self.assertContains(web, "Cada equipo.")
        self.assertNotContains(web, "SYSCloud")
        self.assertEqual(self.client.get("/consulta/").status_code, 200)
        self.assertRedirects(self.client.get("/app/"), reverse("accounts:login") + "?next=/app/", fetch_redirect_response=False)

    def test_login_y_ficha_edicion_etiqueta_qr_informe_consulta(self):
        self.assertRedirects(self.login(), "/app/", fetch_redirect_response=False)
        dashboard = self.client.get("/app/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, "SYSCloud")
        equipo, datos = self.crear()
        segundo, _ = self.crear("RED-2")
        datos.update(camaronera="LOS TIGRES 2", ratio="15:1")
        respuesta = self.client.post(reverse("masiscam:ficha_editar", args=[equipo.pk]), datos)
        self.assertEqual(respuesta.status_code, 302)
        equipo.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(equipo.proyecto.nombre, "LOS TIGRES 2")
        self.assertEqual(segundo.proyecto.nombre, "LOS TIGRES")
        editar = self.client.get(reverse("masiscam:ficha_editar", args=[equipo.pk]))
        self.assertEqual(editar.context["form"]["ratio"].value(), "15:1")
        self.assertEqual(editar.context["form"]["tipo_aceite"].value(), "ISO VG 220")
        detalle = self.client.get(reverse("masiscam:ficha_detalle", args=[equipo.pk]))
        self.assertContains(detalle, "INFORMACI\u00d3N DEL REDUCTOR")
        self.assertContains(detalle, "15:1")
        self.assertNotContains(detalle, "Fabricante")
        etiqueta = self.client.get(reverse("masiscam:equipo_etiqueta", args=[equipo.pk]))
        self.assertContains(etiqueta, "<dt>Ratio</dt><dd>15:1</dd>", html=True)
        self.assertContains(etiqueta, "size: 100mm 100mm")
        self.assertNotContains(etiqueta, "Potencia")
        qr = self.client.get(reverse("masiscam:equipo_qr", args=[equipo.pk]))
        self.assertEqual(qr["Content-Type"], "image/png")
        self.assertTrue(qr.content.startswith(b"\x89PNG"))
        url_publica = reverse("masiscam:equipo_publico", args=[equipo.token_publico])
        self.assertTrue(url_publica.startswith("/consulta/"))
        self.client.logout()
        self.assertContains(self.client.get(url_publica), "15:1")
        for codigo in (equipo.token_publico, equipo.nombre, equipo.numero_serie):
            self.assertRedirects(self.client.get("/consulta/", {"code": codigo}), url_publica)
        equipo.consulta_publica_activa = False
        equipo.save()
        self.assertNotContains(self.client.get(url_publica), "15:1")
        self.assertEqual(self.client.get("/consulta/", {"code": equipo.token_publico}).status_code, 200)

    def test_aislamiento_permisos_y_tipos_de_registro(self):
        self.login()
        equipo, _ = self.crear()
        ajeno = Proyecto.objects.create(empresa=self.otra, codigo="AJENO", nombre="Ajeno", fecha_inicio=date.today(), creado_por=self.user)
        self.assertEqual(self.client.get(reverse("masiscam:proyecto_detalle", args=[ajeno.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("accounts:seleccionar_empresa"), {"empresa_id": self.otra.pk}).status_code, 404)
        for tipo in RegistroEquipo.Tipo.values:
            with patch("masiscam.signals.encolar_carpeta_registro"):
                respuesta = self.client.post(reverse("masiscam:registro_crear", args=[equipo.pk]), {"tipo": tipo, "fecha": "2026-09-16", "clave_creacion": str(uuid.uuid4())})
            self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(equipo.registros.count(), 5)
        self.rol.rol = RolMasiscam.Rol.CONSULTA
        self.rol.save()
        self.assertEqual(self.client.get(reverse("masiscam:ficha_crear")).status_code, 403)
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_media_privada_no_se_expone_directamente(self):
        self.assertEqual(self.client.get("/media/masiscam/placas/secreto.png").status_code, 404)

    def test_documento_publico_controlado(self):
        from masiscam.forms import DocumentoForm
        self.login()
        equipo, _ = self.crear()
        proyecto = equipo.proyecto
        with tempfile.TemporaryDirectory() as directorio, override_settings(MEDIA_ROOT=directorio):
            archivo = SimpleUploadedFile("manual.pdf", b"%PDF-1.4 prueba", content_type="application/pdf")
            form = DocumentoForm({"titulo": "Manual", "categoria": "MANUALES", "equipo": equipo.pk, "publico": True}, {"archivo": archivo}, proyecto=proyecto)
            self.assertTrue(form.is_valid(), form.errors)
            documento = form.save(commit=False)
            documento.proyecto = proyecto
            documento.subido_por = self.user
            documento.estado_sincronizacion = Documento.Sincronizacion.SINCRONIZADO
            documento.save()
            url = reverse("masiscam:documento_publico", args=[proyecto.token_publico, documento.pk])
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 404)
            proyecto.pagina_publica_activa = True
            proyecto.save()
            respuesta = self.client.get(url)
            self.assertEqual(respuesta.status_code, 200)
            respuesta.close()
            documento.publico = False
            documento.save()
            self.assertEqual(self.client.get(url).status_code, 404)

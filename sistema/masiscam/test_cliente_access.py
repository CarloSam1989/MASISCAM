from datetime import date
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Empresa, Perfil

from . import views
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

    @override_settings(GOOGLE_DRIVE_ENABLED=False)
    def test_solo_activos_en_dashboard_contadores_busquedas_y_urls_cliente(self):
        ocultos = [Equipo.objects.create(
            proyecto=self.proyecto, cliente=self.cliente, nombre="OCULTO-" + estado,
            numero_serie="SER-" + estado, ratio="99:1", estado=estado,
        ) for estado in (Equipo.Estado.INACTIVO, Equipo.Estado.MANTENIMIENTO)]
        Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="BOMBA-INACTIVA",
                              tipo_producto="BOMBA", estado="INACTIVO")
        self.login_as(self.cliente_user)
        rutas = [reverse("masiscam:dashboard"), reverse("masiscam:cliente_productos"),
                 reverse("masiscam:producto_listado", args=["reductor"])]
        for ruta in rutas:
            response = self.client.get(ruta)
            self.assertEqual(list(response.context["equipos"]), [self.equipo])
            self.assertEqual({p["codigo"]: p["total"] for p in response.context["productos"]}, {"REDUCTOR": 1, "BOMBA": 0})
            self.assertEqual(re.findall(r"<th>(.*?)</th>", response.content.decode()), [
                "N.º equipo", "Camaronera", "Estación", "Sector", "Marca", "Modelo", "Serie", "Potencia", "Acciones",
            ])
            for query in ("OCULTO", "SER-INACTIVO", "99:1", self.equipo_ajeno.numero_serie):
                response = self.client.get(ruta, {"q": query})
                self.assertEqual(list(response.context["equipos"]), [])
            self.assertEqual(list(self.client.get(ruta, {"q": self.equipo.numero_serie}).context["equipos"]), [self.equipo])
        for equipo in ocultos + [self.equipo_ajeno]:
            for vista in ("ficha_detalle", "equipo_informe"):
                self.assertEqual(self.client.get(reverse("masiscam:" + vista, args=[equipo.pk])).status_code, 403)
        self.login_as(self.admin)
        response = self.client.get(reverse("masiscam:producto_listado", args=["reductor"]))
        for equipo in ocultos:
            self.assertContains(response, equipo.nombre)
            for vista in ("ficha_detalle", "equipo_informe"):
                self.assertEqual(self.client.get(reverse("masiscam:" + vista, args=[equipo.pk])).status_code, 200)
        self.assertEqual(response.context["inactivos"], 1)

    def test_tabla_reductores_columnas_acciones_y_busqueda(self):
        Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="BOMBA-EXCLUIDA", tipo_producto="BOMBA")
        self.login_as(self.cliente_user)
        url = reverse("masiscam:producto_listado", args=["reductor"])
        response = self.client.get(url, {"cliente": self.otro_cliente.pk})
        self.assertEqual(re.findall(r"<th>(.*?)</th>", response.content.decode()), [
            "N.º equipo", "Camaronera", "Estación", "Sector", "Marca", "Modelo", "Serie", "Potencia", "Acciones",
        ])
        self.assertContains(response, self.equipo.nombre)
        self.assertNotContains(response, self.equipo_ajeno.nombre)
        self.assertNotContains(response, "BOMBA-EXCLUIDA")
        self.assertContains(response, "table-responsive")
        for vista in ("ficha_detalle", "equipo_informe"):
            self.assertContains(response, reverse("masiscam:" + vista, args=[self.equipo.pk]))
        for texto in ("Editar", "Documentos", "Drive", "Descargar QR", "Imprimir etiqueta", "Administración"):
            self.assertNotContains(response, texto)
        self.assertContains(self.client.get(url, {"q": self.equipo.numero_serie}), self.equipo.nombre)
        self.assertNotContains(self.client.get(url, {"q": self.equipo_ajeno.numero_serie}), self.equipo_ajeno.nombre)
        self.assertNotContains(self.client.get(url, {"q": "no-coincide"}), self.equipo.nombre)

    @override_settings(GOOGLE_DRIVE_ENABLED=False)
    def test_token_visible_admin_cliente_y_qr_existente_univoco(self):
        import qrcode
        originales = {equipo.pk: equipo.token_publico for equipo in (self.equipo, self.equipo_ajeno)}
        for usuario in (self.admin, self.cliente_user):
            self.login_as(usuario)
            for vista in ("ficha_detalle", "equipo_informe"):
                response = self.client.get(reverse("masiscam:" + vista, args=[self.equipo.pk]))
                self.assertContains(response, f"<dt>ID del producto</dt><dd>{self.equipo.token_publico}</dd>", html=True)
        self.login_as(self.admin)
        for equipo in (self.equipo, self.equipo_ajeno):
            with patch("qrcode.make", wraps=qrcode.make) as make:
                response = self.client.get(reverse("masiscam:equipo_qr", args=[equipo.pk]))
                self.assertEqual(response.status_code, 200)
                make.assert_called_once_with(views._url_publica_equipo(equipo))
            Equipo.objects.filter(pk=equipo.pk).update(consulta_publica_activa=True)
            response = self.client.get(reverse("masiscam:equipo_publico", args=[equipo.token_publico]))
            self.assertEqual(response.context["equipo"].pk, equipo.pk)
            self.assertContains(response, f"<dt>ID del producto</dt><dd>{equipo.token_publico}</dd>", html=True)
            self.login_as(self.admin)
            response = self.client.post(reverse("masiscam:ficha_editar", args=[equipo.pk]), {
                "cliente": equipo.cliente_id, "camaronera": equipo.proyecto.nombre,
                "estacion": "E1", "sector": "S1", "numero_equipo": equipo.nombre,
                "marca": "Marca", "modelo": "EDITADO", "numero_serie": equipo.numero_serie,
                "potencia": "20 HP", "consulta_publica_activa": "on",
            })
            self.assertRedirects(response, reverse("masiscam:ficha_detalle", args=[equipo.pk]))
            equipo.refresh_from_db()
            self.assertEqual(equipo.modelo, "EDITADO")
            self.assertEqual(equipo.token_publico, originales[equipo.pk])
            response = self.client.get(reverse("masiscam:equipo_publico", args=[originales[equipo.pk]]))
            self.assertEqual(response.context["equipo"].pk, equipo.pk)
        self.assertNotEqual(self.equipo.token_publico, self.equipo_ajeno.token_publico)
        self.assertTrue(Equipo._meta.get_field("token_publico").unique)
        self.assertEqual(dict(Equipo.objects.filter(pk__in=originales).values_list("pk", "token_publico")), originales)
        self.login_as(self.cliente_user)
        self.assertEqual(self.client.get(reverse("masiscam:equipo_publico", args=[self.equipo.token_publico])).status_code, 200)
        self.assertEqual(self.client.get(reverse("masiscam:equipo_publico", args=[self.equipo_ajeno.token_publico])).status_code, 403)

    @override_settings(GOOGLE_DRIVE_ENABLED=False)
    def test_id_cambia_solo_con_regenerar_qr(self):
        self.login_as(self.admin)
        original = self.equipo.token_publico
        response = self.client.post(reverse("masiscam:equipo_publico_desactivar", args=[self.equipo.pk]))
        self.assertEqual(response.status_code, 302)
        self.equipo.refresh_from_db()
        self.assertFalse(self.equipo.consulta_publica_activa)
        self.assertEqual(self.equipo.token_publico, original)
        response = self.client.post(reverse("masiscam:equipo_token_regenerar", args=[self.equipo.pk]))
        self.assertEqual(response.status_code, 302)
        self.equipo.refresh_from_db()
        self.assertNotEqual(self.equipo.token_publico, original)
        self.assertEqual(self.client.get(reverse("masiscam:equipo_publico", args=[original])).status_code, 404)
        response = self.client.get(reverse("masiscam:equipo_publico", args=[self.equipo.token_publico]))
        self.assertEqual(response.context["equipo"].pk, self.equipo.pk)

    def test_crear_usuario_cliente_desde_cliente(self):
        self.login_as(self.admin)
        response = self.client.post(reverse("masiscam:cliente_usuario_crear", args=[self.otro_cliente.pk]))
        self.assertRedirects(response, reverse("masiscam:cliente_detalle", args=[self.otro_cliente.pk]))
        user = get_user_model().objects.get(username=self.otro_cliente.ruc)
        self.assertTrue(user.check_password(self.otro_cliente.ruc))
        self.assertEqual(user.perfiles.get().rol_masiscam.cliente_id, self.otro_cliente.pk)
        self.client.post(reverse("masiscam:cliente_usuario_crear", args=[self.otro_cliente.pk]))
        self.assertEqual(get_user_model().objects.filter(username=self.otro_cliente.ruc).count(), 1)

    def test_cliente_con_un_producto_mantiene_listado(self):
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:cliente_productos"))
        self.assertContains(response, self.equipo.nombre)

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

    @override_settings(MASISCAM_PUBLIC_BASE_URL="https://masiscam.com")
    def test_informe_interno_exige_login_y_qr_usa_token(self):
        destino = reverse("masiscam:equipo_informe", args=[self.equipo.pk])
        self.assertEqual(views._url_publica_equipo(self.equipo), "https://masiscam.com" + reverse("masiscam:equipo_publico", args=[self.equipo.token_publico]))
        response = self.client.get(destino)
        self.assertRedirects(response, reverse("accounts:login") + "?next=" + destino, fetch_redirect_response=False)
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.cliente_user.username, "password": "Cliente-Pass-123", "next": destino},
        )
        self.assertRedirects(response, destino, fetch_redirect_response=False)
        response = self.client.get(destino)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INFORME DE IDENTIFICACIÓN DEL EQUIPO")

    def test_usuario_interno_conserva_acceso(self):
        self.login_as(self.admin)
        response = self.client.get(reverse("masiscam:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reductores")

    def test_dashboard_cards_y_filtro_tipo_solo_propios(self):
        bomba = Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre="BOMBA-PROPIA", tipo_producto="BOMBA")
        self.login_as(self.cliente_user)
        response = self.client.get(reverse("masiscam:dashboard"))
        self.assertEqual({p["codigo"]: p["total"] for p in response.context["productos"]}, {"REDUCTOR": 1, "BOMBA": 1})
        self.assertContains(response, "masiscam-producto h-100")
        for tipo, esperado in [("bomba", bomba), ("reductor", self.equipo)]:
            response = self.client.get(reverse("masiscam:producto_listado", args=[tipo]), {"cliente": self.otro_cliente.pk})
            self.assertEqual(list(response.context["equipos"]), [esperado])
            self.assertNotContains(response, self.equipo_ajeno.nombre)

    def test_busqueda_numero_serie_ratio_sin_documentos(self):
        self.equipo.ratio = "37:1"
        self.equipo.save()
        self.login_as(self.cliente_user)
        for ruta in [reverse("masiscam:cliente_productos"), reverse("masiscam:producto_listado", args=["reductor"])]:
            for q in ["EQ-CLIENTE", "SER-CLIENTE", "37:1"]:
                response = self.client.get(ruta, {"q": q})
                self.assertEqual(list(response.context["equipos"]), [self.equipo])
                self.assertContains(response, "SER-CLIENTE")
                self.assertNotContains(response, "Documentos")
                self.assertContains(response, "Ver ficha")
                self.assertContains(response, "Ver informe")
            response = self.client.get(ruta, {"q": "EQ-AJENO"})
            self.assertEqual(list(response.context["equipos"]), [])

    def test_cliente_navegacion_sin_administracion(self):
        self.login_as(self.cliente_user)
        prohibidas = [reverse("masiscam:dashboard"), reverse("masiscam:clientes"), reverse("masiscam:proyectos"), reverse("masiscam:cliente_detalle", args=[self.cliente.pk])]
        for ruta in [reverse("masiscam:cliente_productos"), reverse("masiscam:dashboard"), reverse("masiscam:producto_listado", args=["reductor"]), reverse("masiscam:ficha_detalle", args=[self.equipo.pk])]:
            response = self.client.get(ruta)
            self.assertContains(response, "Mis productos")
            for prohibida in prohibidas:
                self.assertNotContains(response, 'href="' + prohibida + '"')
        for ruta in prohibidas[1:]:
            self.assertEqual(self.client.get(ruta).status_code, 403)
        self.assertEqual(self.client.post(reverse("masiscam:producto_listado", args=["reductor"])).status_code, 403)

    def test_carpeta_principal_solo_administrador(self):
        url = "https://drive.google.com/drive/folders/carpeta-principal"
        self.equipo.drive_folder_url = url
        self.equipo.save()
        for user, visible in [(self.admin, True), (self.cliente_user, False)]:
            self.login_as(user)
            for ruta in [reverse("masiscam:producto_listado", args=["reductor"]), reverse("masiscam:ficha_detalle", args=[self.equipo.pk])]:
                response = self.client.get(ruta)
                if visible:
                    self.assertContains(response, 'href="' + url + '"')
                else:
                    self.assertNotContains(response, url)

    @patch("masiscam.views.EquipoDriveDocuments")
    def test_registros_cliente_tabla_unica_ordenada(self, documentos):
        from .models import RegistroEquipo
        self.equipo.drive_folder_id = "equipo-root"
        self.equipo.save()
        documentos.return_value.list.return_value = []
        for indice, tipo in enumerate([*RegistroEquipo.Tipo.values, "REVISION"]):
            folder = f"registro-{indice}"
            RegistroEquipo.objects.create(equipo=self.equipo, tipo=tipo, fecha=date(2026, 9, indice + 1), drive_folder_id=folder)
            documentos.return_value.list.return_value.append({"id": f"file-{indice}", "name": f"archivo-{indice}.pdf", "folders": [folder]})
        self.login_as(self.cliente_user)
        for vista in ["ficha_detalle", "equipo_informe"]:
            response = self.client.get(reverse("masiscam:" + vista, args=[self.equipo.pk]))
            html = response.content.decode()
            self.assertEqual(html.count('class="table table-sm align-middle ficha-registros mb-0"'), 1)
            self.assertContains(response, '>Registros</h2>')
            for tipo in [*RegistroEquipo.Tipo.labels, "REVISION"]:
                self.assertContains(response, '<td class="registro-tipo" data-label="Tipo">' + tipo + " #001</td>")
            for dia in range(1, 6):
                self.assertLess(html.index(f"{dia:02}/09/2026"), html.index(f"{dia+1:02}/09/2026"))
            self.assertNotContains(response, "Reintentar Drive")

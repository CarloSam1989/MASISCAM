from django.core.exceptions import PermissionDenied, ValidationError
from django.template import Context
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from . import views
from .forms import FichaEquipoForm
from .models import Equipo, RolMasiscam
from .templatetags.masiscam_navigation import breadcrumbs
from .test_clientes import ClientesReductoresTests


class ProductosTests(TestCase):
    setUpTestData = classmethod(ClientesReductoresTests.setUpTestData.__func__)
    request = ClientesReductoresTests.request
    datos_reductor = ClientesReductoresTests.datos_reductor

    def guardar(self, **datos):
        form = FichaEquipoForm(self.datos_reductor(**datos), empresa=self.empresa)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save(usuario=self.usuario)

    def test_numero_empresa_estacion_y_edicion(self):
        equipo = self.guardar()
        self.guardar(estacion="E2", numero_serie="SER-2")
        form = FichaEquipoForm(self.datos_reductor(camaronera="Otra camaronera", numero_serie="SER-3", estacion=" e1 ", numero_equipo="red-1"), empresa=self.empresa)
        self.assertFalse(form.is_valid())
        self.assertIn("numero_equipo", form.errors)
        form = FichaEquipoForm(self.datos_reductor(), empresa=self.empresa, equipo=equipo)
        self.assertTrue(form.is_valid(), form.errors)
        form.save(usuario=self.usuario)
        form = FichaEquipoForm(self.datos_reductor(cliente=self.ajeno.pk), empresa=self.otra)
        self.assertTrue(form.is_valid(), form.errors)
        form.save(usuario=self.usuario)
        copia = Equipo.objects.get(pk=equipo.pk)
        copia.pk = None
        copia.numero_serie = "DISTINTA"
        with self.assertRaises(ValidationError) as error:
            copia.clean()
        self.assertIn("nombre", error.exception.message_dict)
        copia.ubicacion = "E3"
        copia.clean()

    def test_bomba_creacion_edicion_listados_permisos_y_qr(self):
        request = self.request("ficha_crear", self.datos_reductor(), post=True)
        request.GET = {"tipo": "BOMBA"}
        response = views.ficha_crear(request)
        self.assertEqual(response.status_code, 302)
        bomba = Equipo.objects.get(numero_serie="SER-1")
        self.assertEqual(bomba.tipo_producto, "BOMBA")
        self.guardar(numero_equipo="RED-2", numero_serie="RED-SER")
        for tipo, incluido, excluido in [("bomba", "SER-1", "RED-SER"), ("reductor", "RED-SER", "SER-1")]:
            url = reverse("masiscam:producto_listado", args=[tipo])
            request = self.request("dashboard")
            request.resolver_match = resolve(url)
            response = views.producto_listado(request, tipo)
            self.assertContains(response, incluido)
            self.assertNotContains(response, excluido)
        response = views.dashboard(self.request("dashboard"))
        self.assertContains(response, 'href="' + reverse("masiscam:producto_listado", args=["bomba"]) + '"')
        self.assertContains(response, "Bombas")
        response = views.ficha_editar(self.request("ficha_editar", self.datos_reductor(modelo="EDITADO"), pk=bomba.pk, post=True), bomba.pk)
        self.assertEqual(response.status_code, 302)
        bomba.refresh_from_db()
        self.assertEqual(bomba.tipo_producto, "BOMBA")
        for response in [views.ficha_detalle(self.request("ficha_detalle", pk=bomba.pk), bomba.pk),
                         views.equipo_publico(self.request("equipo_publico", pk=bomba.token_publico), bomba.token_publico)]:
            self.assertContains(response, "DE LA BOMBA")
            self.assertNotContains(response, "DEL REDUCTOR")
        response = views.equipo_qr(self.request("equipo_qr", pk=bomba.pk), bomba.pk)
        self.assertEqual(response["Content-Type"], "image/png")
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="CONSULTA")
        with self.assertRaises(PermissionDenied):
            views.ficha_editar(self.request("ficha_editar", pk=bomba.pk), bomba.pk)

    def test_breadcrumbs_ficha_edicion_informe_registros_y_creacion(self):
        equipo = self.guardar()
        expected = ["Dashboard", "Clientes", self.cliente.nombre_comercial, "Reductor", equipo.numero_serie]
        for vista, extra in [("ficha_detalle", []), ("ficha_editar", ["Editar"]), ("equipo_publico", ["Informe"]), ("registro_crear", ["Registros"]), ("equipo_etiqueta", ["Etiqueta QR"])]:
            request = self.request(vista, pk=equipo.token_publico if vista == "equipo_publico" else equipo.pk)
            items = breadcrumbs(Context({"request": request, "equipo": equipo}))
            self.assertEqual([item["label"] for item in items], expected + extra)
            self.assertTrue(all(item["url"] for item in items[:-1]))
            self.assertIsNone(items[-1]["url"])
        request = self.request("ficha_crear", {"tipo": "BOMBA", "cliente": self.cliente.pk})
        response = views.ficha_crear(request)
        self.assertContains(response, "Crear bomba")
        self.assertContains(response, self.cliente.nombre_comercial)
        self.assertContains(response, 'aria-label="Migas de pan"', count=1)
        self.assertContains(response, '?cliente=' + str(self.cliente.pk))
        request = self.request("ficha_crear", {"tipo": "INVALIDO"})
        from django.http import Http404
        with self.assertRaises(Http404):
            views.ficha_crear(request)

    def test_creacion_modal_ambos_tipos_y_reintento_con_errores(self):
        import json
        for tipo in ("REDUCTOR", "BOMBA"):
            request = self.request("dashboard")
            response = views.producto_listado(request, tipo.lower())
            self.assertContains(response, 'data-modal-form data-modal-size="wide"')
            datos = self.datos_reductor(numero_equipo=tipo, numero_serie=tipo)
            request = self.request("ficha_crear", dict(datos, numero_serie=""), post=True)
            request.GET = {"tipo": tipo}
            request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
            response = views.ficha_crear(request)
            self.assertEqual(response.status_code, 400)
            error = json.loads(response.content)
            self.assertFalse(error["success"])
            self.assertIn('id="reductor-form"', error["html"])
            request.POST = datos
            response = views.ficha_crear(request)
            self.assertEqual(response.status_code, 200)
            creado = json.loads(response.content)
            self.assertTrue(creado["success"])
            self.assertEqual(Equipo.objects.get(pk=creado["id"]).tipo_producto, tipo)

    def test_list_state_actions_preserve_all_other_equipment_data(self):
        self.client.force_login(self.usuario)
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="ADMIN")
        for tipo in ("REDUCTOR", "BOMBA"):
            Equipo.objects.filter(pk=self.antiguo.pk).update(tipo_producto=tipo)
            url = reverse("masiscam:producto_listado", args=[tipo.lower()])
            for estado in ("INACTIVO", "ACTIVO"):
                before = Equipo.objects.values().get(pk=self.antiguo.pk)
                response = self.client.post(url, {"equipo_id": self.antiguo.pk, "estado": estado})
                self.assertRedirects(response, url)
                after = Equipo.objects.values().get(pk=self.antiguo.pk)
                self.assertEqual(after.pop("estado"), estado)
                before.pop("estado")
                self.assertEqual(before, after)
            response = self.client.get(url)
            self.assertTemplateUsed(response, "masiscam/producto_listado.html")
            self.assertNotContains(response, "Empresa:")
            self.assertContains(response, "Migas de pan", count=1)
            self.assertContains(response, "return confirm(")
            self.assertContains(response, "Desactivar")
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="CONSULTA")
        self.assertEqual(self.client.post(url, {"equipo_id": self.antiguo.pk, "estado": "INACTIVO"}).status_code, 403)
        self.assertNotContains(self.client.get(url), "Desactivar</button>")

    def test_state_action_scoped_to_company_and_product_and_csrf(self):
        from django.test import Client
        self.client.force_login(self.usuario)
        RolMasiscam.objects.filter(perfil__user=self.usuario).update(rol="ADMIN")
        url = reverse("masiscam:producto_listado", args=["bomba"])
        self.assertEqual(self.client.post(url, {"equipo_id": self.antiguo.pk, "estado": "INACTIVO"}).status_code, 404)
        url = reverse("masiscam:producto_listado", args=["reductor"])
        self.assertEqual(self.client.post(url, {"equipo_id": self.antiguo.pk, "estado": "INVALID"}).status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.usuario)
        self.assertEqual(csrf_client.post(url, {"equipo_id": self.antiguo.pk, "estado": "INACTIVO"}).status_code, 403)
        self.proyecto.empresa = self.otra
        self.proyecto.save(update_fields=["empresa"])
        self.assertEqual(self.client.post(url, {"equipo_id": self.antiguo.pk, "estado": "INACTIVO"}).status_code, 404)

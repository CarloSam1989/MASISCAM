import re
import uuid

from django.test import TestCase, override_settings
from django.urls import reverse

from . import test_plano as qa, tecnica_services as svc
from .models import Auditoria, Equipo, ParteReferenciaTecnica, ReferenciaTecnica
from .test_tecnica import pdf_plano

QUITADOS = ('Compras', 'Ventas', 'Guías de envío', 'Documentación comercial autorizada', 'Mi cuenta')
CONSERVADOS = ('Inicio', 'Productos', 'Clientes', 'Solicitudes de taller', 'Cotizaciones', 'Planos y piezas')


def menu(response):
    html = response.content.decode()
    nav = re.search(r'<nav id="appMenu".*?</nav>', html, re.S).group(0)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '|', nav))


@override_settings(**qa.FLAGS)
class PlanosOficinaTests(TestCase):
    def setUp(self):
        qa.fixture(self)

    def entrar(self, user):
        self.client.force_login(user)
        return self.client

    def test_menu_oficina_oculto_sin_tocar_rutas_ni_otros_roles(self):
        r = self.entrar(self.oficina).get(reverse('masiscam:oficina_inicio'))
        texto = menu(r)
        for label in CONSERVADOS:
            self.assertIn(label, texto)
        for label in QUITADOS:
            self.assertNotIn(label, texto)
        self.assertEqual(self.client.get(reverse('masiscam:oficina_seccion', args=['compras'])).status_code, 200)
        admin = menu(self.entrar(self.planta).get(reverse('masiscam:dashboard')))
        self.assertIn('Aprobaciones', admin)
        taller = menu(self.entrar(self.tecnico).get(reverse('masiscam:taller_inicio')))
        self.assertIn('Mi cuenta', taller)

    def test_flujo_oficina_administrador_taller(self):
        c = self.entrar(self.oficina)
        # Nuevo catálogo desde OFICINA: solo PDF, Excel y configuración (sin proyecto ni CSV).
        r = c.post(reverse('masiscam:plano_importar'), dict(archivo=pdf_plano(), marca='QAMARCA', modelo='QM-1',
            ratio='5:1', rotacion='#2', operacion='VERTICAL', clave=str(uuid.uuid4()),
            excel=xlsx([('1', '2', 'QA-1', 'Eje QA'), ('88', '', 'C88', '')])))
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        ref = ReferenciaTecnica.objects.get(marca='QAMARCA', modelo='QM-1')
        self.assertEqual((ref.estado, ref.ratio, ref.rotacion, ref.operacion), ('BORRADOR', '5:1', '#2', 'VERTICAL'))
        self.assertEqual(ref.documento.proyecto.codigo, 'BIBLIOTECA-TECNICA')
        self.assertNotContains(self.entrar(self.planta).get(reverse('masiscam:proyectos')), 'BIBLIOTECA-TECNICA')
        c = self.entrar(self.oficina)
        # Otro intento con la misma configuración no duplica el catálogo.
        r = c.post(reverse('masiscam:plano_importar'), dict(archivo=pdf_plano(), marca='qamarca', modelo='qm-1',
            ratio='5:1', rotacion='#2', operacion='vertical', clave=str(uuid.uuid4())))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ReferenciaTecnica.objects.filter(marca__iexact='qamarca').count(), 1)
        # Excel con posición 88 sin descripción: queda en edición (no se inventa la descripción).
        self.assertEqual(ref.partes.count(), 2)
        p88 = ref.partes.get(posicion='88')
        self.assertEqual((p88.descripcion, p88.numero_parte), ('', 'C88'))
        url = reverse('masiscam:biblioteca_referencia', args=[ref.pk])
        self.assertContains(c.get(url), 'Pendiente de corrección')
        self.assertEqual(c.post(url, dict(op='PUBLICADA', clave=str(uuid.uuid4()))).status_code, 400)
        self.assertNotIn('Enviar a revisión', c.get(reverse('masiscam:biblioteca')).content.decode())
        # Tabla editable: OFICINA corrige la posición 88 y activa sin aprobación de ADMINISTRADOR.
        r = c.post(url, dict(op='parte', parte_id=p88.pk, posicion='88', numero_parte='', descripcion='Tuerca QA',
            unidad='', cantidad_referencia='', pagina='', clave=str(uuid.uuid4())))
        self.assertEqual(r.status_code, 302)
        p88.refresh_from_db()
        self.assertEqual(p88.descripcion, 'Tuerca QA')
        cambio = Auditoria.objects.get(accion='BIBLIOTECA_PARTE_CAMBIO', objeto_id=str(p88.pk), detalle__campo='descripcion')
        self.assertEqual((cambio.usuario_id, cambio.detalle['anterior'], cambio.detalle['nuevo'], cambio.detalle['posicion']),
                         (self.oficina.pk, '', 'Tuerca QA', '88'))
        self.assertTrue(cambio.creado_en)
        self.assertEqual(c.get(reverse('masiscam:plano', args=[ref.pk])).status_code, 200)
        lista = c.get(reverse('masiscam:biblioteca')).content.decode()
        self.assertIn('QAMARCA QM-1', lista)
        self.assertIn('VERTICAL · #2 · 5:1', lista)
        self.assertIn('En edición', lista)
        self.assertEqual(c.post(url, dict(op='PUBLICADA', clave=str(uuid.uuid4()))).status_code, 302)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, 'PUBLICADA')
        self.assertIn('Activo', c.get(reverse('masiscam:biblioteca')).content.decode())
        # OFICINA no desactiva; ADMINISTRADOR sí y consulta el historial.
        self.assertEqual(c.post(url, dict(op='RETIRADA', clave=str(uuid.uuid4()))).status_code, 403)
        a = self.entrar(self.planta)
        self.assertContains(a.get(url), 'Historial de modificaciones')
        self.assertContains(a.get(url), '«Tuerca QA»')
        # TALLER: coincidencia exacta; aplicable de inmediato, sin aprobación.
        equipo = self.exp.registro.equipo
        Equipo.objects.filter(pk=equipo.pk).update(tipo_producto='REDUCTOR', marca='qamarca', modelo='QM-1',
            operacion='vertical', rotacion='#2', ratio='5:1')
        equipo.refresh_from_db()
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        candidatos = [x for x in res['candidatos'] if x.pk == ref.pk]
        self.assertEqual(len(candidatos), 1)
        self.assertTrue(candidatos[0].aprobada)
        self.assertEqual(ref.aplicabilidades.filter(equipo=equipo, activa=True).count(), 1)
        ap = ref.aplicabilidades.get(equipo=equipo)
        self.assertEqual((ap.autor_id, 'automática' in ap.fundamento), (self.tecnico.pk, True))
        self.assertTrue(Auditoria.objects.filter(accion='BIBLIOTECA_APLICABILIDAD_AUTOMATICA', objeto_id=str(ap.pk), usuario=self.tecnico).exists())
        # Editar un catálogo activo crea una revisión en edición; el activo no cambia hasta activarla.
        r = c.post(url, dict(op='revision', clave=str(uuid.uuid4())))
        self.assertEqual(r.status_code, 302)
        rev = ReferenciaTecnica.objects.get(codigo=ref.codigo, revision=2)
        self.assertEqual((rev.estado, rev.documento_id, rev.partes.count()), ('BORRADOR', ref.documento_id, 2))
        ref.refresh_from_db()
        self.assertEqual(ref.estado, 'PUBLICADA')
        # ADMINISTRADOR desactiva: TALLER ya no lo encuentra.
        a.post(reverse('masiscam:biblioteca_referencia', args=[rev.pk]), dict(op='PUBLICADA', clave=str(uuid.uuid4())))
        rev.refresh_from_db()
        self.assertEqual(rev.estado, 'PUBLICADA')
        self.assertEqual(a.post(reverse('masiscam:biblioteca_referencia', args=[rev.pk]), dict(op='RETIRADA', clave=str(uuid.uuid4()))).status_code, 302)
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertEqual([x.pk for x in res['candidatos'] if x.pk in (ref.pk, rev.pk)], [])
        Equipo.objects.filter(pk=equipo.pk).update(ratio='3:1')
        equipo.refresh_from_db()
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertFalse([x for x in res['candidatos'] if x.pk == ref.pk])


def xlsx(filas):
    """Excel mínimo con las columnas PARTE #, UNITS, CODIGO, DESCRIPCION y CHECK."""
    import io
    import zipfile
    from xml.sax.saxutils import escape
    from django.core.files.uploadedfile import SimpleUploadedFile

    def fila(n, valores):
        celdas = ''.join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % ('ABCDE'[i], n, escape(str(v)))
                         for i, v in enumerate(valores))
        return '<row r="%d">%s</row>' % (n, celdas)
    datos = [('PARTE #', 'UNITS', 'CODIGO', 'DESCRIPCION', 'CHECK')] + [tuple(f) + ('',) * (5 - len(f)) for f in filas]
    hoja = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>'
            % ''.join(fila(i + 1, f) for i, f in enumerate(datos)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as libro:
        libro.writestr('xl/worksheets/sheet1.xml', hoja)
    return SimpleUploadedFile('repuestos.xlsx', buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@override_settings(**qa.FLAGS)
class ImportarRepuestosCatalogoTests(TestCase):
    def setUp(self):
        qa.fixture(self)
        from . import plano_services as ps
        self.ps = ps
        self.ref = svc.guardar_referencia(usuario=self.oficina, empresa=self.empresa, clave=uuid.uuid4(), archivo=pdf_plano(),
            datos=dict(codigo='CAT-IMP', tipo='PLANO', titulo='Catálogo importación', marca='IMPMARCA', modelo='IM-1',
                operacion='VERTICAL', rotacion='#1', ratio='3:1', version_fabricante='V1', procedencia='QA',
                autorizacion_uso='QA', proyecto_id=self.exp.registro.equipo.proyecto_id))
        self.partes = {}
        for pos, desc, code in (('1', 'Eje', 'A1'), ('2', 'Rodamiento', 'B2'), ('88', '', '')):
            pk = svc.guardar_parte(usuario=self.oficina, empresa=self.empresa, referencia_id=self.ref.pk, clave=uuid.uuid4(),
                datos=dict(posicion=pos, descripcion=desc, numero_parte=code))
            self.partes[pos] = pk
        self.mapa = ps.guardar_mapa(usuario=self.oficina, empresa=self.empresa, referencia_id=self.ref.pk, base_version=0,
            clave=uuid.uuid4(), posiciones=[dict(parte=self.partes['1'], pagina=1, x='0.25', y='0.75')])
        self.url = reverse('masiscam:repuestos_importar', args=[self.ref.pk])
        self.client.force_login(self.oficina)

    def previsualizar(self, filas):
        return self.client.post(self.url, dict(op='previsualizar', excel=xlsx(filas)))

    def aplicar(self, r):
        vista = r.context['vista']
        return self.client.post(self.url, dict(op='aplicar', huella=vista['huella'], clave=str(r.context['clave']),
            materiales=r.context['materiales'].replace('\n', '\r\n')))  # como lo envía un navegador

    def test_vista_previa_borrador_sin_duplicar_catalogo_ni_pdf(self):
        from .models import Documento
        filas = [('1', '', '', 'Eje'),                         # igual (código vacío no borra A1)
                 ('2', '4', 'B2', 'Rodamiento reforzado'),     # modificada por posición
                 ('88', '', 'C88', 'Tuerca'),                  # completa la fila incompleta
                 ('89', '3', 'D9', 'Retén nuevo')]             # nueva
        r = self.previsualizar(filas)
        self.assertEqual(r.status_code, 200, r.context.get('error'))
        self.assertEqual(r.context['vista']['conteos'], dict(nuevos=1, existentes=1, modificados=2, errores=0, ausentes=0))
        self.assertContains(r, 'Registros modificados')
        # La vista previa no escribe nada.
        self.assertEqual(self.ref.partes.count(), 3)
        refs, docs = ReferenciaTecnica.objects.count(), Documento.objects.count()
        r2 = self.aplicar(r)
        self.assertEqual(r2.status_code, 302)
        self.assertEqual((ReferenciaTecnica.objects.count(), Documento.objects.count()), (refs, docs))
        p = {x.posicion: x for x in self.ref.partes.all()}
        self.assertEqual({k: x.pk for k, x in p.items() if k in self.partes}, self.partes)  # mismos identificadores
        self.assertEqual((p['1'].numero_parte, p['1'].descripcion), ('A1', 'Eje'))
        self.assertEqual((p['2'].descripcion, str(p['2'].cantidad_referencia)), ('Rodamiento reforzado', '4.000000'))
        self.assertEqual((p['88'].numero_parte, p['88'].descripcion), ('C88', 'Tuerca'))
        self.assertEqual(p['89'].descripcion, 'Retén nuevo')
        self.assertEqual(self.ref.mapas.get().posiciones.get().parte_id, self.partes['1'])  # marcador conservado
        self.assertEqual(Auditoria.objects.filter(accion='BIBLIOTECA_REPUESTOS_IMPORTADOS').count(), 1)
        # El mismo archivo ya no tiene cambios; la huella anterior no se puede reutilizar.
        r3 = self.previsualizar(filas)
        c = r3.context['vista']['conteos']
        self.assertEqual(c['nuevos'] + c['modificados'], 0)
        self.assertEqual(self.aplicar(r).status_code, 400)

    def test_errores_y_estado_cambiado_bloquean(self):
        r = self.previsualizar([('1', '', 'A1', 'Eje'), ('1', '', 'A1', 'Eje repetido')])
        # Una posición repetida invalida el archivo completo: no hay vista previa aplicable.
        self.assertEqual(r.status_code, 400)
        self.assertIn('Fila 3', r.context['error'])
        self.assertIsNone(r.context['vista'])
        self.assertNotContains(r, 'Aplicar al borrador', status_code=400)
        r = self.previsualizar([('90', '1', 'N90', 'Pieza')])
        svc.guardar_parte(usuario=self.oficina, empresa=self.empresa, referencia_id=self.ref.pk, clave=uuid.uuid4(),
            datos=dict(posicion='91', descripcion='Cambio concurrente'))
        self.assertEqual(self.aplicar(r).status_code, 400)
        self.assertFalse(self.ref.partes.filter(posicion='90').exists())
        self.client.force_login(self.tecnico)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_autoguardado_decimales_validacion_auditoria_y_catalogo_congelado(self):
        from decimal import Decimal
        from .campos import normalizar_decimal
        for texto, esperado in (('2,5', '2.5'), ('1.250', '1.250'), ('1.234,50', '1234.50'), ('1,234.50', '1234.50'),
                                ('1,000000', '1.000000'), (' 3 ', '3'), ('1,234,567', '1234567'), ('-2,5', '-2.5'),
                                ('1.234.567,89', '1234567.89'), ('1,234', '1.234'),  # un solo separador es decimal
                                ('1,2,3', '1,2,3'), ('1.2.3', '1.2.3'), ('12,34,56', '12,34,56'), ('1,23.4', '1,23.4'),
                                ('abc', 'abc'), ('', ''), ('--1', '--1')):
            self.assertEqual(normalizar_decimal(texto), esperado, texto)
        from django import forms as _f
        from .campos import DecimalFlexible
        campo = DecimalFlexible(max_digits=20, decimal_places=6, min_value=0, required=False)
        self.assertEqual(campo.clean('1.234,5'), Decimal('1234.5'))
        for malo in ('1,2,3', 'NaN', 'Infinity', 'abc', '-1', '1.2.3', '1,23.4'):
            with self.assertRaises(_f.ValidationError, msg=malo):
                campo.clean(malo)
        url = reverse('masiscam:biblioteca_referencia', args=[self.ref.pk])
        ajax = dict(HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        def guardar(parte, **cambios):
            datos = dict(op='parte', parte_id=parte or '', clave=str(uuid.uuid4()), posicion='1', numero_parte='A1',
                         descripcion='Eje', unidad='', cantidad_referencia='', pagina='')
            datos.update(cambios)
            return self.client.post(url, datos, **ajax)

        pk = self.partes['1']
        eventos = lambda: Auditoria.objects.filter(accion='BIBLIOTECA_PARTE_CAMBIO', objeto_id=str(pk), detalle__campo='cantidad_referencia')
        for texto, valor in (('2,5', '2.5'), ('1.250', '1.25'), ('1.234,50', '1234.5')):
            r = guardar(pk, cantidad_referencia=texto)
            self.assertEqual((r.status_code, r.json()['ok'], r.json()['parte_id']), (200, True, pk), texto)
            self.assertEqual(ParteReferenciaTecnica.objects.get(pk=pk).cantidad_referencia, Decimal(valor))
        self.assertEqual(eventos().count(), 3)
        ultimo = eventos().order_by('-pk').first()
        self.assertEqual((ultimo.usuario_id, ultimo.detalle['campo'], ultimo.detalle['nuevo'], ultimo.detalle['posicion']),
                         (self.oficina.pk, 'cantidad_referencia', '1234.50', '1'))
        self.assertEqual(guardar(pk, cantidad_referencia='1234,5').status_code, 200)  # mismo valor: sin evento nuevo
        self.assertEqual(guardar(pk, cantidad_referencia='-1').status_code, 400)  # no admite negativas
        # Guardado parcial: una pantalla desactualizada no pisa lo que otro cambió después.
        ParteReferenciaTecnica.objects.filter(pk=pk).update(unidad='kg')  # cambio hecho en otra pestaña
        r = guardar(pk, pagina='5', unidad='', campos='pagina')
        self.assertEqual((r.status_code, r.json()['valores']['unidad'], r.json()['valores']['pagina']), (200, 'kg', '5'))
        self.assertEqual(ParteReferenciaTecnica.objects.get(pk=pk).unidad, 'kg')
        ParteReferenciaTecnica.objects.filter(pk=pk).update(unidad='')
        # Un reintento con la misma clave (respuesta perdida) no duplica ni repite eventos.
        clave = str(uuid.uuid4())
        for _ in range(2):
            self.assertEqual(guardar(None, posicion='77', descripcion='Reintento', clave=clave).status_code, 200)
        self.assertEqual(ParteReferenciaTecnica.objects.filter(referencia=self.ref, posicion='77').count(), 1)
        self.assertEqual(eventos().count(), 3)
        # El formulario muestra el decimal con punto, nunca «1,000000».
        self.client.force_login(self.oficina)
        self.assertNotRegex(self.client.get(url).content.decode(), r'value="\d+,\d+"')
        # Validación: no confirma nada y las demás piezas siguen editables.
        r = guardar(pk, descripcion='')
        self.assertEqual((r.status_code, r.json()['ok'], 'descripcion' in r.json()['errores']), (400, False, True))
        r = guardar(pk, cantidad_referencia='abc')
        self.assertEqual((r.status_code, 'cantidad_referencia' in r.json()['errores']), (400, True))
        self.assertEqual(ParteReferenciaTecnica.objects.get(pk=pk).descripcion, 'Eje')
        r = guardar(self.partes['88'], posicion='88', numero_parte='', descripcion='Tuerca QA')
        self.assertEqual(r.status_code, 200)
        r = guardar(None, posicion='9', numero_parte='N9', descripcion='Pieza nueva', cantidad_referencia='4,25')
        self.assertEqual(r.status_code, 200)
        nueva = ParteReferenciaTecnica.objects.get(pk=r.json()['parte_id'])
        self.assertEqual((nueva.posicion, nueva.cantidad_referencia), ('9', Decimal('4.25')))
        self.assertTrue(Auditoria.objects.filter(accion='BIBLIOTECA_PARTE_CAMBIO', objeto_id=str(nueva.pk), detalle__campo='descripcion').exists())
        self.assertEqual(guardar(None, posicion='9', descripcion='Duplicada').status_code, 400)  # posición repetida
        # TALLER no edita el catálogo; un catálogo activo (congelado) no se modifica directamente.
        self.client.force_login(self.tecnico)
        self.assertEqual(guardar(pk, descripcion='Intruso').status_code, 403)
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.post(url, dict(op='PUBLICADA', clave=str(uuid.uuid4()))).status_code, 302)
        self.assertEqual(guardar(pk, descripcion='Congelado').status_code, 403)
        self.assertEqual(ParteReferenciaTecnica.objects.get(pk=pk).descripcion, 'Eje')

    def test_url_anterior_redirige_al_nuevo_catalogo(self):
        r = self.client.get('/app/biblioteca-tecnica/importar-m20/?x=1')
        self.assertEqual((r.status_code, r['Location']), (301, reverse('masiscam:plano_importar') + '?x=1'))
        self.assertEqual(self.client.get(reverse('masiscam:plano_importar')).status_code, 200)
        self.assertTrue(reverse('masiscam:plano_importar').endswith('/nuevo-catalogo/'))

    def test_editar_borrador_sin_proyecto_conserva_piezas_marcadores_y_documento(self):
        from .models import Documento
        url = reverse('masiscam:biblioteca_referencia_editar', args=[self.ref.pk])
        r = self.client.get(url)
        self.assertNotContains(r, 'proyecto_id')
        docs, doc = Documento.objects.count(), self.ref.documento_id
        datos = dict(clave=str(uuid.uuid4()), codigo='CAT-IMP', tipo='PLANO', titulo='Catálogo editado', marca='IMPMARCA',
            modelo='IM-1', operacion='VERTICAL', rotacion='#1', ratio='4:1', version_fabricante='V1',
            procedencia='QA', autorizacion_uso='QA', documento_id=doc)
        self.assertEqual(self.client.post(url, dict(datos, ratio='')).status_code, 400)  # ratio obligatorio en planos
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.ref.refresh_from_db()
        self.assertEqual((self.ref.ratio, self.ref.titulo, self.ref.documento_id), ('4:1', 'Catálogo editado', doc))
        self.assertEqual(self.ref.partes.count(), 3)
        self.assertEqual(self.ref.mapas.get().posiciones.count(), 1)
        self.assertEqual(Documento.objects.count(), docs)

    def test_catalogo_publicado_no_se_sobrescribe_crea_revision_con_marcadores(self):
        from .models import Documento
        svc.guardar_parte(usuario=self.oficina, empresa=self.empresa, referencia_id=self.ref.pk, clave=uuid.uuid4(),
            datos=dict(posicion='88', descripcion='Tuerca corregida', numero_parte=''), parte_id=self.partes['88'])
        self.client.force_login(self.planta)
        self.assertEqual(self.client.post(reverse('masiscam:biblioteca_referencia', args=[self.ref.pk]),
            dict(op='PUBLICADA', clave=str(uuid.uuid4()))).status_code, 302)
        self.client.force_login(self.oficina)
        docs = Documento.objects.count()
        r = self.previsualizar([('2', '5', 'B2X', 'Rodamiento v2'), ('95', '1', 'N95', 'Nueva v2')])
        self.assertContains(r, 'nueva revisión')
        r2 = self.aplicar(r)
        self.assertEqual(r2.status_code, 302)
        self.ref.refresh_from_db()
        nueva = ReferenciaTecnica.objects.get(codigo='CAT-IMP', revision=2)
        self.assertEqual((self.ref.estado, nueva.estado, nueva.sustituye_id, nueva.documento_id),
                         ('PUBLICADA', 'BORRADOR', self.ref.pk, self.ref.documento_id))
        self.assertEqual(Documento.objects.count(), docs)
        self.assertEqual(self.ref.partes.get(posicion='2').descripcion, 'Rodamiento')
        self.assertFalse(self.ref.partes.filter(posicion='95').exists())
        self.assertEqual(nueva.partes.get(posicion='2').descripcion, 'Rodamiento v2')
        marca = nueva.mapas.get().posiciones.get()
        self.assertEqual((marca.parte.posicion, float(marca.x)), ('1', 0.25))
        self.assertEqual(self.ref.mapas.order_by('-version').first().posiciones.get().parte_id, self.partes['1'])
        # OFICINA activa la nueva revisión sin aprobación; la anterior queda superada.
        url = reverse('masiscam:biblioteca_referencia', args=[nueva.pk])
        self.assertEqual(self.client.post(url, dict(op='PUBLICADA', clave=str(uuid.uuid4()))).status_code, 302)
        nueva.refresh_from_db()
        self.assertEqual(nueva.estado, 'PUBLICADA')


@override_settings(**qa.FLAGS)
class RegistroReductorCatalogoTests(TestCase):
    def setUp(self):
        qa.fixture(self)
        self.cliente = self.exp.cliente_contractual
        # Catálogo publicado sin operación + otro con operación.
        self.refs = {}
        for operacion in ('', 'VERTICAL'):
            c = self.client
            c.force_login(self.oficina)
            r = c.post(reverse('masiscam:plano_importar'), dict(archivo=pdf_plano(), marca='QAMARCA', modelo='QM-1',
                ratio='5:1', rotacion='#2', operacion=operacion, clave=str(uuid.uuid4()),
                excel=xlsx([('1', '2', 'QA-1', 'Eje QA')])))
            self.assertEqual(r.status_code, 302)
            ref = ReferenciaTecnica.objects.get(marca='QAMARCA', operacion=operacion)
            self.assertEqual(ref.estado, 'PUBLICADA')  # se activa al guardar, sin aprobación
            self.refs[operacion] = ref
        Equipo.objects.filter(pk=self.exp.registro.equipo_id).update(registrado_por=self.tecnico)

    def registrar(self, **config):
        datos = dict(cliente=self.cliente.pk, camaronera='Camaronera QA', estacion='E-9', sector='S-9',
            numero_equipo=config.pop('numero', 'NUEVO-1'), numero_serie=config.pop('serie', 'SERIE-NUEVA-1'),
            potencia='10 HP', marca='', modelo='', ratio='', rotacion='', operacion='')
        datos.update(config)
        self.client.force_login(self.tecnico)
        return self.client.post(reverse('masiscam:ficha_crear'), datos)

    def test_taller_elige_configuracion_del_catalogo_y_encuentra_plano(self):
        import json
        self.client.force_login(self.tecnico)
        r = self.client.get(reverse('masiscam:ficha_crear'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('id_fabricante', r.content.decode())
        opciones = json.loads(r.context['form'].catalogo_json)
        self.assertEqual({(o['marca'], o['modelo'], o['ratio'], o['rotacion'], o['operacion']) for o in opciones},
                         {('QAMARCA', 'QM-1', '5:1', '#2', ''), ('QAMARCA', 'QM-1', '5:1', '#2', 'VERTICAL'),
                          ('DERAN', 'M20A', '3:1', '#1', 'VERTICAL')})  # este último, del fixture compartido
        self.assertContains(r, 'data-catalogo=')
        self.assertContains(r, '<option value="QAMARCA">')
        # Sin operación: coincide el catálogo sin operación; con operación, el que la define.
        r = self.registrar(marca='QAMARCA', modelo='QM-1', ratio='5:1', rotacion='#2')
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        equipo = Equipo.objects.get(numero_serie='SERIE-NUEVA-1')
        self.assertEqual(list(ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo).values_list('pk', flat=True)),
                         [self.refs[''].pk])  # se vincula al registrar, sin abrir la ficha
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertEqual([c.pk for c in res['candidatos']], [self.refs[''].pk])
        self.assertTrue(res['candidatos'][0].aprobada)  # aplicable de inmediato
        r = self.registrar(marca='qamarca', modelo='qm-1', ratio='5:1', rotacion='#2', operacion='vertical',
                           numero='NUEVO-2', serie='SERIE-NUEVA-2')
        self.assertEqual(r.status_code, 302)
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa,
                                            equipo=Equipo.objects.get(numero_serie='SERIE-NUEVA-2'))
        self.assertEqual([c.pk for c in res['candidatos']], [self.refs['VERTICAL'].pk])
        # ADMINISTRADOR puede excluir el plano para un equipo concreto; el catálogo inactivo no se ofrece.
        svc.aplicar_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=self.refs[''].pk,
            equipo_id=equipo.pk, fundamento='Exclusión QA', clave=uuid.uuid4(), activa=False)
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertFalse(res['candidatos'][0].aprobada)
        svc.estado_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=self.refs[''].pk, estado='RETIRADA', clave=uuid.uuid4())
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertEqual(res['candidatos'], [])

    def test_configuracion_sin_catalogo_registra_sin_plano_incorrecto(self):
        r = self.registrar(marca='OTRAMARCA', modelo='XX-9', ratio='7:1', rotacion='#1', numero='NUEVO-3', serie='SERIE-3', sin_catalogo='on')
        self.assertEqual(r.status_code, 302)
        equipo = Equipo.objects.get(numero_serie='SERIE-3')
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertEqual(res['candidatos'], [])
        # Configuración incompleta: no se adivina ningún plano.
        r = self.registrar(marca='QAMARCA', modelo='QM-1', numero='NUEVO-4', serie='SERIE-4', sin_catalogo='on')
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa,
                                            equipo=Equipo.objects.get(numero_serie='SERIE-4'))
        self.assertEqual((res['candidatos'], res['faltantes']), ([], ['rotacion', 'relacion de transmision']))

    def test_validacion_servidor_vista_previa_inactivos_y_edicion_conserva_configuracion(self):
        from .forms import FichaEquipoForm
        # Una configuración que no existe en catálogos activos se rechaza, salvo que se registre sin plano.
        r = self.registrar(marca='QAMARCA', modelo='OTRO-MODELO', ratio='5:1', rotacion='#2', numero='V-1', serie='V-1')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'catálogo activo')
        self.assertFalse(Equipo.objects.filter(numero_serie='V-1').exists())
        r = self.registrar(marca='QAMARCA', modelo='QM-1', ratio='5:1', rotacion='#2', operacion='LATERAL', numero='V-2', serie='V-2')
        self.assertFalse(Equipo.objects.filter(numero_serie='V-2').exists())  # operación inexistente para ese modelo
        r = self.registrar(marca='QAMARCA', modelo='OTRO-MODELO', numero='V-3', serie='V-3', sin_catalogo='on')
        self.assertEqual(r.status_code, 302)
        # Vista previa: solo catálogos activos de la empresa, con piezas, marcadores y PDF.
        url = reverse('masiscam:catalogo_vista_previa')
        q = dict(marca='qamarca', modelo='QM-1', ratio='5:1', rotacion='#2', operacion='')
        self.client.force_login(self.tecnico)
        datos = self.client.get(url, q).json()['candidatos']
        self.assertEqual([(c['pk'], c['piezas'], c['marcadores']) for c in datos], [(self.refs[''].pk, 1, 0)])
        self.assertEqual(self.client.get(datos[0]['pdf']).status_code, 200)
        self.assertEqual(self.client.get(url, dict(q, modelo='NO-EXISTE')).json()['candidatos'], [])
        self.assertEqual(self.client.get(url, dict(q, ratio='')).json()['candidatos'], [])
        self.client.force_login(self.planta)
        svc.estado_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=self.refs[''].pk, estado='RETIRADA', clave=uuid.uuid4())
        self.client.force_login(self.tecnico)
        self.assertEqual(self.client.get(url, q).json()['candidatos'], [])  # los inactivos no se ofrecen
        self.assertEqual(self.client.get(datos[0]['pdf']).status_code, 404)
        self.assertNotIn(('QAMARCA', 'QM-1', '5:1', '#2', ''),
            {(o['marca'], o['modelo'], o['ratio'], o['rotacion'], o['operacion']) for o in __import__('json').loads(
                self.client.get(reverse('masiscam:ficha_crear')).context['form'].catalogo_json)})
        # Editar un equipo existente conserva su configuración histórica aunque no esté en el catálogo.
        equipo = Equipo.objects.get(numero_serie='V-3')
        form = FichaEquipoForm(data={}, empresa=self.empresa, usuario=self.planta, equipo=equipo)
        form.is_valid()
        form._validar_configuracion(dict(marca=equipo.marca, modelo=equipo.modelo, ratio=equipo.ratio, rotacion=equipo.rotacion, operacion=equipo.operacion))
        self.assertNotIn('catálogo activo', ' '.join(form.errors.get('marca', [])))
        form._validar_configuracion(dict(marca='OTRA', modelo='X', ratio='1:1', rotacion='#1', operacion=''))  # un cambio sí se valida
        self.assertIn('catálogo activo', ' '.join(form.errors.get('marca', [])))

    def test_varias_revisiones_exactas_exigen_elegir_y_auditan_el_vinculo(self):
        # Dos catálogos activos con la misma configuración: no se asigna ninguno de forma arbitraria.
        ReferenciaTecnica.objects.filter(pk=self.refs['VERTICAL'].pk).update(operacion='')
        r = self.registrar(marca='QAMARCA', modelo='QM-1', ratio='5:1', rotacion='#2', numero='A-1', serie='A-1')
        self.assertEqual(r.status_code, 302)
        equipo = Equipo.objects.get(numero_serie='A-1')
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertEqual(len(res['candidatos']), 2)
        from . import plano_services as ps
        busq = lambda: ps.buscar_parte_catalogo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo, posicion='1')
        self.assertEqual(busq()['estado'], 'ambigua')  # no se elige una revisión por el usuario
        self.assertFalse(any(c.aprobada for c in res['candidatos']))
        self.assertFalse(equipo.aplicabilidades.exists() if hasattr(equipo, 'aplicabilidades') else
                         ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo).exists())
        url = reverse('masiscam:vincular_plano', args=[equipo.pk])
        elegido = self.refs[''].pk
        self.client.force_login(self.oficina)
        self.assertIn(self.client.post(url, {'referencia': elegido}).status_code, (302, 403, 404))
        self.assertFalse(ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo).exists())
        self.client.force_login(self.tecnico)
        self.assertEqual(self.client.post(url, {'referencia': 999999}).status_code, 404)
        self.assertEqual(self.client.post(url, {'referencia': elegido}).status_code, 302)
        self.assertEqual(list(ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo, aplicabilidades__activa=True).values_list('pk', flat=True)), [elegido])
        self.assertTrue(Auditoria.objects.filter(accion='BIBLIOTECA_APLICABILIDAD_SELECCIONADA', usuario=self.tecnico).exists())
        r = busq()  # con la revisión elegida, el autocompletado usa solo esa
        self.assertEqual((r['estado'], r['referencia']['codigo'], r['codigo']), ('encontrada', self.refs[''].codigo, 'QA-1'))
        self.assertEqual(self.client.post(url, {'referencia': elegido}).status_code, 302)  # sin duplicar
        self.assertEqual(ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo).count(), 1)

    def test_editar_reductor_conserva_configuracion_vinculo_y_datos_historicos(self):
        r = self.registrar(marca='QAMARCA', modelo='QM-1', ratio='5:1', rotacion='#2', numero='E-1', serie='E-1')
        self.assertEqual(r.status_code, 302)
        equipo = Equipo.objects.get(numero_serie='E-1')
        Equipo.objects.filter(pk=equipo.pk).update(service_factor='1.2', rpm='1750', tipo_aceite='ISO 220', observaciones='Histórico')
        svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        vinculos = list(ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo).values_list('pk', flat=True))
        self.assertEqual(vinculos, [self.refs[''].pk])
        # Configuración histórica que ya no está en un catálogo activo.
        Equipo.objects.filter(pk=equipo.pk).update(marca='LEGADO', modelo='L-1', ratio='1:1', rotacion='#9')
        self.client.force_login(self.planta)
        datos = dict(cliente=self.cliente.pk, camaronera='Camaronera QA', estacion='E-9', sector='S-9', numero_equipo='E-1',
            numero_serie='E-1', potencia='30 HP', marca='LEGADO', modelo='L-1', ratio='1:1', rotacion='#9', operacion='',
            service_factor='1.2', rpm='1750', tipo_aceite='ISO 220', observaciones='Histórico', consulta_publica_activa='on')
        r = self.client.post(reverse('masiscam:ficha_editar', args=[equipo.pk]), datos)
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        equipo.refresh_from_db()
        self.assertEqual((equipo.marca, equipo.modelo, equipo.ratio, equipo.rotacion, equipo.potencia_hp, equipo.service_factor,
                          equipo.rpm, equipo.tipo_aceite, equipo.observaciones),
                         ('LEGADO', 'L-1', '1:1', '#9', '30 HP', '1.2', '1750', 'ISO 220', 'Histórico'))
        self.assertEqual(list(ReferenciaTecnica.objects.filter(aplicabilidades__equipo=equipo).values_list('pk', flat=True)), vinculos)
        # Cambiar la configuración a una inexistente sí se rechaza; sin cambios de config no se exige nada.
        r = self.client.post(reverse('masiscam:ficha_editar', args=[equipo.pk]), dict(datos, modelo='NO-EXISTE'))
        self.assertEqual(r.status_code, 200)
        equipo.refresh_from_db()
        self.assertEqual(equipo.modelo, 'L-1')

    def test_taller_accede_a_su_listado_de_reductores_y_sin_catalogos_se_informa(self):
        from accounts.models import Empresa
        from .models import Cliente, Proyecto
        propio = Equipo.objects.get(pk=self.exp.registro.equipo_id)  # registrado por TALLER en setUp
        otro_taller = Equipo.objects.create(proyecto=propio.proyecto, cliente=propio.cliente, nombre='EQ-AJENO', numero_serie='AJENO-1',
            tipo_producto='REDUCTOR', registrado_por=self.planta)
        otra = Empresa.objects.create(nombre='Otra empresa QA')
        cli = Cliente.objects.create(empresa=otra, nombre_comercial='Cliente otra', ruc='77', razon_social='Otra')
        pro = Proyecto.objects.create(empresa=otra, codigo='OT', nombre='Otra', cliente='Otra', responsable='QA',
            fecha_inicio='2026-11-20', creado_por=self.planta)
        Equipo.objects.create(proyecto=pro, cliente=cli, nombre='EQ-OTRA-EMPRESA', numero_serie='OTRA-1', tipo_producto='REDUCTOR', registrado_por=self.tecnico)
        Equipo.objects.filter(pk=propio.pk).update(tipo_producto='REDUCTOR')
        self.client.force_login(self.tecnico)
        url = reverse('masiscam:producto_listado', args=['reductor'])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, propio.numero_serie)
        self.assertNotContains(r, 'AJENO-1')
        self.assertNotContains(r, 'OTRA-1')
        self.assertContains(r, reverse('masiscam:taller_equipo', args=[propio.pk]))
        self.assertNotContains(r, reverse('masiscam:ficha_detalle', args=[propio.pk]))
        self.assertEqual(self.client.post(url, {'equipo_id': propio.pk, 'estado': 'INACTIVO'}).status_code, 403)  # sin administración
        self.assertEqual(self.client.get(reverse('masiscam:producto_listado', args=['bomba'])).status_code, 403)  # solo Reductores
        from .templatetags.masiscam_navigation import menu_compartido
        from django.test import RequestFactory
        rq = RequestFactory().get('/'); rq.user = self.tecnico; rq.empresa_activa = self.empresa; rq.rol_masiscam = self.tecnico.perfiles.get().rol_masiscam
        hijos = {h['label']: h['url'] for it in menu_compartido({'request': rq})['items'] for h in it.get('children', [])}
        self.assertEqual(hijos['Reductores'], url)
        self.assertIsNone(hijos['Bombas'])
        self.assertEqual(self.client.get(url, {'cliente': cli.pk}).status_code, 404)  # cliente de otra empresa
        self.assertEqual(self.client.get(reverse('masiscam:ficha_detalle', args=[propio.pk])).status_code, 403)
        # ADMIN sigue viendo todo; OFICINA conserva su listado.
        self.client.force_login(self.planta)
        self.assertContains(self.client.get(url), 'AJENO-1')
        self.client.force_login(self.oficina)
        self.assertEqual(self.client.get(url).status_code, 200)
        # Formulario con catálogos activos: datos para la cascada; sin catálogos: mensaje explícito.
        self.client.force_login(self.tecnico)
        self.assertContains(self.client.get(reverse('masiscam:ficha_crear')), 'data-catalogo=')
        for ref in ReferenciaTecnica.objects.filter(empresa=self.empresa, tipo='PLANO', estado='PUBLICADA'):
            svc.estado_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=ref.pk, estado='RETIRADA', clave=uuid.uuid4())
        r = self.client.get(reverse('masiscam:ficha_crear'))
        self.assertFalse(r.context['form'].catalogo)
        self.assertContains(r, 'No hay configuraciones disponibles')
        self.assertContains(r, 'registrar sin plano')

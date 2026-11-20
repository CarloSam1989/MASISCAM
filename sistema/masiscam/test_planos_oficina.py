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
        r = self.registrar(marca='OTRAMARCA', modelo='XX-9', ratio='7:1', rotacion='#1', numero='NUEVO-3', serie='SERIE-3')
        self.assertEqual(r.status_code, 302)
        equipo = Equipo.objects.get(numero_serie='SERIE-3')
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa, equipo=equipo)
        self.assertEqual(res['candidatos'], [])
        # Configuración incompleta: no se adivina ningún plano.
        r = self.registrar(marca='QAMARCA', modelo='QM-1', numero='NUEVO-4', serie='SERIE-4')
        res = svc.planos_compatibles_equipo(usuario=self.tecnico, empresa=self.empresa,
                                            equipo=Equipo.objects.get(numero_serie='SERIE-4'))
        self.assertEqual((res['candidatos'], res['faltantes']), ([], ['rotacion', 'relacion de transmision']))

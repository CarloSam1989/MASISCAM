import copy
import uuid
from decimal import Decimal
from io import BytesIO
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import Empresa, Perfil
from .models import Diagnostico, MapaPlano, PosicionPlano, PiezaRequerida, ReferenciaTecnica, RolMasiscam
from . import tecnica_services as tecnica, plano_services as svc, test_tecnica as qa


FLAGS = dict(MASISCAM_TECNICA_LOCAL=True, MASISCAM_RECEPCION_LOCAL=True, GOOGLE_DRIVE_ENABLED=False)


def foto_pieza():
    data = BytesIO()
    Image.new('RGB', (50, 40), 'green').save(data, 'PNG')
    return SimpleUploadedFile('pieza-qa.png', data.getvalue(), content_type='image/png')


def fixture(obj):
    obj.empresa, obj.oficina, obj.planta, obj.tecnico, obj.exp = qa.preparar()
    qa.inspeccion_enviada(obj.empresa, obj.tecnico, obj.exp)
    obj.diag = tecnica.crear_revision(usuario=obj.tecnico, empresa=obj.empresa,
        expediente_id=obj.exp.pk, tipo='DIAGNOSTICO', clave=uuid.uuid4())
    obj.ref, obj.parte = qa.TecnicaTests.referencia(obj)
    obj.client.force_login(obj.tecnico)


@override_settings(**FLAGS)
class PlanoTests(TestCase):
    def setUp(self):
        fixture(self)

    def mapa(self, **kwargs):
        return svc.guardar_mapa(usuario=kwargs.pop('usuario', self.planta), empresa=self.empresa,
            referencia_id=self.ref.pk, clave=kwargs.pop('clave', uuid.uuid4()),
            base_version=kwargs.pop('base_version', self.ref.mapas.count()),
            posiciones=kwargs.pop('posiciones', [dict(parte=self.parte.pk, pagina=1, x='0.5', y='0.4')]), **kwargs)

    def guardar(self, **kwargs):
        self.diag.refresh_from_db()
        datos = dict(origen='BIBLIOTECA', parte_id=self.parte.pk, cantidad='2.5', unidad='unidad',
            condicion='Desgaste observado QA', accion='EVALUAR', observaciones='Evaluación sintética QA')
        datos.update(kwargs.pop('datos', {}))
        return svc.guardar_seleccion(usuario=kwargs.pop('usuario', self.tecnico), empresa=self.empresa,
            diagnostico_id=self.diag.pk, version=kwargs.pop('version', self.diag.version_edicion),
            clave=kwargs.pop('clave', uuid.uuid4()), datos=datos, **kwargs)

    def test_coordenadas_limites_paginas_partes_y_versiones(self):
        mapa = self.mapa(posiciones=[dict(parte=self.parte.pk, pagina=1, x='0', y='1')])
        punto = mapa.posiciones.get()
        self.assertEqual(punto.x, Decimal('0'))
        self.assertEqual(punto.y, Decimal('1'))
        for change in ({'x':'NaN'}, {'x':'Infinity'}, {'x':'-0.1'}, {'y':'1.1'},
                       {'pagina':0}, {'pagina':2}, {'parte':999999}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.mapa(posiciones=[dict(dict(parte=self.parte.pk, pagina=1, x='.5', y='.4'), **change)])
        with self.assertRaises(ValidationError):
            self.mapa(base_version=0)
        key = uuid.uuid4()
        second = self.mapa(clave=key, base_version=1)
        self.assertEqual(self.mapa(clave=key, base_version=1).pk, second.pk)
        self.assertEqual(mapa.posiciones.get().x, Decimal('0'))
        with self.assertRaises(PermissionDenied):
            punto.x = Decimal('.8'); punto.save()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            PosicionPlano.objects.filter(pk=punto.pk).update(x='.8')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MapaPlano.objects.filter(pk=mapa.pk).delete()

    def test_seleccion_listado_y_posicion_snapshot_y_fotos(self):
        mapa = self.mapa()
        punto = mapa.posiciones.get()
        key = uuid.uuid4()
        pk = self.guardar(clave=key, datos={'posicion_plano_id':punto.pk}, fotografias=[foto_pieza()])
        self.assertEqual(self.guardar(clave=key, version=1, datos={'posicion_plano_id':punto.pk}, fotografias=[foto_pieza()]), pk)
        pieza = PiezaRequerida.objects.get(pk=pk)
        self.assertEqual(pieza.cantidad, Decimal('2.5'))
        self.assertEqual(pieza.fotografias.get().diagnostico_id, self.diag.pk)
        snapshot = copy.deepcopy(pieza.snapshot)
        self.assertEqual(snapshot['plano']['version'], 1)
        self.mapa(posiciones=[dict(parte=self.parte.pk, pagina=1, x='.9', y='.9')])
        pieza.refresh_from_db()
        self.assertEqual(pieza.snapshot, snapshot)
        self.guardar(pieza_id=pk, datos={'posicion_plano_id':punto.pk, 'cantidad':'3', 'observaciones':'Modificación QA'})
        pieza.refresh_from_db()
        self.assertEqual(pieza.observaciones, 'Modificación QA')
        listed = self.guardar()
        self.assertNotIn('plano', PiezaRequerida.objects.get(pk=listed).snapshot)
        self.diag.refresh_from_db()
        svc.guardar_seleccion(usuario=self.tecnico, empresa=self.empresa, diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4(), pieza_id=pk, operacion='retirar')
        self.assertFalse(self.diag.piezas.filter(pk=pk).exists())
        self.assertTrue(self.diag.evidencias.filter(pieza__isnull=True).exists())

    def test_cantidades_acciones_fotos_invalidas_y_conflicto_no_guardan(self):
        for data in ({'cantidad':'0'}, {'cantidad':'-1'}, {'cantidad':'NaN'}, {'cantidad':'1.0000001'},
                     {'accion':''}, {'accion':'APROBAR'}, {'condicion':''}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.guardar(datos=data)
        with self.assertRaises(ValidationError):
            self.guardar(version=999)
        with self.assertRaises(ValidationError):
            self.guardar(fotografias=[SimpleUploadedFile('mal.png', b'not a photo', content_type='image/png')])
        self.assertEqual(self.diag.piezas.count(), 0)

    def test_permisos_empresa_asignacion_csrf_y_urls_ajax(self):
        for codigo in ('TALLER','ADMIN','PLANTA','OFICINA','GERENCIA','CONSULTA'):
            user = self.planta if codigo == 'PLANTA' else self.oficina if codigo == 'OFICINA' else qa.usuario(self.empresa, 'plano-'+codigo, codigo)
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('masiscam:plano_pieza', args=[self.diag.pk]), {}).status_code, 403)
            if codigo != 'PLANTA':
                self.assertEqual(self.client.post(reverse('masiscam:plano_mapa', args=[self.ref.pk]), '{}', content_type='application/json').status_code, 403)
            if codigo == 'TALLER':
                for name in ('plano', 'plano_datos'):
                    self.assertEqual(self.client.get(reverse('masiscam:'+name, args=[self.ref.pk])).status_code, 403)
        other = qa.usuario(self.empresa, 'tecnico-plano-no-asignado', 'TECNICO')
        self.client.force_login(other)
        self.assertIn(self.client.get(reverse('masiscam:plano_datos', args=[self.ref.pk]), {'diagnostico':self.diag.pk}).status_code, (403,404))
        otra = Empresa.objects.create(nombre='Otra empresa QA')
        user = qa.usuario(otra, 'planta-plano-otra', 'PLANTA')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('masiscam:plano_datos', args=[self.ref.pk])).status_code, 404)
        with self.assertRaises(PermissionDenied):
            self.guardar(usuario=other)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.tecnico)
        self.assertEqual(strict.post(reverse('masiscam:plano_pieza', args=[self.diag.pk]), {}).status_code, 403)

    def test_envio_congela_piezas_fotos_y_nueva_revision_conserva_vinculos(self):
        pk = self.guardar(fotografias=[foto_pieza()])
        self.diag.refresh_from_db()
        tecnica.guardar_revision(usuario=self.tecnico, empresa=self.empresa, tipo='DIAGNOSTICO', revision_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4(), datos=dict(fecha=timezone.localdate(),
                fallas='QA', causas='QA', componentes_afectados='QA', recomendaciones='QA', conclusiones='QA', repuestos='REQUERIDOS'))
        self.diag.refresh_from_db()
        tecnica.enviar_revision(usuario=self.tecnico, empresa=self.empresa, tipo='DIAGNOSTICO', revision_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4())
        with self.assertRaises(PermissionDenied): self.guardar(pieza_id=pk)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            PiezaRequerida.objects.filter(pk=pk).update(observaciones='Cambio indebido')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.diag.evidencias.filter(pieza_id=pk).update(pieza=None)
        tecnica.decidir_diagnostico(usuario=self.planta, empresa=self.empresa, diagnostico_id=self.diag.pk,
            decision='OBSERVADO', motivo='Corregir en revisión nueva QA', clave=uuid.uuid4())
        nueva = tecnica.crear_revision(usuario=self.planta, empresa=self.empresa, expediente_id=self.exp.pk,
            tipo='DIAGNOSTICO', clave=uuid.uuid4(), motivo='Corrección QA')
        self.assertEqual(nueva.piezas.get().fotografias.get().diagnostico_id, nueva.pk)
        self.assertNotEqual(nueva.piezas.get().pk, pk)

    def test_csv_revision_humana_no_inventa_datos(self):
        text='posicion,numero_parte,descripcion,unidad,cantidad_referencia,pagina\n91,,STEADY BUSHING V2030-SB-____,,,1\n'
        row = svc.leer_materiales(text)[0]
        self.assertIsNone(row['cantidad_referencia'])
        self.assertEqual(row['numero_parte'], '')
        for invalid in (text+text.splitlines()[1], text.replace(',,,1',' ,NaN,1'), 'posicion,descripcion\n1,x'):
            with self.assertRaises(ValidationError): svc.leer_materiales(invalid)
        self.client.force_login(self.planta)
        self.assertEqual(self.client.get(reverse('masiscam:plano_importar')).status_code, 200)
        before = ReferenciaTecnica.objects.count()
        with self.assertRaises(ValidationError):
            svc.importar_m20(usuario=self.planta, empresa=self.empresa, proyecto_id=self.exp.registro.equipo.proyecto_id,
                archivo=qa.pdf_plano(), materiales=text, procedencia='QA', autorizacion='QA', confirmado=False, clave=uuid.uuid4())
        self.assertEqual(ReferenciaTecnica.objects.count(), before)

    def test_importacion_verificada_crea_solo_borrador_y_reintento_idempotente(self):
        from reportlab.pdfgen.canvas import Canvas
        data = BytesIO()
        canvas = Canvas(data)
        canvas.drawString(40, 750, 'PDF SINTETICO QA - M20A-HNRC 3:1 Fig.1')
        canvas.save()
        def upload():
            return SimpleUploadedFile('m20-qa.pdf', data.getvalue(), content_type='application/pdf')
        text='posicion,numero_parte,descripcion,unidad,cantidad_referencia,pagina\n91,,Pieza QA sin codigo verificado,,,1\n'
        key = uuid.uuid4()
        args = dict(usuario=self.planta, empresa=self.empresa, proyecto_id=self.exp.registro.equipo.proyecto_id,
            materiales=text, procedencia='Simulación QA', autorizacion='Solo QA', confirmado=True, clave=key)
        ref = svc.importar_m20(archivo=upload(), **args)
        self.assertEqual(svc.importar_m20(archivo=upload(), **args).pk, ref.pk)
        self.assertEqual(ref.estado, 'BORRADOR')
        self.assertEqual(ref.modelo, 'M20A')
        self.assertEqual(ref.ratio, '3:1')
        self.assertIsNone(ref.partes.get().cantidad_referencia)
        self.assertEqual(ref.partes.get().numero_parte, '')
        self.assertFalse(ref.aplicabilidades.exists())
        self.assertFalse(ref.mapas.exists())
        self.client.force_login(self.planta)
        response = self.client.post(reverse('masiscam:plano_importar'), {'op':'revisar', 'materiales':text}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['filas'][0]['numero_parte'], '')

    def test_plano_retirado_conserva_consulta_y_permite_limpiar_solo_borrador_propio(self):
        pk = self.guardar()
        tecnica.estado_referencia(usuario=self.planta, empresa=self.empresa, referencia_id=self.ref.pk,
            estado='RETIRADA', clave=uuid.uuid4())
        response = self.client.get(reverse('masiscam:plano_datos', args=[self.ref.pk]), {'diagnostico':self.diag.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['editable'])
        self.assertFalse(response.json()['seleccionable'])
        with self.assertRaises(PermissionDenied): self.guardar()
        self.diag.refresh_from_db()
        response = self.client.post(reverse('masiscam:plano_pieza', args=[self.diag.pk]),
            dict(operacion='retirar', pieza_id=pk, version=self.diag.version_edicion, clave=uuid.uuid4()))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['piezas'],[])


@override_settings(**FLAGS)
@skipUnless(connection.vendor == 'postgresql', 'Históricos protegidos: migración y TRUNCATE en PostgreSQL QA')
class PlanoMigrationTests(TransactionTestCase):
    def test_migracion_conserva_diagnostico_y_partes_historicas_y_guardas(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        fixture(self)
        tecnica.agregar_pieza(usuario=self.tecnico, empresa=self.empresa, diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion, clave=uuid.uuid4(), datos=dict(origen='BIBLIOTECA',
                parte_id=self.parte.pk, cantidad='1', unidad='unidad QA', condicion='QA', accion='Evaluar'))
        old = [('masiscam','0030_rol_taller_ingreso')]
        latest = [('masiscam','0032_guardas_plano_interactivo')]
        try:
            executor = MigrationExecutor(connection); executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            before = {name: list(apps.get_model('masiscam',name).objects.values().order_by('pk'))
                for name in ('Diagnostico','PiezaRequerida','ReferenciaTecnica','Documento')}
            executor = MigrationExecutor(connection); executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            for name, rows in before.items():
                model = apps.get_model('masiscam',name)
                self.assertEqual(list(model.objects.values(*rows[0].keys()).order_by('pk')), rows)
            pieza = apps.get_model('masiscam','PiezaRequerida').objects.get()
            self.assertIsNone(pieza.posicion_plano_id)
            self.assertEqual(pieza.observaciones,'')
            self.assertFalse(apps.get_model('masiscam','MapaPlano').objects.exists())
            # The pre-existing phase-3 immutable-reference trigger must survive
            # SQLite table rebuilds as well as additive PostgreSQL migrations.
            with transaction.atomic(), self.assertRaises(IntegrityError):
                apps.get_model('masiscam','ReferenciaTecnica').objects.filter(pk=self.ref.pk).update(titulo='Cambio indebido')
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

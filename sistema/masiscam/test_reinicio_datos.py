"""Reinicio de datos operativos en PostgreSQL 16 aislado (requiere MASISCAM_TEST_POSTGRES)."""
import json
import os
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import uuid
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings

from accounts.models import Empresa, Perfil
from . import test_plano as qa
from .models import Auditoria, Cliente, Documento, Equipo, Proyecto, RolMasiscam
from .management.depuracion_reinicio import snapshot_acceso

CONSERVADOS = {'RolMasiscam', 'Auditoria'}


@skipUnless(os.environ.get('MASISCAM_TEST_POSTGRES'), 'Requiere PostgreSQL 16 aislado.')
@override_settings(**{**qa.FLAGS, "GOOGLE_DRIVE_ENABLED": False})
class ReinicioDatosTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Solo PostgreSQL 16 aislado.')
        qa.fixture(self)
        self.temp = tempfile.TemporaryDirectory(prefix='masiscam-reinicio-')
        self.addCleanup(self.temp.cleanup)
        otra = Empresa.objects.create(nombre='Otra empresa QA')
        cliente = Cliente.objects.create(empresa=otra, nombre_comercial='Cliente ajeno', ruc='9', razon_social='Ajeno')
        proyecto = Proyecto.objects.create(empresa=otra, codigo='AJ', nombre='Ajeno', cliente='Ajeno', responsable='QA',
            fecha_inicio='2026-11-16', creado_por=self.planta)
        Equipo.objects.create(proyecto=proyecto, cliente=cliente, nombre='EQ ajeno', numero_serie='AJ-1')

    def run_command(self, accion, **kwargs):
        salida = StringIO()
        call_command('reiniciar_datos_masiscam', accion, stdout=salida, **kwargs)
        return json.loads(salida.getvalue())

    def respaldo(self, **opts):
        raiz = Path(__file__).resolve().parent.parent / '.venv-rbac-pg/pgsql/bin'
        dump, restore = raiz / 'pg_dump.exe', raiz / 'pg_restore.exe'
        ruta = Path(self.temp.name) / 'backup.dump'
        cfg = connection.settings_dict
        env = dict(os.environ, PGHOST=cfg['HOST'], PGPORT=str(cfg['PORT']), PGUSER=cfg['USER'],
                   PGPASSWORD=cfg['PASSWORD'], PGDATABASE=cfg['NAME'])
        subprocess.run([str(dump), '-Fc', '--file=' + str(ruta)], env=env, capture_output=True, check=True, timeout=90)
        salida = Path(self.temp.name) / 'verificado.json'
        with patch.object(type(self), 'databases', self.databases | {'depuracion_admin', 'depuracion_restore'}):
            self.run_command('verificar-respaldo', usuario=self.planta.pk, respaldo=str(ruta),
                qa_url=os.environ['MASISCAM_TEST_POSTGRES'], pg_restore=str(restore),
                carpeta=str(Path(self.temp.name) / 'custodia'), salida=str(salida), **opts)
        return ruta, salida

    def operativos(self):
        return {m.__name__: m._base_manager.count() for m in apps.get_app_config('masiscam').get_models()
                if m.__name__ not in CONSERVADOS}

    def test_simulacion_respaldo_ejecucion_conserva_acceso_y_permite_registrar(self):
        usuarios = list(get_user_model().objects.order_by('pk').values_list('pk', 'password', 'is_active'))
        acceso = snapshot_acceso()
        archivos = list(Documento.objects.exclude(archivo='').values_list('archivo', flat=True))
        self.assertTrue(archivos and all(default_storage.exists(a) for a in archivos))
        antes = self.operativos()
        self.assertTrue(all(antes[n] for n in ('Cliente', 'Proyecto', 'Equipo', 'ExpedienteMantenimiento',
                                                'ReferenciaTecnica', 'Documento', 'Diagnostico')))
        auditorias = Auditoria.objects.count()
        # Simular no escribe nada.
        sim = self.run_command('simular', usuario=self.planta.pk)
        plan = sim['simulacion']
        self.assertEqual(plan['bloqueos'], [])
        self.assertEqual(plan['cantidades']['registros'], sum(antes.values()))
        self.assertGreaterEqual(plan['cantidades']['pdf'], 1)
        self.assertEqual((self.operativos(), Auditoria.objects.count()), (antes, auditorias))
        ruta, constancia = self.respaldo()
        self.assertEqual(self.operativos(), antes)  # el ensayo con rollback no alteró la fuente
        base = dict(usuario=self.planta.pk, respaldo=str(ruta), verificacion=str(constancia), huella=sim['huella'],
                    carpeta=str(Path(self.temp.name) / 'custodia'), motivo='Reinicio QA')
        confirmar = 'REINICIAR TODOS LOS DATOS OPERATIVOS ' + sim['huella']
        with self.assertRaises(CommandError):
            self.run_command('ejecutar', confirmar='si', **base)
        self.assertEqual(self.operativos(), antes)
        with self.assertRaises(CommandError):
            self.run_command('ejecutar', confirmar=confirmar, **{**base, 'usuario': self.oficina.pk})
        res = self.run_command('ejecutar', confirmar=confirmar, **base)
        self.assertEqual(res['registros_eliminados'], plan['cantidades']['registros'])
        # Todo cero; auditoría y acceso intactos.
        self.assertEqual(set(self.operativos().values()), {0})
        self.assertEqual(snapshot_acceso(), acceso)
        self.assertEqual(list(get_user_model().objects.order_by('pk').values_list('pk', 'password', 'is_active')), usuarios)
        self.assertEqual(Perfil.objects.count(), acceso['perfiles']['filas'])
        self.assertGreater(RolMasiscam.objects.count(), 0)
        self.assertEqual(Auditoria.objects.count(), auditorias + 1)
        self.assertFalse(Auditoria.objects.exclude(expediente=None).exists() or Auditoria.objects.exclude(proyecto=None).exists())
        self.assertTrue(Auditoria.objects.filter(accion='REINICIO_DATOS_MASISCAM').exists())
        self.assertFalse([a for a in archivos if default_storage.exists(a)])
        self.assertTrue(get_user_model().objects.filter(is_active=True).count() >= 3)
        # El sistema vuelve a registrar: secuencias reiniciadas, usuarios intactos.
        cliente = Cliente.objects.create(empresa=self.empresa, nombre_comercial='Nuevo', ruc='1', razon_social='Nuevo')
        proyecto = Proyecto.objects.create(empresa=self.empresa, codigo='N1', nombre='Nuevo', cliente='Nuevo',
            responsable='QA', fecha_inicio='2026-11-20', creado_por=self.planta)
        equipo = Equipo.objects.create(proyecto=proyecto, cliente=cliente, nombre='EQ nuevo', numero_serie='N-1')
        self.assertEqual((cliente.pk, proyecto.pk, equipo.pk), (1, 1, 1))
        # Segunda ejecución: la constancia ya no corresponde y no se borra nada más.
        with self.assertRaises(CommandError):
            self.run_command('ejecutar', confirmar=confirmar, **base)
        self.assertEqual(Equipo.objects.count(), 1)

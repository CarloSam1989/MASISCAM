import json
import os
from pathlib import Path
import subprocess
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from datetime import datetime
from zoneinfo import ZoneInfo

from accounts.models import Empresa, Perfil
from .models import Auditoria, Cliente, Equipo, ExpedienteMantenimiento, Proyecto, RegistroEquipo, RolMasiscam
from .management.commands.depurar_dos_equipos_prueba import fingerprint, inspect


@override_settings(GOOGLE_DRIVE_ENABLED=False)
class DepuracionPruebaTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Procedimiento exclusivo de PostgreSQL 16 aislado.')
        self.user = get_user_model().objects.create_user(username='admin-depuracion')
        self.empresa = Empresa.objects.create(nombre='Empresa QA')
        perfil = Perfil.objects.create(user=self.user, empresa=self.empresa)
        self.rol = RolMasiscam.objects.create(perfil=perfil, rol='ADMIN')
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre_comercial='Cliente QA', ruc='QA', razon_social='QA')
        self.proyecto = Proyecto.objects.create(empresa=self.empresa, codigo='QA', nombre='QA', cliente='QA',
            responsable='QA', fecha_inicio='2026-11-16', creado_por=self.user)
        self.antiguo = self.equipo('antiguo')
        self.uno, self.dos = self.equipo('prueba1'), self.equipo('prueba2')
        self.temp = tempfile.TemporaryDirectory(prefix='masiscam-depuracion-')
        self.addCleanup(self.temp.cleanup)

    def equipo(self, nombre):
        return Equipo.objects.create(proyecto=self.proyecto, cliente=self.cliente, nombre=nombre, numero_serie=nombre)

    def run_command(self, action, **kwargs):
        out = StringIO()
        call_command('depurar_dos_equipos_prueba', action, stdout=out, **kwargs)
        return json.loads(out.getvalue())

    def opts(self):
        if getattr(self, '_monthly', False):
            from .management.commands.depurar_dos_equipos_prueba import inspect_month
            return dict(mes='2026-11', empresa=self.empresa.pk, usuario=self.user.pk,
                        huella=fingerprint(inspect_month(self.empresa.pk)))
        return dict(ids=[self.uno.pk, self.dos.pk], usuario=self.user.pk, huella=fingerprint(inspect()))

    def tools(self):
        root = Path(__file__).resolve().parent.parent / '.venv-rbac-pg/pgsql/bin'
        return root / 'pg_dump.exe', root / 'pg_restore.exe'

    def backup(self):
        dump, restore = self.tools()
        path = Path(self.temp.name) / 'backup.dump'
        cfg = connection.settings_dict
        env = os.environ.copy()
        env.update(PGHOST=cfg['HOST'], PGPORT=str(cfg['PORT']), PGUSER=cfg['USER'],
                   PGPASSWORD=cfg['PASSWORD'], PGDATABASE=cfg['NAME'])
        subprocess.run([str(dump), '-Fc', '--file=' + str(path)], env=env,
                       capture_output=True, check=True, timeout=60)
        # URL QA: únicamente el destino de una base nueva de restauración, jamás la fuente.
        qa = os.environ['MASISCAM_TEST_POSTGRES']
        receipt = Path(self.temp.name) / 'verificado.json'
        # Autorizar únicamente los dos alias temporales creados por el procedimiento.
        with patch.object(type(self), 'databases', self.databases | {'depuracion_admin', 'depuracion_restore'}):
            self.run_command('verificar-respaldo', **self.opts(), respaldo=str(path), qa_url=qa,
                             pg_restore=str(restore), salida=str(receipt))
        return dict(respaldo=str(path), verificacion=str(receipt))

    def delete_opts(self, backup):
        opts = self.opts()
        opts.update(backup, motivo='Dos registros QA verificados',
                    confirmar='ELIMINAR ' + ','.join(map(str, sorted(opts['ids']))) + ' ' + opts['huella'])
        return opts

    def test_consulta_y_simulacion_solo_lectura_identidad_y_relaciones(self):
        count = Auditoria.objects.count()
        query = self.run_command('consultar')
        self.assertEqual([e['id'] for e in query['simulacion']['equipos']], [self.dos.pk, self.uno.pk])
        self.assertEqual(query['simulacion']['equipos'][0]['cliente_id'], self.cliente.pk)
        self.assertEqual(self.run_command('simular', **self.opts())['huella'], query['huella'])
        self.assertEqual(Auditoria.objects.count(), count)
        self.assertEqual(Equipo.objects.count(), 3)

    def test_ids_exactos_duplicados_y_huella_obsoleta_rechazados(self):
        for ids in ([self.uno.pk, self.uno.pk], [self.antiguo.pk, self.dos.pk]):
            with self.assertRaises(CommandError):
                self.run_command('simular', **{**self.opts(), 'ids': ids})
        opts = self.opts()
        self.uno.nombre = 'cambio posterior'
        self.uno.save()
        with self.assertRaises(CommandError):
            self.run_command('simular', **opts)

    def test_no_omite_permisos_ni_multiempresa(self):
        self.rol.rol = 'OFICINA'
        self.rol.save()
        with self.assertRaises(CommandError):
            self.run_command('simular', **self.opts())
        self.rol.rol = 'ADMIN'
        self.rol.save()
        empresa = Empresa.objects.create(nombre='Otra empresa')
        self.proyecto.empresa = empresa
        self.proyecto.save()
        with self.assertRaises(CommandError):
            self.run_command('simular', **self.opts())

    def test_relaciones_expedientes_documentos_drive_bloquean(self):
        registro = RegistroEquipo.objects.create(equipo=self.uno, tipo='REPARACION', fecha='2026-11-16')
        exp = ExpedienteMantenimiento.objects.create(registro=registro, empresa=self.empresa,
            cliente_contractual=self.cliente, correlativo=1, numero='QA-1', huella_solicitud='qa', creado_por=self.user)
        self.dos.drive_folder_id = 'no-tocar'
        self.dos.save()
        query = self.run_command('simular', **self.opts())['simulacion']
        self.assertEqual(query['eliminaria'], [])
        self.assertEqual(query['equipos'][1]['expedientes_inmutables'][0]['datos']['id'], exp.pk)
        backup = self.backup()
        with self.assertRaisesMessage(CommandError, 'Borrado bloqueado'):
            self.run_command('eliminar', **self.delete_opts(backup))
        self.assertTrue(ExpedienteMantenimiento.objects.filter(pk=exp.pk).exists())
        self.assertEqual(Equipo.objects.count(), 3)

    def test_respaldo_real_restauracion_confirmacion_y_borrado_auditado(self):
        backup = self.backup()
        options = self.delete_opts(backup)
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **{**options, 'confirmar': 'si'})
        result = self.run_command('eliminar', **options)
        self.assertEqual(result['eliminados'], sorted([self.uno.pk, self.dos.pk]))
        self.assertEqual(list(Equipo.objects.values_list('pk', flat=True)), [self.antiguo.pk])
        self.assertEqual(Auditoria.objects.filter(accion='ELIMINAR_EQUIPO_PRUEBA').count(), 2)
        self.assertTrue(Cliente.objects.filter(pk=self.cliente.pk).exists())
        self.assertTrue(Proyecto.objects.filter(pk=self.proyecto.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **options)

    def test_respaldo_obsoleto_corrupto_y_sin_constancia_rechazados(self):
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **self.opts())
        backup = self.backup()
        original_receipt = Path(backup['verificacion']).read_text(encoding='utf-8')
        altered = json.loads(original_receipt)
        altered['huella'] = 'firma-no-corresponde'
        Path(backup['verificacion']).write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaisesMessage(CommandError, 'Constancia de restauración alterada'):
            self.run_command('eliminar', **self.delete_opts(backup))
        Path(backup['verificacion']).write_text(original_receipt, encoding='utf-8')
        original = Path(backup['respaldo']).read_bytes()
        with Path(backup['respaldo']).open('ab') as stream:
            stream.write(b'alterado')
        with self.assertRaisesMessage(CommandError, 'Respaldo/constancia'):
            self.run_command('eliminar', **self.delete_opts(backup))
        Path(backup['respaldo']).write_bytes(original)
        self.uno.nombre = 'modificado'
        self.uno.save()
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **self.delete_opts(backup))

    def test_auditoria_fallida_revierte_sin_borrar(self):
        backup = self.backup()
        original_create = Auditoria.objects.create
        calls = []
        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError('QA rollback')
            return original_create(**kwargs)
        with patch.object(Auditoria.objects, 'create', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.run_command('eliminar', **self.delete_opts(backup))
        self.assertEqual(Equipo.objects.count(), 3)
        self.assertEqual(Auditoria.objects.filter(accion='ELIMINAR_EQUIPO_PRUEBA').count(), 0)

    def test_restauracion_no_acepta_destino_remoto_ni_nombre_real(self):
        for url in ('postgres://postgres@remote/masiscam_qa_x', 'postgres://postgres@127.0.0.1/produccion'):
            with self.assertRaises(CommandError):
                self.run_command('verificar-respaldo', **self.opts(), respaldo='no.dump', qa_url=url,
                                 salida=str(Path(self.temp.name) / 'no.json'))
        backup = self.backup()
        from .management.commands import depurar_dos_equipos_prueba as command
        source = command.source_identity()
        source[0] = 'produccion_simulada'
        with patch.object(command, 'source_identity', return_value=source), patch.object(
                type(self), 'databases', self.databases | {'depuracion_admin', 'depuracion_restore'}):
            with self.assertRaisesMessage(CommandError, 'otra instancia PostgreSQL'):
                self.run_command('verificar-respaldo', **self.opts(), respaldo=backup['respaldo'],
                    qa_url=os.environ['MASISCAM_TEST_POSTGRES'], pg_restore=str(self.tools()[1]),
                    salida=str(Path(self.temp.name) / 'no-produccion.json'))

    def monthly_setup(self):
        self._monthly = True
        zone = ZoneInfo('America/Bogota')
        Equipo.objects.filter(pk=self.antiguo.pk).update(creado_en=datetime(2026, 10, 31, 23, 59, tzinfo=zone))
        Equipo.objects.filter(pk__in=[self.uno.pk, self.dos.pk]).update(creado_en=datetime(2026, 11, 16, tzinfo=zone))

    def monthly_delete_opts(self, backup):
        opts = self.opts()
        return dict(opts, **backup, motivo='Pruebas noviembre verificadas por propietario',
                    confirmar=f"LIMPIAR {self.empresa.pk} 2026-11 {opts['huella']}")

    def test_mes_simulacion_fecha_empresa_dependencias_y_qr_drive(self):
        self.monthly_setup()
        zone = ZoneInfo('America/Bogota')
        with patch.object(timezone, 'now', return_value=datetime(2026, 10, 31, 23, 59, tzinfo=zone)):
            old = RegistroEquipo.objects.create(equipo=self.uno, tipo='REPARACION', fecha='2026-10-31')
        with patch.object(timezone, 'now', return_value=datetime(2026, 12, 1, tzinfo=zone)):
            december = self.equipo('diciembre')
        self.dos.consulta_publica_activa = True
        self.dos.save()
        result = self.run_command('simular', mes='2026-11', empresa=self.empresa.pk, usuario=self.user.pk)['simulacion']
        equipos = result['registros']['masiscam.Equipo']['ids']
        self.assertEqual(set(equipos), {self.uno.pk, self.dos.pk})
        self.assertNotIn(december.pk, equipos)
        self.assertIn(str(self.uno.pk), result['conservados']['masiscam.Equipo'])
        self.assertIn(str(self.dos.pk), result['conservados']['masiscam.Equipo'])
        self.assertEqual(result['eliminaria'], {})
        self.assertIn(old.pk, result['conservados']['masiscam.Equipo'][str(self.uno.pk)]['dependencias'][0].values())
        self.assertEqual(Auditoria.objects.count(), 0)

    def test_mes_respaldo_restaura_prueba_limpieza_rollback_y_ejecucion(self):
        self.monthly_setup()
        with patch.object(timezone, 'now', return_value=datetime(2026, 11, 16, tzinfo=ZoneInfo('America/Bogota'))):
            reg = RegistroEquipo.objects.create(equipo=self.dos, tipo='REPARACION', fecha='2026-11-16')
        backup = self.backup()
        receipt = json.loads(Path(backup['verificacion']).read_text(encoding='utf-8'))
        self.assertTrue(receipt['limpieza_probada_pg16'])
        self.assertEqual(Equipo.objects.count(), 3)  # QA no altera la fuente.
        opts = self.monthly_delete_opts(backup)
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **{**opts, 'confirmar': 'si'})
        result = self.run_command('eliminar', **opts)
        self.assertEqual(set(result['eliminados']['masiscam.Equipo']), {self.uno.pk, self.dos.pk})
        self.assertEqual(result['eliminados']['masiscam.RegistroEquipo'], [reg.pk])
        self.assertTrue(Equipo.objects.filter(pk=self.antiguo.pk).exists())
        self.assertEqual(Auditoria.objects.filter(accion='LIMPIAR_PRUEBA_NOV2026').count(), 3)
        self.assertTrue(Auditoria.objects.filter(accion='LIMPIEZA_NOV2026_RESUMEN').exists())
        self.assertTrue(Cliente.objects.filter(pk=self.cliente.pk).exists())
        self.assertTrue(Proyecto.objects.filter(pk=self.proyecto.pk).exists())
        self.assertTrue(RolMasiscam.objects.filter(pk=self.rol.pk).exists())
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **opts)

    def test_mes_exige_empresa_permisos_y_huella_actual(self):
        self.monthly_setup()
        with self.assertRaisesMessage(CommandError, 'empresa'):
            self.run_command('simular', mes='2026-11', usuario=self.user.pk)
        self.rol.rol = 'OFICINA'
        self.rol.save()
        with self.assertRaises(CommandError):
            self.run_command('simular', **self.opts())
        self.rol.rol = 'ADMIN'
        self.rol.save()
        opts = self.opts()
        self.uno.nombre = 'cambió'
        self.uno.save()
        with self.assertRaises(CommandError):
            self.run_command('simular', **opts)

    def test_mes_fallo_auditoria_revierte_toda_la_limpieza(self):
        self.monthly_setup()
        backup = self.backup()
        original = Auditoria.objects.create
        def fail_summary(**kwargs):
            if kwargs['accion'] == 'LIMPIEZA_NOV2026_RESUMEN':
                raise RuntimeError('QA rollback después del borrado')
            return original(**kwargs)
        with patch.object(Auditoria.objects, 'create', side_effect=fail_summary):
            with self.assertRaises(RuntimeError):
                self.run_command('eliminar', **self.monthly_delete_opts(backup))
        self.assertEqual(Equipo.objects.count(), 3)
        self.assertEqual(Auditoria.objects.count(), 0)


from . import test_oficina_gestion as oficina_qa


@override_settings(**oficina_qa.FLAGS)
class DepuracionFlujoMensualTests(oficina_qa.OficinaFixture, TransactionTestCase):
    run_command = DepuracionPruebaTests.run_command
    tools = DepuracionPruebaTests.tools
    backup = DepuracionPruebaTests.backup
    opts = DepuracionPruebaTests.opts
    monthly_delete_opts = DepuracionPruebaTests.monthly_delete_opts

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Sólo PostgreSQL 16 aislado.')
        with patch.object(timezone, 'now', return_value=datetime(2026, 11, 16, tzinfo=ZoneInfo('America/Bogota'))):
            super().setUp()

    def test_inventario_flujo_completo_conserva_inmutables_pdf_fotos_y_otra_empresa(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Sólo PostgreSQL 16 aislado.')
        self.temp = tempfile.TemporaryDirectory(prefix='masiscam-depuracion-flujo-')
        self.addCleanup(self.temp.cleanup)
        with patch.object(timezone, 'now', return_value=datetime(2026, 11, 16, tzinfo=ZoneInfo('America/Bogota'))):
            self.preparar()
            prop = self.cotizar(archivo=None)
            bare = Equipo.objects.create(proyecto=self.equipo.proyecto, cliente=self.cliente,
                                         nombre='Sin expediente QA', numero_serie='QA-ELIMINABLE')
        self.user, self._monthly = self.admin, True
        from .models import Documento, Inspeccion, Diagnostico, PiezaRequerida, PropuestaComercial, RecepcionEquipo
        from .management.commands.depurar_dos_equipos_prueba import inspect_month
        protected = (ExpedienteMantenimiento, RecepcionEquipo, Inspeccion, Diagnostico, PiezaRequerida,
                     PropuestaComercial, Documento)
        original = {model: list(model.objects.order_by('pk').values()) for model in protected}
        pdf = prop.evidencias.get().documento
        pdf_bytes = pdf.archivo.read()
        pdf.archivo.close()
        foreign_company = Empresa.objects.create(nombre='Empresa que se conserva')
        foreign_project = Proyecto.objects.create(empresa=foreign_company, codigo='OTRA', nombre='Otra',
            cliente='Otra', responsable='QA', fecha_inicio='2026-11-16', creado_por=self.admin)
        foreign_equipment = Equipo.objects.create(proyecto=foreign_project, nombre='No tocar')
        plan = inspect_month(self.empresa.pk)
        for model in protected:
            self.assertGreater(plan['registros'][model._meta.label]['cantidad'], 0)
        self.assertTrue(plan['registros']['masiscam.PiezaRequerida']['fecha_no_verificable'])
        self.assertNotIn(foreign_equipment.pk, plan['registros']['masiscam.Equipo']['ids'])
        self.assertIn(bare.pk, plan['eliminaria']['masiscam.Equipo'])
        with patch('masiscam.services.GoogleDriveService', side_effect=AssertionError('No tocar Drive')):
            backup = self.backup()
            result = self.run_command('eliminar', **self.monthly_delete_opts(backup))
        self.assertFalse(result['limpieza_fisica_integral'])
        self.assertFalse(Equipo.objects.filter(pk=bare.pk).exists())
        self.assertTrue(Equipo.objects.filter(pk=foreign_equipment.pk).exists())
        self.assertTrue(Equipo.objects.filter(pk=self.equipo.pk).exists())
        for model, rows in original.items():
            self.assertEqual(list(model.objects.order_by('pk').values()), rows)
        pdf.refresh_from_db()
        self.assertEqual(pdf.archivo.read(), pdf_bytes)
        pdf.archivo.close()


@override_settings(**oficina_qa.FLAGS)
class DepuracionConfirmadaTests(oficina_qa.OficinaFixture, TransactionTestCase):
    reset_sequences = True
    run_command = DepuracionPruebaTests.run_command
    tools = DepuracionPruebaTests.tools

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Sólo PostgreSQL 16 aislado.')
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix='masiscam-confirmados-')
        self.addCleanup(self.temp.cleanup)
        self.folder = str(Path(self.temp.name) / 'custodia')
        with connection.cursor() as cursor:
            cursor.execute("SELECT setval(pg_get_serial_sequence('masiscam_registroequipo','id'),22,false)")
        self.preparar()
        self.cotizar(archivo=None)
        self.assertEqual(self.exp.pk, 1)
        self.assertEqual(self.exp.registro_id, 22)
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        photo = BytesIO()
        Image.new('RGB', (40, 30), 'red').save(photo, 'PNG')
        with patch('masiscam.test_taller.foto', side_effect=lambda: SimpleUploadedFile(
                'recepcion-23.png', photo.getvalue(), content_type='image/png')):
            self.cargar()
        self.componente()
        self.assertEqual(self.exp.pk, 2)
        self.assertEqual(self.exp.registro_id, 23)
        photo.seek(0)
        photo.truncate()
        Image.new('RGB', (40, 30), 'orange').save(photo, 'PNG')
        with patch('masiscam.test_taller.foto', side_effect=lambda: SimpleUploadedFile(
                'recepcion-conservada.png', photo.getvalue(), content_type='image/png')):
            self.other_exp = self.recepcion(self.crear('FUERA-ALCANCE'))
        self.assertEqual(self.other_exp.pk, 3)

    def options(self):
        return dict(confirmados=True, carpeta=self.folder, usuario=self.admin.pk)

    def verified(self):
        self.run_command('simular', **self.options())
        dump, restore = self.tools()
        path = Path(self.temp.name) / 'respaldo.dump'
        cfg = connection.settings_dict
        env = os.environ.copy()
        env.update(PGHOST=cfg['HOST'], PGPORT=str(cfg['PORT']), PGUSER=cfg['USER'],
                   PGPASSWORD=cfg['PASSWORD'], PGDATABASE=cfg['NAME'])
        subprocess.run([str(dump), '-Fc', '--file=' + str(path)], env=env,
                       capture_output=True, check=True, timeout=60)
        with patch.object(type(self), 'databases', self.databases | {'depuracion_admin', 'depuracion_restore'}):
            return self.run_command('verificar-respaldo', **self.options(), respaldo=str(path),
                qa_url=os.environ['MASISCAM_TEST_POSTGRES'], pg_restore=str(restore))

    def test_respaldo_real_borrado_exacto_constancia_guardas_y_documentos(self):
        from .models import Documento, ReferenciaTecnica, ContadorExpediente
        from .management.depuracion_confirmada import guards, read_signed
        from django.db import transaction, IntegrityError
        from django.db.models.deletion import ProtectedError
        equipos = list(Equipo.objects.order_by('pk').values())
        users = list(get_user_model().objects.order_by('pk').values())
        roles = list(RolMasiscam.objects.order_by('pk').values())
        docs = list(Documento.objects.order_by('pk').values())
        counter = ContadorExpediente.objects.get(empresa=self.empresa).ultimo_numero
        with self.assertRaises(ProtectedError):
            RegistroEquipo.objects.filter(pk__in=[22, 23]).delete()
        receipt = self.verified()
        self.assertTrue(receipt['limpieza_probada_pg16'])
        self.assertTrue(ExpedienteMantenimiento.objects.filter(pk=1).exists())
        plan = read_signed(Path(self.folder) / 'plan.json')['plan']
        self.assertFalse(plan['bloqueos'])
        self.assertTrue(any(d['on_delete_django'] == 'PROTECT' for d in plan['dependencias']))
        tables = {r['tabla'] for r in plan['eliminar'] + plan['desvincular']}
        before = fingerprint(guards(tables, 'default'))
        with patch('masiscam.services.GoogleDriveService', side_effect=AssertionError('No Drive')):
            result = self.run_command('eliminar', **self.options(), confirmar='ELIMINAR PRUEBAS CONFIRMADAS')
        self.assertEqual(result['registros_eliminados'], [22, 23])
        self.assertFalse(RegistroEquipo.objects.filter(pk__in=[22, 23]).exists())
        self.assertFalse(ExpedienteMantenimiento.objects.filter(pk__in=[1, 2]).exists())
        self.assertTrue(ExpedienteMantenimiento.objects.filter(pk=3).exists())
        self.assertTrue(RegistroEquipo.objects.filter(pk=self.other_exp.registro_id).exists())
        self.assertEqual(list(Equipo.objects.order_by('pk').values()), equipos)
        self.assertEqual(list(get_user_model().objects.order_by('pk').values()), users)
        self.assertEqual(list(RolMasiscam.objects.order_by('pk').values()), roles)
        self.assertEqual(list(Documento.objects.order_by('pk').values()), docs)
        self.assertEqual(ContadorExpediente.objects.get(empresa=self.empresa).ultimo_numero, counter)
        self.assertEqual(fingerprint(guards(tables, 'default')), before)
        self.ref.refresh_from_db()
        self.assertIsNone(self.ref.expediente_trabajo_id)
        self.assertTrue(ReferenciaTecnica.objects.filter(pk=self.ref.pk).exists())
        event = Auditoria.objects.get(accion='DEPURACION_CONFIRMADA_22_23')
        self.assertTrue(read_signed(event.detalle['ruta_custodia'])['plan']['eliminar'])
        self.assertFalse(Auditoria.objects.filter(expediente_id__in=[1, 2]).exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM masiscam_expedientemantenimiento WHERE id=3')
        with self.assertRaises(CommandError):
            self.run_command('eliminar', **self.options(), confirmar='ELIMINAR PRUEBAS CONFIRMADAS')

    def test_constancia_previa_y_rollback_integro_si_falla_auditoria(self):
        from .management.depuracion_confirmada import guards, inspect_confirmed
        self.verified()
        plan = inspect_confirmed()
        tables = {r['tabla'] for r in plan['eliminar'] + plan['desvincular']}
        before = fingerprint(guards(tables, 'default'))
        with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('QA fallo al final')):
            with self.assertRaises(RuntimeError):
                self.run_command('eliminar', **self.options(), confirmar='ELIMINAR PRUEBAS CONFIRMADAS')
        self.assertEqual(fingerprint(inspect_confirmed()), fingerprint(plan))
        self.assertEqual(fingerprint(guards(tables, 'default')), before)
        self.assertTrue((Path(self.folder) / 'custodia-previa.json').exists())

    def test_alcance_fijo_sin_otros_ids_y_rechazo_sin_respaldo(self):
        with self.assertRaises(CommandError):
            self.run_command('simular', **self.options(), ids=[24, 25])
        with self.assertRaises(CommandError):
            self.run_command('simular', **self.options(), mes='2026-11', empresa=self.empresa.pk)
        self.run_command('simular', **self.options())
        with self.assertRaises(FileNotFoundError):
            self.run_command('eliminar', **self.options(), confirmar='ELIMINAR PRUEBAS CONFIRMADAS')

    def test_guardas_solo_permiten_filas_firmadas_incluso_dentro_de_excepcion(self):
        from .management.depuracion_confirmada import scoped_guards, inspect_confirmed
        from django.db import transaction, IntegrityError
        plan = inspect_confirmed()
        self.assertFalse(plan['bloqueos'])
        with transaction.atomic():
            with scoped_guards(plan, 'default'):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM masiscam_expedientemantenimiento WHERE id=3')
            transaction.set_rollback(True)
        self.assertTrue(ExpedienteMantenimiento.objects.filter(pk=3).exists())

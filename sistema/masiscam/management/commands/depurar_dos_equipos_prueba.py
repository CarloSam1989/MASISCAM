"""Inspección por defecto; nunca desactiva protecciones históricas ni toca Drive."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction
from django.db.models import FileField
from django.utils import timezone

from masiscam.models import Auditoria, Equipo, ExpedienteMantenimiento, RolMasiscam


def canonical(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, ensure_ascii=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inspect(empresa=None, using='default'):
    qs = Equipo.objects.using(using).select_related('proyecto__empresa', 'cliente')
    if empresa:
        qs = qs.filter(proyecto__empresa_id=empresa)
    equipos = list(qs.order_by('-creado_en', '-pk')[:2])
    result = {'empresa_filtro': empresa, 'equipos': [], 'bloqueos': []}
    for equipo in equipos:
        relaciones = {}
        for rel in Equipo._meta.related_objects:
            if not (rel.one_to_many or rel.one_to_one):
                result['bloqueos'].append(f'Equipo {equipo.pk}: relación no soportada {rel.name}.')
                continue
            rows = list(rel.related_model.objects.using(using).filter(
                **{rel.field.attname: equipo.pk}).order_by('pk').values())
            if rows:
                relaciones[rel.related_model._meta.label] = rows
                result['bloqueos'].append(f'Equipo {equipo.pk}: conserva {len(rows)} {rel.related_model._meta.label}.')
        expedientes = list(ExpedienteMantenimiento.objects.using(using).filter(
            registro__equipo_id=equipo.pk).order_by('pk').values())
        # Detalle del expediente: mostrar cada relación, sin recolectar ni borrar cascadas.
        detalle_expedientes = []
        for exp in expedientes:
            detalle = {'datos': exp, 'relaciones': {}}
            for rel in ExpedienteMantenimiento._meta.related_objects:
                if rel.one_to_many or rel.one_to_one:
                    detalle['relaciones'][rel.related_model._meta.label] = list(
                        rel.related_model.objects.using(using).filter(
                            **{rel.field.attname: exp['id']}).order_by('pk').values())
            detalle_expedientes.append(detalle)
        archivos = {f.name: str(getattr(equipo, f.name)) for f in Equipo._meta.fields
                    if isinstance(f, FileField) and getattr(equipo, f.name)}
        if archivos or equipo.drive_folder_id or equipo.drive_folder_url:
            result['bloqueos'].append(f'Equipo {equipo.pk}: archivos o vínculo Drive; conservar.')
        result['equipos'].append({
            'id': equipo.pk, 'empresa_id': equipo.proyecto.empresa_id,
            'empresa': equipo.proyecto.empresa.nombre, 'proyecto_id': equipo.proyecto_id,
            'cliente_id': equipo.cliente_id,
            'cliente': str(equipo.cliente) if equipo.cliente_id else equipo.proyecto.cliente,
            'equipo': equipo.nombre, 'serie': equipo.numero_serie, 'fecha': equipo.creado_en,
            'datos': {f.attname: str(f.value_from_object(equipo)) if isinstance(f, FileField)
                      else f.value_from_object(equipo) for f in Equipo._meta.concrete_fields},
            'relaciones_conservadas': relaciones, 'expedientes_inmutables': detalle_expedientes,
            'archivos_conservados': archivos,
        })
    if len(equipos) != 2:
        result['bloqueos'].append('No existen exactamente dos candidatos recientes.')
    result['eliminaria'] = [] if result['bloqueos'] else [e.pk for e in equipos]
    result['conserva'] = 'Clientes, proyectos, empresas, usuarios, configuración, auditorías, archivos y Drive.'
    return json.loads(canonical(result))


def source_identity(using='default'):
    with connections[using].cursor() as cursor:
        cursor.execute('SELECT current_database(), inet_server_addr()::text, inet_server_port(), pg_postmaster_start_time()')
        return json.loads(canonical(list(cursor.fetchone())))


class Command(BaseCommand):
    help = 'Consulta los dos equipos más recientes; simula y bloquea borrados con cualquier relación o archivo.'

    def add_arguments(self, parser):
        parser.add_argument('accion', choices=['consultar', 'simular', 'verificar-respaldo', 'eliminar'])
        parser.add_argument('--empresa', type=int)
        parser.add_argument('--ids', nargs=2, type=int)
        parser.add_argument('--usuario', type=int)
        parser.add_argument('--huella')
        parser.add_argument('--respaldo')
        parser.add_argument('--qa-url', help='PostgreSQL 16 desechable en loopback; nunca producción.')
        parser.add_argument('--pg-restore', default='pg_restore')
        parser.add_argument('--salida', help='Constancia nueva de restauración verificada; no sobrescribe.')
        parser.add_argument('--verificacion')
        parser.add_argument('--confirmar')
        parser.add_argument('--motivo')

    def selected(self, plan, opts):
        ids = opts['ids']
        if not ids or len(set(ids)) != 2 or set(ids) != {e['id'] for e in plan['equipos']}:
            raise CommandError('Seleccione exactamente los dos IDs recientes mostrados por consultar.')
        if not opts['huella'] or opts['huella'] != fingerprint(plan):
            raise CommandError('Identidad o relaciones cambiaron: consulte y verifique de nuevo la huella.')

    def actor(self, plan, opts):
        user = get_user_model().objects.filter(pk=opts['usuario'], is_active=True).first()
        if user is None:
            raise CommandError('Indique --usuario de un ADMINISTRADOR activo.')
        for empresa in {e['empresa_id'] for e in plan['equipos']}:
            if not RolMasiscam.objects.filter(perfil__user=user, perfil__empresa_id=empresa,
                    perfil__activo=True, perfil__empresa__activa=True, activo=True, rol='ADMIN').exists():
                raise CommandError('Se requiere ADMINISTRADOR activo en cada empresa seleccionada.')
        return user

    def handle(self, *args, **opts):
        if connections['default'].vendor != 'postgresql':
            raise CommandError('Este procedimiento requiere PostgreSQL.')
        if opts['accion'] == 'eliminar':
            return self.delete(opts)
        # Consulta y simulación no escriben en la base, ni siquiera auditorías.
        with transaction.atomic():
            with connections['default'].cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            plan = inspect(opts['empresa'])
            source = source_identity()
            if opts['accion'] != 'consultar':
                self.selected(plan, opts)
                self.actor(plan, opts)
        if opts['accion'] == 'verificar-respaldo':
            return self.verify_backup(plan, source, opts)
        self.stdout.write(canonical({'origen': source, 'huella': fingerprint(plan), 'simulacion': plan}))

    def verify_backup(self, plan, source, opts):
        import environ
        if not all(opts[k] for k in ('respaldo', 'qa_url', 'salida')):
            raise CommandError('Exija --respaldo, --qa-url y --salida nuevos para restauración aislada.')
        cfg = environ.Env.db_url_config(opts['qa_url'])
        if (cfg['ENGINE'] != 'django.db.backends.postgresql' or
                cfg['HOST'] not in {'localhost', '127.0.0.1', '::1'} or
                not cfg['NAME'].startswith('masiscam_qa_')):
            raise CommandError('--qa-url debe identificar PostgreSQL desechable masiscam_qa_* en loopback.')
        if Path(opts['salida']).exists():
            raise CommandError('La constancia ya existe; no se sobrescribe.')
        before = file_hash(opts['respaldo'])
        name = 'masiscam_qa_restore_' + uuid.uuid4().hex
        admin_alias, restored_alias = 'depuracion_admin', 'depuracion_restore'
        base = connections['default'].settings_dict.copy()
        base.update(cfg)
        base.update(OPTIONS={}, CONN_MAX_AGE=0)
        connections.databases[admin_alias] = {**base, 'NAME': 'postgres'}
        connections.databases[restored_alias] = {**base, 'NAME': name}
        created = False
        try:
            with connections[admin_alias].cursor() as cursor:
                cursor.execute("SELECT current_setting('server_version_num')::int, inet_server_port(), pg_postmaster_start_time()")
                version, port, started = cursor.fetchone()
                if not 160000 <= version < 170000:
                    raise CommandError('La restauración aislada requiere PostgreSQL 16.')
                if ([port, json.loads(canonical(started))] == source[2:]
                        and not source[0].startswith(('masiscam_qa_', 'test_masiscam_qa_'))):
                    raise CommandError('QA debe usar otra instancia PostgreSQL; nunca el clúster de producción.')
                cursor.execute('CREATE DATABASE ' + connections[admin_alias].ops.quote_name(name))
                created = True
            env = os.environ.copy()
            env.update(PGHOST=cfg['HOST'], PGPORT=str(cfg.get('PORT') or 5432),
                       PGUSER=cfg.get('USER', ''), PGPASSWORD=cfg.get('PASSWORD', ''), PGDATABASE=name)
            result = subprocess.run([opts['pg_restore'], '--exit-on-error', '--no-owner', '--no-privileges',
                                     '--dbname=' + name, str(Path(opts['respaldo']).resolve())],
                                    env=env, capture_output=True, timeout=300)
            if result.returncode:
                raise CommandError('Falló pg_restore en QA: no se verifica el respaldo ni se permite borrar.')
            with transaction.atomic(using=restored_alias):
                with connections[restored_alias].cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                restored = inspect(opts['empresa'], using=restored_alias)
            if fingerprint(restored) != fingerprint(plan) or file_hash(opts['respaldo']) != before:
                raise CommandError('El respaldo restaurado no coincide con los candidatos y sus relaciones.')
            receipt = {'origen': source, 'huella': fingerprint(plan), 'sha256_respaldo': before,
                       'restauracion_pg16': True, 'verificado_en': timezone.now().isoformat(),
                       'verificado_por': opts['usuario'], 'ids': opts['ids']}
            receipt['firma'] = signing.dumps(receipt, salt='masiscam.depuracion.respaldo')
            with Path(opts['salida']).open('x', encoding='utf-8') as stream:
                stream.write(canonical(receipt))
            self.stdout.write(canonical(receipt))
        finally:
            connections[restored_alias].close()
            if created:
                with connections[admin_alias].cursor() as cursor:
                    cursor.execute('DROP DATABASE ' + connections[admin_alias].ops.quote_name(name))
            connections[admin_alias].close()
            del connections[admin_alias]
            del connections[restored_alias]
            del connections.databases[admin_alias]
            del connections.databases[restored_alias]

    def delete(self, opts):
        if not all(opts[k] for k in ('respaldo', 'verificacion', 'motivo', 'confirmar')):
            raise CommandError('Faltan respaldo restaurado, constancia, motivo o confirmación explícita.')
        receipt = json.loads(Path(opts['verificacion']).read_text(encoding='utf-8'))
        try:
            verified = signing.loads(receipt.get('firma', ''), salt='masiscam.depuracion.respaldo')
        except signing.BadSignature as exc:
            raise CommandError('Constancia de restauración sin firma válida.') from exc
        if verified != {k: v for k, v in receipt.items() if k != 'firma'}:
            raise CommandError('Constancia de restauración alterada.')
        backup_hash = file_hash(opts['respaldo'])
        with transaction.atomic():
            # Congelar altas/modificaciones y relaciones directas durante la comprobación y el borrado.
            tables = {Equipo._meta.db_table, *(r.related_model._meta.db_table for r in Equipo._meta.related_objects),
                      get_user_model()._meta.db_table, RolMasiscam._meta.db_table,
                      RolMasiscam._meta.get_field('perfil').related_model._meta.db_table,
                      *(f.related_model._meta.db_table for f in Equipo._meta.fields if f.is_relation),
                      Auditoria._meta.get_field('empresa').related_model._meta.db_table}
            with connections['default'].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute('LOCK TABLE ' + ', '.join(connections['default'].ops.quote_name(t)
                               for t in sorted(tables)) + ' IN SHARE ROW EXCLUSIVE MODE')
            plan = inspect(opts['empresa'])
            self.selected(plan, opts)
            user = self.actor(plan, opts)
            if plan['bloqueos']:
                raise CommandError('Borrado bloqueado: ' + ' '.join(plan['bloqueos']))
            if (receipt.get('restauracion_pg16') is not True or receipt.get('origen') != source_identity()
                    or receipt.get('huella') != fingerprint(plan)
                    or receipt.get('sha256_respaldo') != backup_hash
                    or set(receipt.get('ids', [])) != set(opts['ids'])):
                raise CommandError('Respaldo/constancia no corresponden a esta base y simulación.')
            expected = 'ELIMINAR ' + ','.join(map(str, sorted(opts['ids']))) + ' ' + fingerprint(plan)
            if opts['confirmar'] != expected or not opts['motivo'].strip():
                raise CommandError('Confirme exactamente: ' + expected + ' ; indique motivo no vacío.')
            for equipo in plan['equipos']:
                Auditoria.objects.create(empresa_id=equipo['empresa_id'], proyecto_id=equipo['proyecto_id'],
                    usuario=user, accion='ELIMINAR_EQUIPO_PRUEBA', objeto_tipo='Equipo', objeto_id=str(equipo['id']),
                    detalle={'identidad': equipo, 'huella': fingerprint(plan), 'respaldo': receipt,
                             'motivo': opts['motivo'], 'confirmacion': opts['confirmar']})
            count, breakdown = Equipo.objects.filter(pk__in=opts['ids']).delete()
            if count != 2 or breakdown != {'masiscam.Equipo': 2}:
                raise CommandError('Cascada inesperada: transacción revertida íntegramente.')
        self.stdout.write(canonical({'eliminados': sorted(opts['ids']), 'auditorias_creadas': 2}))

"""Inspección por defecto; nunca desactiva protecciones históricas ni toca Drive."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction
from django.db.models import FileField
from django.utils import timezone

from masiscam.models import Auditoria, Equipo, ExpedienteMantenimiento, RolMasiscam


def company_path(model):
    """Ruta de propiedad; nunca inferir empresa a través de un usuario o cliente."""
    if model is RolMasiscam:
        return 'perfil__empresa_id'
    pending = [(model, '', frozenset())]
    while pending:
        current, prefix, visited = pending.pop(0)
        if current in visited:
            continue
        fields = {f.name: f for f in current._meta.fields}
        if 'empresa' in fields and fields['empresa'].is_relation:
            return prefix + 'empresa_id'
        for field in current._meta.fields:
            if (field.is_relation and not field.null and field.related_model._meta.app_label == 'masiscam'
                    and field.name not in {'cliente', 'cliente_contractual', 'sustituye', 'componente'}):
                pending.append((field.related_model, prefix + field.name + '__', visited | {current}))
    return None


def inspect_month(empresa, using='default'):
    """Inventario integral; sólo borrar raíces mutables cuya fecha se pueda probar."""
    from accounts.models import Empresa
    company = Empresa._base_manager.using(using).get(pk=empresa)
    zone = ZoneInfo('America/Bogota')
    start, end = datetime(2026, 11, 1, tzinfo=zone), datetime(2026, 12, 1, tzinfo=zone)
    result = {'mes': '2026-11', 'zona_horaria': str(zone), 'empresa_filtro': empresa,
              'empresa': company.nombre, 'equipos': [], 'registros': {}, 'eliminaria': {},
              'conservados': {}, 'sin_ruta_empresa': [], 'bloqueos': [],
              'politica': 'Sin bypass de inmutabilidad. Conservar documentos, archivos, QR activos, Drive y auditoría.'}
    mutable = {Equipo._meta.label, 'masiscam.RegistroEquipo'}
    candidates, records, models = set(), {}, {}
    for model in sorted(apps.get_app_config('masiscam').get_models(), key=lambda m: m._meta.label):
        label = model._meta.label
        path = company_path(model)
        if not path:
            result['sin_ruta_empresa'].append(label)
            continue
        created = next((f for f in model._meta.fields if getattr(f, 'auto_now_add', False)), None)
        qs = model._base_manager.using(using).filter(**{path: empresa})
        if created:
            qs = qs.filter(**{created.name + '__gte': start, created.name + '__lt': end})
        rows = list(qs.order_by('pk').values()[:20001])
        if len(rows) > 20000:
            raise CommandError(f'Inventario {label} demasiado grande; no se permite truncar una limpieza.')
        result['registros'][label] = {'cantidad': len(rows), 'ids': [row['id'] for row in rows],
            'fecha_registro': created.name if created else None,
            'fecha_no_verificable': not bool(created), 'datos': rows,
            'dependencias': {str(row['id']): {f.name: {'modelo': f.related_model._meta.label,
                'id': row[f.attname]} for f in model._meta.fields
                if f.is_relation and row.get(f.attname) is not None} for row in rows}}
        for row in rows:
            key = (label, row['id'])
            records[key], models[key] = row, model
            reason = None
            if not created:
                reason = 'Fecha de creación no verificable: no inferir antigüedad de su padre.'
            elif label not in mutable:
                reason = 'Conservar historial inmutable, documentos/archivos, configuración o auditoría.'
            elif (any(row.get(f.name) for f in model._meta.fields if isinstance(f, FileField))
                    or any(value for name, value in row.items() if name.startswith('drive_') and name != 'drive_error')
                    or row.get('consulta_publica_activa')):
                reason = 'Conservar archivos, referencias Drive o QR público activo y su registro.'
            if reason:
                result['conservados'].setdefault(label, {})[str(row['id'])] = reason
            else:
                candidates.add(key)
    # Toda referencia entrante retenida conserva también su padre. Nunca cascadas implícitas.
    changed = True
    while changed:
        changed = False
        for key in sorted(candidates):
            dependencies = []
            for rel in models[key]._meta.get_fields(include_hidden=True):
                if rel.auto_created and not rel.concrete and (rel.one_to_many or rel.one_to_one):
                    for pk in rel.related_model._base_manager.using(using).filter(
                            **{rel.field.attname: key[1]}).values_list('pk', flat=True):
                        dependency = (rel.related_model._meta.label, pk)
                        if dependency not in candidates:
                            dependencies.append({'modelo': dependency[0], 'id': pk})
            if dependencies:
                candidates.remove(key)
                result['conservados'].setdefault(key[0], {})[str(key[1])] = {
                    'motivo': 'Dependencia conservada, anterior, externa o compartida.', 'dependencias': dependencies}
                changed = True
    for label, pk in sorted(candidates):
        result['eliminaria'].setdefault(label, []).append(pk)
    result['cantidades'] = {'identificados': sum(v['cantidad'] for v in result['registros'].values()),
        'eliminar': len(candidates), 'conservar': len(records) - len(candidates)}
    return json.loads(canonical(result))


def inspect_plan(opts, using='default'):
    if opts.get('confirmados'):
        from masiscam.management.depuracion_confirmada import inspect_confirmed
        return inspect_confirmed(using)
    if opts.get('completo'):
        from masiscam.management.depuracion_mensual import inspect_month_full
        return inspect_month_full(opts['empresa'], using)
    return inspect_month(opts['empresa'], using) if opts.get('mes') else inspect(opts['empresa'], using)


def apply_month_rows(plan, using='default'):
    """Mismo borrado en QA (con rollback) y ejecución; sin SQL que eluda guardas."""
    deleted = {}
    for label in ('masiscam.RegistroEquipo', 'masiscam.Equipo'):
        ids = plan['eliminaria'].get(label, [])
        if ids:
            count, breakdown = apps.get_model(label)._base_manager.using(using).filter(pk__in=ids).delete()
            if count != len(ids) or breakdown != {label: len(ids)}:
                raise CommandError('Cascada inesperada: limpieza revertida íntegramente.')
            deleted[label] = ids
    for label, retained in plan['conservados'].items():
        previous = [row for row in plan['registros'][label]['datos'] if str(row['id']) in retained]
        current = list(apps.get_model(label)._base_manager.using(using).filter(
            pk__in=[row['id'] for row in previous]).order_by('pk').values())
        if fingerprint(current) != fingerprint(previous):
            raise CommandError('Integridad de registros conservados alterada: transacción revertida.')
    return deleted


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
    qs = Equipo._base_manager.using(using).select_related('proyecto__empresa', 'cliente')
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
            rows = list(rel.related_model._base_manager.using(using).filter(
                **{rel.field.attname: equipo.pk}).order_by('pk').values())
            if rows:
                relaciones[rel.related_model._meta.label] = rows
                result['bloqueos'].append(f'Equipo {equipo.pk}: conserva {len(rows)} {rel.related_model._meta.label}.')
        expedientes = list(ExpedienteMantenimiento._base_manager.using(using).filter(
            registro__equipo_id=equipo.pk).order_by('pk').values())
        # Detalle del expediente: mostrar cada relación, sin recolectar ni borrar cascadas.
        detalle_expedientes = []
        for exp in expedientes:
            detalle = {'datos': exp, 'relaciones': {}}
            for rel in ExpedienteMantenimiento._meta.related_objects:
                if rel.one_to_many or rel.one_to_one:
                    detalle['relaciones'][rel.related_model._meta.label] = list(
                        rel.related_model._base_manager.using(using).filter(
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
    help = 'Depuración segura de dos equipos o inventario de noviembre 2026 por empresa, conservando protecciones.'

    def add_arguments(self, parser):
        parser.add_argument('accion', choices=['consultar', 'simular', 'verificar-respaldo', 'eliminar', 'retirar'])
        parser.add_argument('--empresa', type=int)
        parser.add_argument('--confirmados', action='store_true', help='Perfil fijo RegistroEquipo 22/23 y expediente 1/2.')
        parser.add_argument('--carpeta', help='Custodia local del perfil confirmado; nunca se sobrescriben constancias.')
        parser.add_argument('--operador', help='Nombre de usuario ADMINISTRADOR; no requiere introducir IDs.')
        parser.add_argument('--mes', choices=['2026-11'], help='Alcance autorizado: noviembre de 2026; exige empresa.')
        parser.add_argument('--completo', action='store_true', help='Con --mes: elimina también documentos y PDF exclusivos.')
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
        if opts.get('confirmados'):
            if opts['accion'] in {'consultar', 'simular'}:
                return
            if opts['huella'] != fingerprint(plan):
                raise CommandError('Cambió el perfil confirmado: vuelva a simular en una carpeta nueva.')
            return
        if opts.get('mes'):
            if opts['ids']:
                raise CommandError('--mes usa el inventario completo, no --ids de dos equipos.')
            if opts['accion'] in {'consultar', 'simular'} and not opts['huella']:
                return
            if opts['huella'] != fingerprint(plan):
                raise CommandError('Cambió el inventario mensual; repita simulación y respaldo verificado.')
            return
        ids = opts['ids']
        if not ids or len(set(ids)) != 2 or set(ids) != {e['id'] for e in plan['equipos']}:
            raise CommandError('Seleccione exactamente los dos IDs recientes mostrados por consultar.')
        if not opts['huella'] or opts['huella'] != fingerprint(plan):
            raise CommandError('Identidad o relaciones cambiaron: consulte y verifique de nuevo la huella.')

    def actor(self, plan, opts):
        user = get_user_model().objects.filter(pk=opts['usuario'], is_active=True).first()
        if user is None:
            raise CommandError('Indique --usuario de un ADMINISTRADOR activo.')
        empresas = {plan['empresa_filtro']} if opts.get('mes') else {e['empresa_id'] for e in plan['equipos']}
        for empresa in empresas:
            if not RolMasiscam.objects.filter(perfil__user=user, perfil__empresa_id=empresa,
                    perfil__activo=True, perfil__empresa__activa=True, activo=True, rol='ADMIN').exists():
                raise CommandError('Se requiere ADMINISTRADOR activo en cada empresa seleccionada.')
        return user

    def handle(self, *args, **opts):
        if connections['default'].vendor != 'postgresql':
            raise CommandError('Este procedimiento requiere PostgreSQL.')
        if opts['accion'] == 'retirar':
            from masiscam.retiro_operativo import retirar_confirmados
            self.stdout.write(canonical(retirar_confirmados(opts)))
            return
        if opts.get('completo') and (not opts.get('mes') or opts.get('confirmados') or opts.get('ids')):
            raise CommandError('--completo exige --mes 2026-11 y no admite --confirmados ni --ids.')
        if opts.get('completo') and opts['accion'] in {'verificar-respaldo', 'eliminar'} and not opts.get('carpeta'):
            raise CommandError('--completo exige --carpeta de custodia para las copias de los archivos.')
        if opts.get('mes') and not opts['empresa']:
            raise CommandError('--mes exige --empresa explícita; no se permite limpieza global.')
        if opts.get('confirmados'):
            self.prepare_confirmed(opts)
        if opts['accion'] == 'eliminar':
            return self.delete(opts)
        # Consulta y simulación no escriben en la base, ni siquiera auditorías.
        with transaction.atomic():
            with connections['default'].cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            plan = inspect_plan(opts)
            source = source_identity()
            if opts['accion'] != 'consultar' or opts.get('mes'):
                self.selected(plan, opts)
                self.actor(plan, opts)
        if opts['accion'] == 'verificar-respaldo':
            return self.verify_backup(plan, source, opts)
        if opts.get('confirmados'):
            from masiscam.management.depuracion_confirmada import save_signed, read_signed
            payload = {'perfil': plan['perfil'], 'origen': source, 'plan': plan, 'usuario': opts['usuario']}
            path = Path(opts['carpeta']) / 'plan.json'
            if path.exists():
                if fingerprint(read_signed(path)) != fingerprint(payload):
                    raise CommandError('Ya existe otra constancia; use --carpeta con un directorio nuevo.')
            else:
                save_signed(path, payload)
        self.stdout.write(canonical({'origen': source, 'huella': fingerprint(plan), 'simulacion': plan}))

    def prepare_confirmed(self, opts):
        from django.conf import settings
        from masiscam.management.depuracion_confirmada import read_signed, PROFILE
        if opts['ids'] or opts.get('mes'):
            raise CommandError('--confirmados no admite cambiar IDs ni ampliar el alcance por mes.')
        opts['carpeta'] = str(Path(opts['carpeta'] or settings.BASE_DIR / 'backups/depuracion-confirmados-22-23').resolve())
        folder = Path(opts['carpeta'])
        if opts['accion'] not in {'consultar', 'simular'}:
            saved = read_signed(folder / 'plan.json')
            if saved['perfil'] != PROFILE or saved['origen'] != source_identity():
                raise CommandError('La constancia pertenece a otra base o perfil.')
            empresa = saved['plan']['empresa_filtro']
            if opts['empresa'] and opts['empresa'] != empresa:
                raise CommandError('No se puede cambiar la empresa del perfil confirmado.')
            opts['empresa'], opts['huella'] = empresa, fingerprint(saved['plan'])
            opts['usuario'] = opts['usuario'] or saved['usuario']
            if opts['accion'] == 'verificar-respaldo':
                opts['respaldo'] = opts['respaldo'] or input('Ruta del respaldo PostgreSQL completo (.dump): ').strip()
                opts['qa_url'] = opts['qa_url'] or os.environ.get('MASISCAM_DEPURACION_QA_URL') or input('URL PostgreSQL 16 aislado (masiscam_qa_* en loopback): ').strip()
                opts['salida'] = opts['salida'] or str(folder / 'verificacion.json')
                if opts['pg_restore'] == 'pg_restore':
                    import shutil
                    local = settings.BASE_DIR / '.venv-rbac-pg/pgsql/bin/pg_restore.exe'
                    opts['pg_restore'] = shutil.which('pg_restore') or (str(local) if local.exists() else 'pg_restore')
            else:
                opts['verificacion'] = opts['verificacion'] or str(folder / 'verificacion.json')
                receipt = json.loads(Path(opts['verificacion']).read_text(encoding='utf-8'))
                opts['respaldo'] = opts['respaldo'] or receipt.get('ruta_respaldo')
                opts['motivo'] = opts['motivo'] or 'Borrado excepcional de pruebas confirmadas por el propietario: registros 22/23 y expedientes 1/2.'
                opts['confirmar'] = opts['confirmar'] or input('Escriba ELIMINAR PRUEBAS CONFIRMADAS para ejecutar: ').strip()
        if not opts['usuario']:
            username = opts['operador'] or input('Usuario ADMINISTRADOR responsable: ').strip()
            user = get_user_model().objects.filter(username=username, is_active=True).first()
            if user is None:
                raise CommandError('Usuario responsable no encontrado o inactivo.')
            opts['usuario'] = user.pk

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
                restored = inspect_plan(opts, using=restored_alias)
            if fingerprint(restored) != fingerprint(plan) or file_hash(opts['respaldo']) != before:
                raise CommandError('El respaldo restaurado no coincide con los candidatos y sus relaciones.')
            assets = []
            if opts.get('confirmados'):
                from masiscam.management.depuracion_confirmada import apply_confirmed, archive_assets
                if plan['bloqueos']:
                    raise CommandError('Dependencias no autorizadas: ' + ' '.join(plan['bloqueos']))
                assets = archive_assets(plan, opts['carpeta'])
                with transaction.atomic(using=restored_alias):
                    apply_confirmed(restored, using=restored_alias)
                    transaction.set_rollback(True, using=restored_alias)
                if fingerprint(inspect_plan(opts, using=restored_alias)) != fingerprint(plan):
                    raise CommandError('El ensayo de limpieza no revirtió exactamente la copia restaurada.')
            elif opts.get('completo'):
                from masiscam.management.depuracion_mensual import apply_month_full, archive_files
                if plan['bloqueos']:
                    raise CommandError('Dependencias no autorizadas: ' + ' '.join(plan['bloqueos']))
                assets, missing = archive_files(plan, opts['carpeta'])
                with transaction.atomic(using=restored_alias):
                    apply_month_full(restored, using=restored_alias)
                    transaction.set_rollback(True, using=restored_alias)
                if fingerprint(inspect_plan(opts, using=restored_alias)) != fingerprint(plan):
                    raise CommandError('Falló el rollback de la simulación completa sobre el respaldo restaurado.')
            elif opts.get('mes'):
                # Ejercitar las guardas reales en la copia restaurada y revertir todos los cambios.
                with transaction.atomic(using=restored_alias):
                    apply_month_rows(restored, using=restored_alias)
                    transaction.set_rollback(True, using=restored_alias)
                if fingerprint(inspect_plan(opts, using=restored_alias)) != fingerprint(plan):
                    raise CommandError('Falló el rollback de la simulación sobre el respaldo restaurado.')
            receipt = {'origen': source, 'huella': fingerprint(plan), 'sha256_respaldo': before,
                       'restauracion_pg16': True, 'verificado_en': timezone.now().isoformat(),
                       'verificado_por': opts['usuario'], 'ids': opts['ids'] or [],
                       'mes': opts.get('mes'), 'empresa': opts['empresa'],
                       'limpieza_probada_pg16': bool(opts.get('mes') or opts.get('confirmados')),
                       'completo': bool(opts.get('completo')),
                       'perfil_confirmado': plan.get('perfil'), 'archivos_locales': assets,
                       'ruta_respaldo': str(Path(opts['respaldo']).resolve())}
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
            if opts.get('mes') or opts.get('confirmados'):
                tables.update(model._meta.db_table for model in apps.get_app_config('masiscam').get_models())
            with connections['default'].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute('LOCK TABLE ' + ', '.join(connections['default'].ops.quote_name(t)
                               for t in sorted(tables)) + ' IN SHARE ROW EXCLUSIVE MODE')
            plan = inspect_plan(opts)
            self.selected(plan, opts)
            user = self.actor(plan, opts)
            if plan['bloqueos']:
                raise CommandError('Borrado bloqueado: ' + ' '.join(plan['bloqueos']))
            if (receipt.get('restauracion_pg16') is not True or receipt.get('origen') != source_identity()
                    or receipt.get('huella') != fingerprint(plan)
                    or receipt.get('sha256_respaldo') != backup_hash
                    or set(receipt.get('ids', [])) != set(opts['ids'] or [])
                    or bool(receipt.get('completo')) != bool(opts.get('completo'))
                    or receipt.get('mes') != opts.get('mes') or receipt.get('empresa') != opts['empresa']
                    or ((opts.get('mes') or opts.get('confirmados')) and receipt.get('limpieza_probada_pg16') is not True)
                    or receipt.get('perfil_confirmado') != plan.get('perfil')):
                raise CommandError('Respaldo/constancia no corresponden a esta base y simulación.')
            if opts.get('confirmados'):
                outcome = self.delete_confirmed(plan, receipt, user, opts)
            elif opts.get('completo'):
                outcome = self.delete_month_full(plan, receipt, user, opts)
            elif opts.get('mes'):
                outcome = self.delete_month(plan, receipt, user, opts)
            else:
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
                outcome = {'eliminados': sorted(opts['ids']), 'auditorias_creadas': 2}
        if outcome.pop('_purgar', None):
            from masiscam.management.depuracion_mensual import purge_files
            deleted, skipped = purge_files(plan, receipt['archivos_locales'])
            outcome['archivos_fisicos_eliminados'] = len(deleted)
            outcome['archivos_conservados_sin_copia_o_alterados'] = skipped
        self.stdout.write(canonical(outcome))

    def delete_month_full(self, plan, receipt, user, opts):
        from masiscam.management.depuracion_confirmada import read_signed, save_signed, verify_assets
        from masiscam.management.depuracion_mensual import PROFILE, apply_month_full
        expected = f"LIMPIAR COMPLETO {opts['empresa']} 2026-11 {fingerprint(plan)}"
        if plan['perfil'] != PROFILE or opts['confirmar'] != expected or not opts['motivo'].strip():
            raise CommandError('Confirme exactamente: ' + expected + ' ; indique motivo no vacío.')
        if not plan['cantidades']['eliminar']:
            raise CommandError('Nada eliminable: el inventario ya fue depurado o está protegido.')
        verify_assets([{'copia': a['copia'], 'sha256': a['sha256']} for a in receipt['archivos_locales']])
        custody = {'perfil': PROFILE, 'origen': source_identity(), 'plan': plan,
                   'respaldo_verificado': receipt, 'usuario': user.pk, 'motivo': opts['motivo']}
        path = Path(opts['carpeta']) / 'custodia-previa-mensual.json'
        if path.exists():
            if fingerprint(read_signed(path)) != fingerprint(custody):
                raise CommandError('La constancia de custodia previa no coincide.')
        else:
            save_signed(path, custody)  # fsync antes de borrar datos inmutables.
        apply_month_full(plan)
        event = Auditoria.objects.create(empresa_id=opts['empresa'], usuario=user,
            accion='LIMPIEZA_COMPLETA_NOV2026', objeto_tipo='Empresa', objeto_id=str(opts['empresa']),
            detalle={'huella': fingerprint(plan), 'cantidades': plan['cantidades'],
                     'conservados': plan['conservados'], 'custodia': str(path),
                     'respaldo_sha256': receipt['sha256_respaldo'], 'motivo': opts['motivo'],
                     'archivos': [a['nombre'] for a in plan['archivos']]})
        return {'registros_eliminados': plan['cantidades']['eliminar'], 'por_tabla': plan['cantidades']['por_tabla'],
                'pdf_a_eliminar': plan['cantidades']['pdf'], 'raices_conservadas': plan['conservados'],
                'auditoria': event.pk, 'custodia': str(path), '_purgar': True}

    def delete_confirmed(self, plan, receipt, user, opts):
        from masiscam.management.depuracion_confirmada import (
            PROFILE, apply_confirmed, read_signed, save_signed, verify_assets)
        if plan['perfil'] != PROFILE or opts['confirmar'] != 'ELIMINAR PRUEBAS CONFIRMADAS':
            raise CommandError('Confirme literalmente ELIMINAR PRUEBAS CONFIRMADAS.')
        verify_assets(receipt['archivos_locales'])
        custody = {'perfil': PROFILE, 'origen': source_identity(), 'plan': plan,
                   'respaldo_verificado': receipt, 'usuario': user.pk, 'motivo': opts['motivo']}
        path = Path(opts['carpeta']) / 'custodia-previa.json'
        if path.exists():
            if fingerprint(read_signed(path)) != fingerprint(custody):
                raise CommandError('La constancia de custodia previa no coincide.')
        else:
            save_signed(path, custody)  # fsync antes de borrar cualquier dato inmutable.
        apply_confirmed(plan)
        event = Auditoria.objects.create(empresa_id=plan['empresa_filtro'], usuario=user,
            accion='DEPURACION_CONFIRMADA_22_23', objeto_tipo='DepuracionConfirmada', objeto_id=PROFILE,
            detalle={'custodia': custody, 'huella_custodia': fingerprint(custody),
                     'ruta_custodia': str(path), 'confirmacion': opts['confirmar']})
        return {'registros_eliminados': [22, 23], 'expedientes_eliminados': [1, 2],
                'dependencias_eliminadas': len(plan['eliminar']) - 4, 'auditoria': event.pk,
                'custodia': str(path), 'equipos_documentos_y_drive': 'CONSERVADOS', 'guardas': 'RESTAURADAS'}

    def delete_month(self, plan, receipt, user, opts):
        expected = f"LIMPIAR {opts['empresa']} 2026-11 {fingerprint(plan)}"
        if opts['confirmar'] != expected or not opts['motivo'].strip():
            raise CommandError('Confirme exactamente: ' + expected + ' ; indique motivo no vacío.')
        if not plan['cantidades']['eliminar']:
            raise CommandError('Nada eliminable según las reglas actuales; el inventario protegido se conserva.')
        # Ya estamos dentro de la transacción y bajo los bloqueos de delete().
        for label in ('masiscam.RegistroEquipo', 'masiscam.Equipo'):
            ids = plan['eliminaria'].get(label, [])
            if not ids:
                continue
            model = apps.get_model(label)
            for row in plan['registros'][label]['datos']:
                if row['id'] in ids:
                    Auditoria.objects.create(empresa_id=opts['empresa'], usuario=user,
                        accion='LIMPIAR_PRUEBA_NOV2026', objeto_tipo=model.__name__, objeto_id=str(row['id']),
                        detalle={'identidad': row, 'huella': fingerprint(plan), 'respaldo': receipt,
                                 'motivo': opts['motivo'], 'confirmacion': opts['confirmar']})
        deleted = apply_month_rows(plan)
        Auditoria.objects.create(empresa_id=opts['empresa'], usuario=user,
            accion='LIMPIEZA_NOV2026_RESUMEN', objeto_tipo='Empresa', objeto_id=str(opts['empresa']),
            detalle={'eliminados': deleted, 'conservados': plan['conservados'], 'huella': fingerprint(plan),
                     'respaldo': receipt, 'motivo': opts['motivo'], 'confirmacion': opts['confirmar']})
        return {'eliminados': deleted, 'conservados': plan['conservados'],
                'limpieza_fisica_integral': not bool(plan['cantidades']['conservar'])}

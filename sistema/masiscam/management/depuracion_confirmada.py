"""Mantenimiento excepcional y acotado: RegistroEquipo 22/23, expediente 1/2."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from django.apps import apps
from django.core import signing
from django.core.management.base import CommandError
from django.db import connections

from masiscam.models import Auditoria, Documento, Equipo, ExpedienteMantenimiento, RegistroEquipo
from .commands.depurar_dos_equipos_prueba import canonical, fingerprint

PROFILE = 'registros-22-23-expedientes-1-2-v1'
ROOT_RECORDS = {22, 23}
ROOT_FILES = {1, 2}
KEEP = {'Equipo', 'Proyecto', 'Cliente', 'RolMasiscam', 'ContadorExpediente', 'Documento',
        'Auditoria', 'ReferenciaTecnica', 'ComponenteTecnico', 'ParteReferenciaTecnica',
        'MapaPlano', 'PosicionPlano', 'AplicabilidadReferenciaEquipo', 'PlantillaCalibracion',
        'ValidacionPlantillaCalibracion', 'AplicabilidadCalibracion'}


def foreign_keys(using):
    with connections[using].cursor() as cursor:
        cursor.execute("""SELECT ns.nspname,c.relname,a.attname,pns.nspname,p.relname,pa.attname,
            f.confdeltype,cardinality(f.conkey),cardinality(f.confkey)
            FROM pg_constraint f JOIN pg_class c ON c.oid=f.conrelid
            JOIN pg_namespace ns ON ns.oid=c.relnamespace JOIN pg_class p ON p.oid=f.confrelid
            JOIN pg_namespace pns ON pns.oid=p.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=f.conkey[1]
            JOIN pg_attribute pa ON pa.attrelid=p.oid AND pa.attnum=f.confkey[1]
            WHERE f.contype='f' ORDER BY ns.nspname,c.relname,a.attname,p.relname""")
        return list(cursor.fetchall())


def schema_name(using):
    with connections[using].cursor() as cursor:
        cursor.execute('SELECT current_schema()')
        return cursor.fetchone()[0]


def guards(tables, using):
    with connections[using].cursor() as cursor:
        cursor.execute("""SELECT DISTINCT n.nspname,p.proname,pg_get_functiondef(p.oid),p.prosrc,l.lanname
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace cn ON cn.oid=c.relnamespace
            JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE NOT t.tgisinternal AND cn.nspname=current_schema() AND c.relname=ANY(%s)
            ORDER BY n.nspname,p.proname""", [list(tables)])
        functions = list(cursor.fetchall())
        cursor.execute("""SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname=ANY(%s) ORDER BY c.relname,t.tgname""", [list(tables)])
        # Los nombres de los triggers RI contienen OIDs distintos al restaurar: excluir internos del manifiesto.
        triggers = [row for row in cursor.fetchall() if not row[1].startswith('RI_ConstraintTrigger_')]
        cursor.execute("""SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.tgisinternal
            AND n.nspname=current_schema() AND c.relname=ANY(%s) AND t.tgenabled<>'O'""", [list(tables)])
        disabled_fk = cursor.fetchone()[0]
    return {'funciones': functions, 'triggers': triggers, 'fk_deshabilitadas': disabled_fk}


def exp_ids(model, row, using, seen=None):
    seen = (seen or set()) | {(model._meta.label, row['id'])}
    found = set()
    for field in model._meta.fields:
        if not field.is_relation or row.get(field.attname) is None:
            continue
        parent = field.related_model
        pk = row[field.attname]
        if parent is ExpedienteMantenimiento:
            found.add(pk)
        elif (parent._meta.app_label == 'masiscam' and parent.__name__ not in KEEP
                and (parent._meta.label, pk) not in seen):
            data = parent._base_manager.using(using).filter(pk=pk).values().first()
            if data:
                found.update(exp_ids(parent, data, using, seen))
    return found


def inspect_confirmed(using='default'):
    records = list(RegistroEquipo._base_manager.using(using).filter(pk__in=ROOT_RECORDS).order_by('pk').values())
    files = list(ExpedienteMantenimiento._base_manager.using(using).filter(pk__in=ROOT_FILES).order_by('pk').values())
    if len(records) != 2 or len(files) != 2 or {x['registro_id'] for x in files} != ROOT_RECORDS:
        raise CommandError('El perfil exige exactamente registros 22/23 vinculados a expedientes 1/2. Sin sustituciones.')
    companies = {x['empresa_id'] for x in files}
    equipos = list(Equipo._base_manager.using(using).filter(pk__in={r['equipo_id'] for r in records})
                   .order_by('pk').values())
    eq_companies = set(Equipo._base_manager.using(using).filter(pk__in=[e['id'] for e in equipos])
                       .values_list('proyecto__empresa_id', flat=True))
    if len(companies) != 1 or companies != eq_companies:
        raise CommandError('Empresa incoherente o mixta en los registros confirmados.')
    company = companies.pop()
    schema = schema_name(using)
    model_map = {m._meta.db_table: m for m in apps.get_models()}
    keys = foreign_keys(using)
    rows = {(RegistroEquipo._meta.db_table, r['id']): r for r in records}
    rows.update({(ExpedienteMantenimiento._meta.db_table, r['id']): r for r in files})
    detach, dependencies, blocked = {}, [], []
    queue = list(rows)
    while queue:
        table, pk = queue.pop(0)
        for child_schema, child, col, parent_schema, parent, parent_col, deletion, ncols, nparent in keys:
            if parent_schema != schema or parent != table:
                continue
            if ncols != 1 or nparent != 1 or parent_col != 'id' or deletion not in {'a', 'r'}:
                blocked.append(f'FK no soportada: {child_schema}.{child}.{col} -> {table}.')
                continue
            q = connections[using].ops.quote_name
            with connections[using].cursor() as cursor:
                cursor.execute(f'SELECT id FROM {q(child_schema)}.{q(child)} WHERE {q(col)}=%s ORDER BY id', [pk])
                ids = [r[0] for r in cursor.fetchall()]
            if not ids:
                continue
            model = model_map.get(child) if child_schema == schema else None
            policy = next((f.remote_field.on_delete.__name__ for f in model._meta.fields
                           if f.is_relation and f.column == col), 'DESCONOCIDO') if model else 'DESCONOCIDO'
            dependencies.append({'padre': [table, pk], 'hijo': child, 'columna': col,
                                 'ids': ids, 'on_delete_django': policy})
            if model is None or model._meta.app_label != 'masiscam':
                blocked.append(f'Dependencia externa no autorizada: {child_schema}.{child}: {ids}.')
                continue
            for data in model._base_manager.using(using).filter(pk__in=ids).order_by('pk').values():
                key = (child, data['id'])
                if model.__name__ in KEEP:
                    if ((model is Auditoria and col == 'expediente_id') or
                            (model.__name__ == 'ReferenciaTecnica' and col == 'expediente_trabajo_id')):
                        if data['empresa_id'] != company:
                            blocked.append(f'Dependencia de otra empresa: {child} {data["id"]}.')
                        else:
                            detach[key] = {'campo': col, 'datos': data}
                    else:
                        blocked.append(f'Dependencia compartida/configuración conservada: {child} {data["id"]}.')
                    continue
                if key in rows:
                    continue
                owners = exp_ids(model, data, using)
                if model is ExpedienteMantenimiento:
                    owners.add(data['id'])
                if (model.__name__ == 'ArchivoRegistro' and data.get('registro_id') in ROOT_RECORDS):
                    owners = ROOT_FILES
                if not owners or not owners <= ROOT_FILES:
                    blocked.append(f'Dependencia operativa de otro expediente: {child} {data["id"]}: {sorted(owners)}.')
                    continue
                rows[key] = data
                queue.append(key)
    # Hijos primero, incluidas revisiones de una misma tabla. Cualquier ciclo falla cerrado.
    pending, order = set(rows), []
    while pending:
        parents = set()
        for child_key in pending:
            model, data = model_map[child_key[0]], rows[child_key]
            parents.update((f.related_model._meta.db_table, data[f.attname]) for f in model._meta.fields
                           if f.is_relation and data.get(f.attname) is not None
                           and (f.related_model._meta.db_table, data[f.attname]) in pending)
        leaves = sorted(pending - parents)
        if not leaves:
            blocked.append('Ciclo de dependencias: requiere revisión; no se omiten restricciones FK.')
            break
        order.extend(leaves)
        pending.difference_update(leaves)
    document_ids = {data['documento_id'] for data in rows.values() if data.get('documento_id')}
    document_ids.update(d['datos']['documento_id'] for d in detach.values() if d['datos'].get('documento_id'))
    # Los documentos permanecen enlazados a su equipo/proyecto: no borrar bytes ni Drive.
    docs = list(Documento._base_manager.using(using).filter(pk__in=document_ids).order_by('pk').values())
    if Documento._base_manager.using(using).filter(pk__in=document_ids).exclude(proyecto__empresa_id=company).exists():
        blocked.append('Hay documentos asociados de otra empresa: no se permite archivarlos ni modificar su relación.')
    affected_tables = {key[0] for key in rows} | {key[0] for key in detach}
    catalog = guards(affected_tables, using)
    if catalog['fk_deshabilitadas'] or any(row[2] != 'O' for row in catalog['triggers']):
        blocked.append('Hay disparadores de aplicación deshabilitados o con modo no ordinario.')
    return __import__('json').loads(canonical({
        'perfil': PROFILE, 'empresa_filtro': company,
        'equipos': [dict(e, empresa_id=company) for e in equipos], 'equipos_conservados': equipos,
        'eliminar': [{'tabla': t, 'id': pk, 'datos': rows[t, pk]} for t, pk in sorted(rows)],
        'desvincular': [{'tabla': t, 'id': pk, **data} for (t, pk), data in sorted(detach.items())],
        'documentos_conservados': docs, 'dependencias': dependencies, 'orden_borrado': order,
        'guardas': catalog, 'bloqueos': sorted(set(blocked)),
        'cantidades': {'eliminar': len(rows), 'desvincular': len(detach), 'documentos_conservados': len(docs)},
    }))


@contextmanager
def scoped_guards(plan, using, roots=None):
    """Excepción por fila, sólo en esta transacción; restaurar definiciones ANTES del commit."""
    connection = connections[using]
    if not connection.in_atomic_block:
        raise CommandError('La excepción acotada requiere una transacción.')
    q = connection.ops.quote_name
    schema = schema_name(using)
    allowed = ','.join(str(int(x)) for x in sorted(ROOT_FILES if roots is None else roots))
    with connection.cursor() as cursor:
        cursor.execute('CREATE TEMP TABLE masiscam_purge_confirmada (tabla text,id bigint,op text,campo text, PRIMARY KEY(tabla,id,op)) ON COMMIT DROP')
        cursor.executemany('INSERT INTO pg_temp.masiscam_purge_confirmada VALUES (%s,%s,%s,%s)',
            [(r['tabla'], r['id'], 'DELETE', '') for r in plan['eliminar']] +
            [(r['tabla'], r['id'], 'UPDATE', r['campo']) for r in plan['desvincular']])
        for ns, name, definition, body, language in plan['guardas']['funciones']:
            if language != 'plpgsql' or not name.startswith(('masiscam_', 'flujo_')) or definition.count(body) != 1:
                raise CommandError('Disparador desconocido: no se modifica ' + name)
            wrapped = """BEGIN
                IF TG_TABLE_SCHEMA=current_schema() AND TG_OP='DELETE' AND EXISTS (
                    SELECT 1 FROM pg_temp.masiscam_purge_confirmada p
                    WHERE p.tabla=TG_TABLE_NAME AND p.id=OLD.id AND p.op='DELETE') THEN RETURN OLD; END IF;
                IF TG_TABLE_SCHEMA=current_schema() AND TG_OP='UPDATE' AND EXISTS (
                    SELECT 1 FROM pg_temp.masiscam_purge_confirmada p
                    WHERE p.tabla=TG_TABLE_NAME AND p.id=OLD.id AND p.op='UPDATE'
                      AND p.campo IN ('expediente_id','expediente_trabajo_id')
                      AND (to_jsonb(OLD)->>p.campo)::bigint IN (SELECT unnest(ARRAY[""" + allowed + """]::bigint[]))
                      AND to_jsonb(NEW)->p.campo='null'::jsonb
                      AND to_jsonb(NEW)-p.campo=to_jsonb(OLD)-p.campo) THEN RETURN NEW; END IF;
            """ + body.rstrip().rstrip(';') + ';\nEND;'
            cursor.execute(definition.replace(body, wrapped, 1))
    try:
        yield
        with connection.cursor() as cursor:
            for ns, name, definition, body, language in plan['guardas']['funciones']:
                cursor.execute(definition)
            cursor.execute('DROP TABLE pg_temp.masiscam_purge_confirmada')
        tables = {r['tabla'] for r in plan['eliminar'] + plan['desvincular']}
        if fingerprint(guards(tables, using)) != fingerprint(plan['guardas']):
            raise CommandError('Las guardas no quedaron idénticas: se revierte toda la transacción.')
    except Exception:
        # No intentar DDL en una transacción fallida: atomic revierte también las funciones.
        raise


def apply_confirmed(plan, using='default'):
    if plan['perfil'] != PROFILE or plan['bloqueos']:
        raise CommandError('Perfil o dependencias no autorizados.')
    q = connections[using].ops.quote_name
    schema = schema_name(using)
    with scoped_guards(plan, using), connections[using].cursor() as cursor:
        for row in plan['desvincular']:
            cursor.execute(f'UPDATE {q(schema)}.{q(row["tabla"])} SET {q(row["campo"])}=NULL WHERE id=%s AND {q(row["campo"])} IN (1,2)', [row['id']])
            if cursor.rowcount != 1:
                raise CommandError('Cambió una dependencia a desvincular.')
        for table, pk in plan['orden_borrado']:
            cursor.execute(f'DELETE FROM {q(schema)}.{q(table)} WHERE id=%s', [pk])
            if cursor.rowcount != 1:
                raise CommandError('Borrado distinto del manifiesto: se revierte.')
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    if RegistroEquipo._base_manager.using(using).filter(pk__in=ROOT_RECORDS).exists() or ExpedienteMantenimiento._base_manager.using(using).filter(pk__in=ROOT_FILES).exists():
        raise CommandError('Persisten raíces confirmadas: se revierte.')
    kept = list(Equipo._base_manager.using(using).filter(pk__in=[r['id'] for r in plan['equipos_conservados']]).order_by('pk').values())
    docs = list(Documento._base_manager.using(using).filter(pk__in=[r['id'] for r in plan['documentos_conservados']]).order_by('pk').values())
    if fingerprint(kept) != fingerprint(plan['equipos_conservados']) or fingerprint(docs) != fingerprint(plan['documentos_conservados']):
        raise CommandError('Equipos o documentos conservados alterados: se revierte.')
    for row in plan['desvincular']:
        model = next(m for m in apps.get_models() if m._meta.db_table == row['tabla'])
        current = model._base_manager.using(using).filter(pk=row['id']).values().first()
        expected = dict(row['datos'], **{row['campo']: None})
        if fingerprint(current) != fingerprint(expected):
            raise CommandError('Se alteró información fuera de la desvinculación autorizada.')


def save_signed(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = dict(payload, firma=signing.dumps(payload, salt=PROFILE, compress=True))
    with path.open('x', encoding='utf-8') as stream:
        stream.write(canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        descriptor = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return result


def read_signed(path):
    import json
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    try:
        decoded = signing.loads(payload.pop('firma'), salt=PROFILE)
    except (KeyError, signing.BadSignature) as exc:
        raise CommandError('Constancia de identidad inválida.') from exc
    if decoded != payload:
        raise CommandError('Constancia alterada.')
    return payload


def archive_assets(plan, folder):
    """Sólo lectura del almacenamiento original. Los originales y Drive nunca se borran."""
    from django.core.files.storage import default_storage
    from .commands.depurar_dos_equipos_prueba import file_hash
    folder = Path(folder) / 'archivos'
    folder.mkdir(parents=True, exist_ok=True)
    assets = []
    for row in plan['documentos_conservados']:
        name = row['archivo']
        if not name:
            continue
        digest = hashlib.sha256()
        with tempfile.TemporaryFile(dir=folder) as staged:
            with default_storage.open(name, 'rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                    staged.write(block)
            sha = digest.hexdigest()
            if row['hash_sha256'] and row['hash_sha256'] != sha:
                raise CommandError('El documento no coincide con su hash: ' + str(row['id']))
            target = folder / sha
            if not target.exists():
                staged.seek(0)
                with target.open('xb') as stream:
                    shutil.copyfileobj(staged, stream, length=1024 * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
        if file_hash(target) != sha:
            raise CommandError('Copia local de documento alterada.')
        assets.append({'documento': row['id'], 'origen': name, 'copia': str(target.resolve()), 'sha256': sha})
    return assets


def verify_assets(assets):
    from .commands.depurar_dos_equipos_prueba import file_hash
    for asset in assets:
        if file_hash(asset['copia']) != asset['sha256']:
            raise CommandError('Respaldo local de documentos alterado.')

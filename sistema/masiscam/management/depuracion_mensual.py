"""Limpieza completa de noviembre 2026: registros operativos, documentos y PDF exclusivos."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from zoneinfo import ZoneInfo

from django.apps import apps
from django.core.management.base import CommandError
from django.db import connections, models

from masiscam.models import Documento, Equipo, ExpedienteMantenimiento, RegistroEquipo
from .commands.depurar_dos_equipos_prueba import canonical
from .depuracion_confirmada import foreign_keys, guards, schema_name, scoped_guards

PROFILE = 'mes-2026-11-completo-v1'
ZONE = ZoneInfo('America/Bogota')
START, END = datetime(2026, 11, 1, tzinfo=ZONE), datetime(2026, 12, 1, tzinfo=ZONE)
# Compartidos, catálogos, auditoría y biblioteca técnica: nunca se eliminan.
KEEP = {'Proyecto', 'Cliente', 'RolMasiscam', 'ContadorExpediente', 'Auditoria', 'ReferenciaTecnica',
        'ComponenteTecnico', 'ParteReferenciaTecnica', 'MapaPlano', 'PosicionPlano',
        'AplicabilidadReferenciaEquipo', 'PlantillaCalibracion', 'ValidacionPlantillaCalibracion',
        'AplicabilidadCalibracion'}
DETACH = {('Auditoria', 'expediente_id'), ('ReferenciaTecnica', 'expediente_trabajo_id')}
NO_OWNER_OK = {'RegistroEquipo', 'ArchivoRegistro', 'Documento'}


def created_field(model):
    return next((f for f in model._meta.fields if getattr(f, 'auto_now_add', False)), None)


def in_month(model, row):
    field = created_field(model)
    if not field:
        return True
    value = row.get(field.attname)
    if value is None:
        return False
    if not isinstance(value, datetime):
        return START.date() <= value < END.date()
    return START <= (value if value.tzinfo else value.replace(tzinfo=ZONE)) < END


def owners_of(model, row, using, seen=None):
    seen = (seen or set()) | {(model._meta.label, row['id'])}
    if model is ExpedienteMantenimiento:
        return {row['id']}
    found = set()
    for field in model._meta.fields:
        if not field.is_relation or row.get(field.attname) is None:
            continue
        parent = field.related_model
        if (parent._meta.app_label == 'masiscam' and parent.__name__ not in KEEP
                and (parent._meta.label, row[field.attname]) not in seen):
            data = parent._base_manager.using(using).filter(pk=row[field.attname]).values().first()
            if data:
                found |= owners_of(parent, data, using, seen)
    return found


def inspect_month_full(empresa, using='default'):
    from accounts.models import Empresa
    company = Empresa.objects.using(using).get(pk=empresa)
    schema, keys = schema_name(using), foreign_keys(using)
    q = connections[using].ops.quote_name
    model_map = {m._meta.db_table: m for m in apps.get_models()}
    base = lambda model: model._base_manager.using(using)
    window = lambda model: {created_field(model).name + '__gte': START, created_field(model).name + '__lt': END}
    expedientes = set(base(ExpedienteMantenimiento).filter(empresa_id=empresa, **window(ExpedienteMantenimiento))
                      .values_list('pk', flat=True))
    roots = [(m._meta.db_table, pk) for m, qs in (
        (Equipo, base(Equipo).filter(proyecto__empresa_id=empresa, **window(Equipo))),
        (RegistroEquipo, base(RegistroEquipo).filter(equipo__proyecto__empresa_id=empresa, **window(RegistroEquipo))),
        (ExpedienteMantenimiento, base(ExpedienteMantenimiento).filter(pk__in=expedientes)))
        for pk in qs.order_by('pk').values_list('pk', flat=True)]
    structural = []

    def walk(root):
        model = model_map[root[0]]
        rows = {root: base(model).filter(pk=root[1]).values().first()}
        detach, blocked, queue = {}, [], [root]
        while queue:
            table, pk = queue.pop(0)
            for child_schema, child, col, parent_schema, parent, parent_col, deletion, ncols, nparent in keys:
                if parent_schema != schema or parent != table:
                    continue
                if ncols != 1 or nparent != 1 or parent_col != 'id' or deletion not in {'a', 'r'}:
                    structural.append(f'FK no soportada: {child_schema}.{child}.{col} -> {table}.')
                    continue
                with connections[using].cursor() as cursor:
                    cursor.execute(f'SELECT id FROM {q(child_schema)}.{q(child)} WHERE {q(col)}=%s ORDER BY id', [pk])
                    ids = [r[0] for r in cursor.fetchall()]
                cmodel = model_map.get(child) if child_schema == schema else None
                if ids and (cmodel is None or cmodel._meta.app_label != 'masiscam'):
                    blocked.append(f'Dependencia externa: {child} {ids}.')
                    continue
                for data in (cmodel._base_manager.using(using).filter(pk__in=ids).order_by('pk').values() if ids else []):
                    key, name = (child, data['id']), cmodel.__name__
                    if name in KEEP:
                        if (name, col) in DETACH and data['empresa_id'] == empresa:
                            detach[key] = {'campo': col, 'datos': data}
                        else:
                            blocked.append(f'Dependencia compartida conservada: {name} {data["id"]}.')
                        continue
                    if key in rows:
                        continue
                    owners = owners_of(cmodel, data, using)
                    if not in_month(cmodel, data):
                        blocked.append(f'{name} {data["id"]} fuera de noviembre 2026.')
                    elif owners and not owners <= expedientes:
                        blocked.append(f'{name} {data["id"]} pertenece a expedientes ajenos {sorted(owners - expedientes)}.')
                    elif not owners and name not in NO_OWNER_OK:
                        blocked.append(f'{name} {data["id"]} sin propietario verificable.')
                    elif name == 'Documento' and not base(Documento).filter(pk=data['id'], proyecto__empresa_id=empresa).exists():
                        blocked.append(f'Documento {data["id"]} de otra empresa.')
                    else:
                        rows[key] = data
                        queue.append(key)
        return rows, detach, blocked

    results = {root: walk(root) for root in roots}
    excluded = {r for r, (_, _, b) in results.items() if b}
    changed = True
    while changed:
        changed = False
        blocked_rows = set().union(*(set(results[r][0]) for r in excluded)) if excluded else set()
        for root, (rows, _, _) in results.items():
            if root not in excluded and (set(rows) & blocked_rows):
                excluded.add(root)
                changed = True
    rows, detach, conserved = {}, {}, {}
    for root, (r, d, b) in results.items():
        if root in excluded:
            conserved[f'{root[0]}:{root[1]}'] = b or ['Comparte registros con una raíz conservada.']
        else:
            rows.update(r)
            detach.update(d)
    # Documentos referenciados: sólo si todos sus dependientes se eliminan y no son de otra empresa.
    doc_table = Documento._meta.db_table
    candidates = {data[f.attname] for key, data in rows.items() for f in model_map[key[0]]._meta.fields
                  if f.is_relation and f.related_model is Documento and data.get(f.attname)}
    for doc_id in sorted(candidates - {pk for t, pk in rows if t == doc_table}):
        doc = base(Documento).filter(pk=doc_id).values().first()
        if (not doc or not in_month(Documento, doc) or
                not base(Documento).filter(pk=doc_id, proyecto__empresa_id=empresa).exists()):
            conserved[f'{doc_table}:{doc_id}'] = ['Documento anterior/posterior a noviembre o de otra empresa.']
            continue
        outside = []
        for child_schema, child, col, parent_schema, parent, *_ in keys:
            if parent_schema == schema and parent == doc_table:
                with connections[using].cursor() as cursor:
                    cursor.execute(f'SELECT id FROM {q(child_schema)}.{q(child)} WHERE {q(col)}=%s', [doc_id])
                    outside += [(child, r[0]) for r in cursor.fetchall() if (child, r[0]) not in rows]
        if outside:
            conserved[f'{doc_table}:{doc_id}'] = [f'Compartido con {len(outside)} registros conservados.']
        else:
            rows[(doc_table, doc_id)] = doc
    # Orden: hijos primero.
    pending, order, blocked = set(rows), [], []
    while pending:
        parents = set()
        for key in pending:
            model, data = model_map[key[0]], rows[key]
            parents.update((f.related_model._meta.db_table, data[f.attname]) for f in model._meta.fields
                           if f.is_relation and data.get(f.attname) is not None
                           and (f.related_model._meta.db_table, data[f.attname]) in pending)
        leaves = sorted(pending - parents)
        if not leaves:
            blocked.append('Ciclo de dependencias: se requiere revisión manual.')
            break
        order.extend(leaves)
        pending.difference_update(leaves)
    # Archivos físicos exclusivos: ninguna otra fila conservada puede referenciar el mismo nombre.
    file_fields = [(m, f) for m in apps.get_models() if m._meta.app_label in {'masiscam', 'accounts'}
                   for f in m._meta.fields if isinstance(f, models.FileField)]
    files, shared, drive = [], [], 0
    for key, data in sorted(rows.items()):
        model = model_map[key[0]]
        drive += any(v for k, v in data.items() if k.startswith('drive_') and k != 'drive_error')
        for field in model._meta.fields:
            if isinstance(field, models.FileField) and data.get(field.attname):
                name = data[field.attname]
                other = any(m._base_manager.using(using).filter(**{f.name: name}).exclude(
                    pk__in=[pk for t, pk in rows if t == m._meta.db_table]).exists() for m, f in file_fields)
                (shared if other else files).append({'tabla': key[0], 'id': key[1], 'campo': field.name,
                    'nombre': name, 'pdf': name.lower().endswith('.pdf')})
    catalog = guards({k[0] for k in rows} | {k[0] for k in detach}, using)
    if catalog['fk_deshabilitadas'] or any(row[2] != 'O' for row in catalog['triggers']):
        blocked.append('Hay disparadores de aplicación deshabilitados o con modo no ordinario.')
    by_model = {}
    for t, _ in rows:
        by_model[t] = by_model.get(t, 0) + 1
    return json.loads(canonical({
        'perfil': PROFILE, 'mes': '2026-11', 'empresa_filtro': empresa, 'empresa': company.nombre,
        'eliminar': [{'tabla': t, 'id': pk, 'datos': rows[t, pk]} for t, pk in sorted(rows)],
        'desvincular': [{'tabla': t, 'id': pk, **v} for (t, pk), v in sorted(detach.items())],
        'orden_borrado': order, 'conservados': conserved, 'guardas': catalog,
        'archivos': files, 'archivos_compartidos_conservados': shared,
        'bloqueos': sorted(set(blocked + structural)),
        'cantidades': {'eliminar': len(rows), 'por_tabla': by_model, 'desvincular': len(detach),
                       'pdf': sum(f['pdf'] for f in files), 'archivos': len(files),
                       'raices_conservadas': len(conserved), 'drive_sin_tocar': drive}}))


def apply_month_full(plan, using='default'):
    if plan['perfil'] != PROFILE or plan['bloqueos']:
        raise CommandError('Perfil o dependencias no autorizados.')
    q = connections[using].ops.quote_name
    schema = schema_name(using)
    exp_table = ExpedienteMantenimiento._meta.db_table
    exp_ids = sorted(r['id'] for r in plan['eliminar'] if r['tabla'] == exp_table)
    with scoped_guards(plan, using, roots=exp_ids), connections[using].cursor() as cursor:
        for row in plan['desvincular']:
            cursor.execute(f'UPDATE {q(schema)}.{q(row["tabla"])} SET {q(row["campo"])}=NULL '
                           f'WHERE id=%s AND {q(row["campo"])}=ANY(%s)', [row['id'], exp_ids])
            if cursor.rowcount != 1:
                raise CommandError('Cambió una dependencia a desvincular.')
        for table, pk in plan['orden_borrado']:
            cursor.execute(f'DELETE FROM {q(schema)}.{q(table)} WHERE id=%s', [pk])
            if cursor.rowcount != 1:
                raise CommandError('Borrado distinto del manifiesto: se revierte.')
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def archive_files(plan, folder):
    """Copia verificada (sha256) de cada archivo exclusivo; sólo lectura del origen."""
    from django.core.files.storage import default_storage
    folder = Path(folder) / 'archivos-mensual'
    folder.mkdir(parents=True, exist_ok=True)
    assets, faltantes = [], []
    for item in plan['archivos']:
        if not default_storage.exists(item['nombre']):
            faltantes.append(item['nombre'])
            continue
        digest = hashlib.sha256()
        with tempfile.TemporaryFile(dir=folder) as staged:
            with default_storage.open(item['nombre'], 'rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                    staged.write(block)
            sha = digest.hexdigest()
            target = folder / sha
            if not target.exists():
                staged.seek(0)
                with target.open('xb') as out:
                    shutil.copyfileobj(staged, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
        assets.append({'origen': item['nombre'], 'copia': str(target.resolve()), 'sha256': sha, 'pdf': item['pdf']})
    return assets, faltantes


def purge_files(plan, assets):
    """Tras el commit: borra sólo originales con copia verificada e idéntica."""
    from django.core.files.storage import default_storage
    copies = {a['origen']: a for a in assets}
    deleted, skipped = [], []
    for item in plan['archivos']:
        asset = copies.get(item['nombre'])
        if not asset or not default_storage.exists(item['nombre']):
            skipped.append(item['nombre'])
            continue
        digest = hashlib.sha256()
        with default_storage.open(item['nombre'], 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != asset['sha256']:
            skipped.append(item['nombre'])
            continue
        default_storage.delete(item['nombre'])
        deleted.append(item['nombre'])
    return deleted, skipped

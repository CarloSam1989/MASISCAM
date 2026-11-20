"""Reinicio de datos operativos de MASISCAM conservando usuarios, perfiles, roles y estructura."""
from contextlib import contextmanager
import hashlib
import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import connections, models

from .commands.depurar_dos_equipos_prueba import canonical
from .depuracion_confirmada import foreign_keys, guards, schema_name

PROFILE = 'reinicio-datos-masiscam-v1'
# Roles (y su vínculo con clientes históricos) son parte del acceso: nunca se tocan.
CONSERVAR = {'RolMasiscam'}
# La auditoría se conserva; solo se desvinculan sus referencias a datos operativos eliminados.
AUDITORIA = 'Auditoria'


def _q(using):
    return connections[using].ops.quote_name


def _scalar(using, sql, params=()):
    with connections[using].cursor() as cursor:
        cursor.execute(sql, list(params))
        return cursor.fetchone()


def _snapshot(model, using):
    """Huella de una tabla de acceso (usuarios, perfiles, empresas, roles) sin exponer sus valores."""
    q = _q(using)
    columnas = ', '.join(f'coalesce({q(f.column)}::text, \'\')' for f in model._meta.concrete_fields)
    pk = q(model._meta.pk.column)
    total, activos, digest = _scalar(using, f"""SELECT count(*),
        count(*) FILTER (WHERE {'is_active' if any(f.name == 'is_active' for f in model._meta.fields) else 'true'}),
        coalesce(md5(string_agg(concat_ws('|', {columnas}), ',' ORDER BY {pk})), '')
        FROM {q(model._meta.db_table)}""")
    return {'filas': total, 'activas': activos, 'md5': digest}


def snapshot_acceso(using='default'):
    from accounts.models import Empresa, Perfil
    from masiscam.models import RolMasiscam
    return {'usuarios': _snapshot(get_user_model(), using), 'perfiles': _snapshot(Perfil, using),
            'empresas': _snapshot(Empresa, using), 'roles': _snapshot(RolMasiscam, using)}


def _filtro(tabla, retener, schema, using):
    q = _q(using)
    if tabla in retener:
        return f'{q(schema)}.{q(tabla)} WHERE id <> ALL(%s)', [retener[tabla]]
    return f'{q(schema)}.{q(tabla)}', []


def inspect_reset(using='default'):
    from masiscam.models import Auditoria, Cliente, RolMasiscam
    schema, keys, q = schema_name(using), foreign_keys(using), _q(using)
    todos = list(apps.get_app_config('masiscam').get_models())
    borrar = {m._meta.db_table: m for m in todos if m.__name__ not in CONSERVAR | {AUDITORIA}}
    tabla_roles, tabla_auditoria = RolMasiscam._meta.db_table, Auditoria._meta.db_table
    desvincular = [f.column for f in Auditoria._meta.concrete_fields
                   if f.is_relation and f.related_model._meta.db_table in borrar]
    bloqueos = []
    for campo in Auditoria._meta.concrete_fields:
        if campo.column in desvincular and not campo.null:
            bloqueos.append(f'La auditoría exige {campo.column}; no se puede desvincular.')
    retener = {}
    ids = sorted(RolMasiscam._base_manager.using(using).exclude(cliente__isnull=True)
                 .values_list('cliente_id', flat=True).distinct())
    if ids:
        retener[Cliente._meta.db_table] = ids
    aristas = {t: set() for t in borrar}
    for child_schema, child, col, parent_schema, parent, parent_col, deletion, ncols, nparent in keys:
        if parent_schema != schema or parent not in borrar:
            continue
        if ncols != 1 or nparent != 1 or parent_col != 'id' or deletion not in {'a', 'r'}:
            bloqueos.append(f'FK no soportada: {child}.{col} -> {parent}.')
        elif child_schema == schema and child in borrar:
            if child != parent:
                aristas[parent].add(child)
        elif child_schema == schema and child == tabla_roles and col == 'cliente_id':
            continue
        elif child_schema == schema and child == tabla_auditoria and col in desvincular:
            continue
        else:
            bloqueos.append(f'Dependencia no autorizada: {child_schema}.{child}.{col} -> {parent}.')
    orden, pendientes = [], set(borrar)
    while pendientes:
        hojas = sorted(t for t in pendientes if not (aristas[t] & pendientes))
        if not hojas:
            bloqueos.append('Ciclo de dependencias entre tablas: se requiere revisión manual.')
            break
        orden.extend(hojas)
        pendientes.difference_update(hojas)
    tablas = {}
    for tabla in orden:
        origen, params = _filtro(tabla, retener, schema, using)
        total, huella = _scalar(using, f"SELECT count(*), coalesce(md5(string_agg(id::text, ',' ORDER BY id)), '') "
                                       f"FROM {origen}", params)
        tablas[tabla] = {'filas': total, 'ids_md5': huella}
    archivos, compartidos, drive = [], [], 0
    campos_conservados = [(m, f) for m in apps.get_models() if m._meta.db_table not in borrar
                          for f in m._meta.concrete_fields if isinstance(f, models.FileField)]
    for tabla in orden:
        modelo = borrar[tabla]
        origen, params = _filtro(tabla, retener, schema, using)
        for campo in modelo._meta.concrete_fields:
            if campo.column.startswith('drive_') and campo.column != 'drive_error':
                drive += _scalar(using, f"SELECT count(*) FROM {origen} {'AND' if params else 'WHERE'} "
                                        f"coalesce({q(campo.column)}::text, '') NOT IN ('', 'False', 'false')", params)[0]
            if not isinstance(campo, models.FileField):
                continue
            with connections[using].cursor() as cursor:
                cursor.execute(f"SELECT {q(campo.column)} FROM {origen} {'AND' if params else 'WHERE'} "
                               f"{q(campo.column)} <> '' ORDER BY id", params)
                nombres = sorted({fila[0] for fila in cursor.fetchall()})
            for nombre in nombres:
                otro = any(m._base_manager.using(using).filter(**{f.name: nombre}).exists() for m, f in campos_conservados)
                (compartidos if otro else archivos).append({'tabla': tabla, 'campo': campo.name, 'nombre': nombre,
                                                             'pdf': nombre.lower().endswith('.pdf')})
    auditoria = {'filas': _scalar(using, f'SELECT count(*) FROM {q(schema)}.{q(tabla_auditoria)}')[0]}
    for columna in desvincular:
        auditoria[columna] = _scalar(using, f'SELECT count(*) FROM {q(schema)}.{q(tabla_auditoria)} '
                                            f'WHERE {q(columna)} IS NOT NULL')[0]
    afectadas = set(orden) | {tabla_auditoria}
    guardas = guards(afectadas, using)
    if guardas['fk_deshabilitadas'] or any(f[2] != 'O' for f in guardas['triggers']):
        bloqueos.append('Hay disparadores de aplicación deshabilitados o con modo no ordinario.')
    return json.loads(canonical({
        'perfil': PROFILE, 'orden_borrado': orden, 'tablas': tablas, 'clientes_conservados_por_roles': retener,
        'auditoria': auditoria, 'desvincular_auditoria': desvincular, 'acceso': snapshot_acceso(using),
        'archivos': archivos, 'archivos_compartidos_conservados': compartidos, 'guardas': guardas,
        'bloqueos': sorted(set(bloqueos)),
        'cantidades': {'registros': sum(t['filas'] for t in tablas.values()), 'tablas': len(orden),
                       'pdf': sum(a['pdf'] for a in archivos), 'archivos': len(archivos), 'drive_sin_tocar': drive}}))


@contextmanager
def _guardas_acotadas(plan, using):
    """Excepción por fila, solo dentro de esta transacción: las funciones se restauran antes del commit."""
    conexion = connections[using]
    if not conexion.in_atomic_block:
        raise CommandError('El reinicio exige una transacción.')
    with conexion.cursor() as cursor:
        cursor.execute('CREATE TEMP TABLE masiscam_reinicio (tabla text, id bigint, op text, campo text, '
                       'PRIMARY KEY (tabla, id, op)) ON COMMIT DROP')
        for ns, nombre, definicion, cuerpo, lenguaje in plan['guardas']['funciones']:
            if lenguaje != 'plpgsql' or not nombre.startswith(('masiscam_', 'flujo_')) or definicion.count(cuerpo) != 1:
                raise CommandError('Disparador desconocido: no se modifica ' + nombre)
            envoltura = """BEGIN
                IF TG_TABLE_SCHEMA=current_schema() AND TG_OP='DELETE' AND EXISTS (
                    SELECT 1 FROM pg_temp.masiscam_reinicio p
                    WHERE p.tabla=TG_TABLE_NAME AND p.id=OLD.id AND p.op='DELETE') THEN RETURN OLD; END IF;
                IF TG_TABLE_SCHEMA=current_schema() AND TG_OP='UPDATE' AND EXISTS (
                    SELECT 1 FROM pg_temp.masiscam_reinicio p
                    WHERE p.tabla=TG_TABLE_NAME AND p.id=OLD.id AND p.op='UPDATE'
                      AND to_jsonb(NEW)->p.campo='null'::jsonb
                      AND to_jsonb(NEW)-p.campo=to_jsonb(OLD)-p.campo) THEN RETURN NEW; END IF;
            """ + cuerpo.rstrip().rstrip(';') + ';\nEND;'
            cursor.execute(definicion.replace(cuerpo, envoltura, 1))
    yield
    with conexion.cursor() as cursor:
        for ns, nombre, definicion, cuerpo, lenguaje in plan['guardas']['funciones']:
            cursor.execute(definicion)
        cursor.execute('DROP TABLE pg_temp.masiscam_reinicio')
    afectadas = set(plan['orden_borrado']) | {'masiscam_auditoria'}
    if canonical(guards(afectadas, using)) != canonical(plan['guardas']):
        raise CommandError('Las guardas no quedaron idénticas: se revierte toda la transacción.')


def apply_reset(plan, using='default'):
    """Vacía las tablas operativas (hijos primero) sin desactivar restricciones ni triggers."""
    if plan['perfil'] != PROFILE or plan['bloqueos']:
        raise CommandError('Perfil o dependencias no autorizados: ' + ' '.join(plan['bloqueos']))
    q, schema = _q(using), schema_name(using)
    retener = {t: v for t, v in plan['clientes_conservados_por_roles'].items()}
    tabla_auditoria = 'masiscam_auditoria'
    with _guardas_acotadas(plan, using), connections[using].cursor() as cursor:
        for tabla in plan['orden_borrado']:
            origen, params = _filtro(tabla, retener, schema, using)
            cursor.execute(f"INSERT INTO pg_temp.masiscam_reinicio SELECT %s, id, 'DELETE', '' FROM {origen}",
                           [tabla] + params)
        for columna in plan['desvincular_auditoria']:
            cursor.execute("DELETE FROM pg_temp.masiscam_reinicio WHERE op='UPDATE'")
            cursor.execute(f"INSERT INTO pg_temp.masiscam_reinicio SELECT %s, id, 'UPDATE', %s "
                           f"FROM {q(schema)}.{q(tabla_auditoria)} WHERE {q(columna)} IS NOT NULL", [tabla_auditoria, columna])
            cursor.execute(f'UPDATE {q(schema)}.{q(tabla_auditoria)} SET {q(columna)}=NULL WHERE {q(columna)} IS NOT NULL')
            if cursor.rowcount != plan['auditoria'][columna]:
                raise CommandError('La auditoría cambió durante el reinicio: se revierte.')
        for tabla in plan['orden_borrado']:
            origen, params = _filtro(tabla, retener, schema, using)
            cursor.execute(f"DELETE FROM {origen}", params)
            if cursor.rowcount != plan['tablas'][tabla]['filas']:
                raise CommandError(f'Borrado distinto del manifiesto en {tabla}: se revierte.')
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    resumen = verificar_reinicio(plan, using)
    reiniciar_secuencias(plan, using)
    return resumen


def reiniciar_secuencias(plan, using='default'):
    """Solo secuencias de tablas operativas; nunca las de usuarios, perfiles o roles."""
    q, schema = _q(using), schema_name(using)
    with connections[using].cursor() as cursor:
        for tabla in plan['orden_borrado']:
            cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [f'{q(schema)}.{q(tabla)}', 'id'])
            secuencia = cursor.fetchone()[0]
            if not secuencia:
                continue
            cursor.execute(f'SELECT coalesce(max(id), 0) + 1 FROM {q(schema)}.{q(tabla)}')
            cursor.execute('SELECT setval(%s, %s, false)', [secuencia, cursor.fetchone()[0]])


def verificar_reinicio(plan, using='default'):
    """Conteos operativos en cero (salvo clientes ligados a roles) y acceso idéntico."""
    q, schema = _q(using), schema_name(using)
    restante = {}
    for tabla in plan['orden_borrado']:
        esperado = len(plan['clientes_conservados_por_roles'].get(tabla, []))
        total = _scalar(using, f'SELECT count(*) FROM {q(schema)}.{q(tabla)}')[0]
        if total != esperado:
            raise CommandError(f'Quedan {total} registros en {tabla}; se revierte.')
        restante[tabla] = total
    if snapshot_acceso(using) != plan['acceso']:
        raise CommandError('Usuarios, perfiles, empresas o roles cambiaron: se revierte.')
    if _scalar(using, f'SELECT count(*) FROM {q(schema)}.{q("masiscam_auditoria")}')[0] != plan['auditoria']['filas']:
        raise CommandError('La auditoría no se conserva íntegra: se revierte.')
    return {'tablas_operativas': restante, 'usuarios_activos': plan['acceso']['usuarios']['activas'],
            'auditoria_conservada': plan['auditoria']['filas']}

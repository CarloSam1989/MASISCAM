"""Retiro lógico del alcance confirmado, sin alterar sus datos históricos."""
from django.apps import apps
from django.db import models

PROFILE = 'registros-22-23-expedientes-1-2-v1'
ACTION = 'RETIRO_OPERATIVO_CONFIRMADO'


def marcadores(using='default'):
    return apps.get_model('masiscam', 'Auditoria').objects.using(using).filter(
        accion=ACTION, objeto_tipo='RetiroOperativo', objeto_id=PROFILE)


class OperativosManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        exp = apps.get_model('masiscam', 'ExpedienteMantenimiento')
        retirados = exp._base_manager.using(qs.db).filter(
            pk__in=(1, 2), registro_id__in=(22, 23),
            empresa_id__in=marcadores(qs.db).values('empresa_id'))
        if self.model is exp:
            return qs.exclude(pk__in=retirados.values('pk'))
        return qs.exclude(pk__in=retirados.values('registro_id'))


def retirar_confirmados(opts):
    import getpass
    import os
    import socket
    import uuid
    from pathlib import Path
    from django.conf import settings
    from django.core.management.base import CommandError
    from django.db import connection, transaction
    from .management.depuracion_confirmada import inspect_confirmed, save_signed, read_signed
    from .management.commands.depurar_dos_equipos_prueba import fingerprint, source_identity

    if any(opts.get(k) for k in ('ids', 'mes', 'empresa', 'usuario', 'operador', 'carpeta')):
        raise CommandError('retirar usa exclusivamente el perfil confirmado y la custodia persistente de MEDIA_ROOT.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(22123012)')
            # Inventario y marcador se publican juntos; impedir escrituras concurrentes.
            tables = sorted(m._meta.db_table for m in apps.get_app_config('masiscam').get_models())
            cursor.execute('LOCK TABLE ' + ', '.join(connection.ops.quote_name(t) for t in tables)
                           + ' IN SHARE ROW EXCLUSIVE MODE')
            cursor.execute('SELECT current_user')
            db_actor = cursor.fetchone()[0]
        plan = inspect_confirmed()
        marker = marcadores().filter(empresa_id=plan['empresa_filtro']).first()
        if marker:
            saved = read_signed(marker.detalle['ruta_custodia'])
            if fingerprint(saved['plan']) != marker.detalle['huella']:
                raise CommandError('La constancia de custodia no coincide con la auditoría.')
        else:
            root = Path(settings.MEDIA_ROOT).resolve()
            if not root.is_dir():
                raise CommandError('MEDIA_ROOT debe ser un volumen persistente existente y escribible por la aplicación.')
            folder = root / '.masiscam-maintenance'
            try:
                folder.mkdir(mode=0o700, exist_ok=True)
                if folder.is_symlink() or folder.resolve().parent != root:
                    raise CommandError('Ruta de custodia fuera del volumen autorizado.')
                path = folder / f'{PROFILE}-{uuid.uuid4().hex}.json'
                identity = {'identidad': 'MANTENIMIENTO_TECNICO', 'usuario_sistema': getpass.getuser(),
                            'host': socket.gethostname(), 'pid': os.getpid(), 'usuario_postgresql': db_actor}
                save_signed(path, {'perfil': PROFILE, 'origen': source_identity(), 'plan': plan,
                                  'operador': identity, 'operacion': 'RETIRO_LOGICO_SIN_BORRADO'})
                saved = read_signed(path)
                if fingerprint(saved['plan']) != fingerprint(plan):
                    raise CommandError('No se pudo verificar la constancia persistente.')
            except OSError as exc:
                raise CommandError(f'Custodia no escribible en el volumen MEDIA_ROOT: {exc}') from exc
            marker = apps.get_model('masiscam', 'Auditoria').objects.create(
                empresa_id=plan['empresa_filtro'], usuario=None, accion=ACTION,
                objeto_tipo='RetiroOperativo', objeto_id=PROFILE,
                detalle=dict(identity, ruta_custodia=str(path), huella=fingerprint(plan),
                             registros=[22, 23], expedientes=[1, 2], sin_borrado=True))
        reg = apps.get_model('masiscam', 'RegistroEquipo')
        exp = apps.get_model('masiscam', 'ExpedienteMantenimiento')
        if reg.objects.filter(pk__in=(22, 23)).exists() or exp.objects.filter(pk__in=(1, 2)).exists():
            raise CommandError('Verificación operativa fallida; retiro revertido.')
        if fingerprint(inspect_confirmed()) != fingerprint(plan):
            raise CommandError('Cambió el histórico protegido; retiro revertido.')
        return {'resultado': 'VERIFICADO: retirados de operación, históricos conservados',
                'registros': [22, 23], 'expedientes': [1, 2], 'auditoria': marker.pk,
                'ruta_custodia': marker.detalle['ruta_custodia'], 'visibles': 0,
                'historicos_conservados': 4}

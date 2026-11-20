"""Reinicio de datos operativos: simula, verifica el respaldo restaurable y ejecuta con confirmación."""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management.base import CommandError
from django.db import connections, transaction
from django.utils import timezone

from masiscam.models import Auditoria, RolMasiscam
from .depurar_dos_equipos_prueba import Command as DepuracionBase, canonical, file_hash, fingerprint, source_identity


class Command(DepuracionBase):
    help = ('Elimina TODOS los datos operativos y la biblioteca técnica (con sus PDF exclusivos) conservando '
            'usuarios, contraseñas, roles, permisos, configuración y estructura. Modos: simular, '
            'verificar-respaldo y ejecutar.')

    def add_arguments(self, parser):
        parser.add_argument('accion', choices=['simular', 'verificar-respaldo', 'ejecutar'])
        parser.add_argument('--usuario', type=int, help='ID del ADMINISTRADOR responsable.')
        parser.add_argument('--respaldo', help='Respaldo PostgreSQL completo (.dump).')
        parser.add_argument('--qa-url', help='PostgreSQL 16 desechable en loopback (masiscam_qa_*).')
        parser.add_argument('--pg-restore', default='pg_restore')
        parser.add_argument('--carpeta', help='Custodia local de archivos y constancias (carpeta nueva).')
        parser.add_argument('--salida', help='Constancia nueva de restauración verificada.')
        parser.add_argument('--verificacion', help='Constancia emitida por verificar-respaldo.')
        parser.add_argument('--huella')
        parser.add_argument('--motivo')
        parser.add_argument('--confirmar', help='Opcional y no interactivo: si se indica debe ser REINICIAR TODOS LOS DATOS OPERATIVOS <huella>.')

    def _preparar(self, opts):
        opts.update(reinicio=True, mes=None, empresa=None, ids=None, completo=False, confirmados=False)
        user = get_user_model().objects.filter(pk=opts['usuario'], is_active=True).first() if opts['usuario'] else None
        if user is None or not RolMasiscam.objects.filter(perfil__user=user, perfil__activo=True,
                perfil__empresa__activa=True, activo=True, rol='ADMIN').exists():
            raise CommandError('Indique --usuario de un ADMINISTRADOR activo.')
        return user

    def handle(self, *args, **opts):
        from masiscam.management.depuracion_reinicio import inspect_reset
        if connections['default'].vendor != 'postgresql':
            raise CommandError('Este procedimiento requiere PostgreSQL.')
        user = self._preparar(opts)
        if opts['accion'] == 'ejecutar':
            return self.ejecutar(opts, user)
        with transaction.atomic():
            with connections['default'].cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            plan = inspect_reset()
            source = source_identity()
        if opts['accion'] == 'verificar-respaldo':
            if not opts.get('carpeta'):
                raise CommandError('Indique --carpeta nueva para la custodia de archivos.')
            return self.verify_backup(plan, source, opts)
        self.stdout.write(canonical({'origen': source, 'huella': fingerprint(plan), 'simulacion': plan}))

    def ejecutar(self, opts, user):
        from django.apps import apps
        from masiscam.management.depuracion_mensual import purge_files
        from masiscam.management.depuracion_confirmada import read_signed, save_signed, verify_assets
        from masiscam.management.depuracion_reinicio import PROFILE, apply_reset, inspect_reset
        opts['motivo'] = (opts.get('motivo') or 'Reinicio completo de datos operativos').strip()
        if not all(opts.get(k) for k in ('respaldo', 'verificacion', 'huella', 'carpeta')):
            raise CommandError('Faltan respaldo restaurado, constancia, huella o carpeta de custodia.')
        receipt = json.loads(Path(opts['verificacion']).read_text(encoding='utf-8'))
        try:
            verified = signing.loads(receipt.get('firma', ''), salt='masiscam.depuracion.respaldo')
        except signing.BadSignature as exc:
            raise CommandError('Constancia de restauración sin firma válida.') from exc
        if verified != {k: v for k, v in receipt.items() if k != 'firma'}:
            raise CommandError('Constancia de restauración alterada.')
        backup_hash = file_hash(opts['respaldo'])
        with transaction.atomic():
            tablas = [m._meta.db_table for m in apps.get_models() if m._meta.app_label in {'masiscam', 'accounts', 'auth'}]
            q = connections['default'].ops.quote_name
            with connections['default'].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute('LOCK TABLE ' + ', '.join(q(t) for t in sorted(set(tablas))) + ' IN SHARE ROW EXCLUSIVE MODE')
            plan = inspect_reset()
            if plan['bloqueos']:
                raise CommandError('Reinicio bloqueado: ' + ' '.join(plan['bloqueos']))
            if (opts['huella'] != fingerprint(plan) or receipt.get('reinicio') is not True
                    or receipt.get('huella') != fingerprint(plan) or receipt.get('restauracion_pg16') is not True
                    or receipt.get('sha256_respaldo') != backup_hash or receipt.get('origen') != source_identity()
                    or receipt.get('perfil_confirmado') != PROFILE):
                raise CommandError('Respaldo o constancia no corresponden a esta base y a esta simulación.')
            if opts.get('confirmar') and opts['confirmar'] != f"REINICIAR TODOS LOS DATOS OPERATIVOS {fingerprint(plan)}":
                raise CommandError('La confirmación indicada no corresponde a esta simulación.')
            if not plan['cantidades']['registros']:
                raise CommandError('No hay datos operativos que reiniciar.')
            verify_assets(receipt['archivos_locales'])
            custodia = {'perfil': PROFILE, 'origen': source_identity(), 'plan': plan, 'respaldo_verificado': receipt,
                        'usuario': user.pk, 'motivo': opts['motivo']}
            ruta = Path(opts['carpeta']) / 'custodia-previa-reinicio.json'
            if ruta.exists():
                if fingerprint(read_signed(ruta)) != fingerprint(custodia):
                    raise CommandError('La constancia de custodia previa no coincide.')
            else:
                save_signed(ruta, custodia)
            resumen = apply_reset(plan)
            perfil = user.perfiles.filter(activo=True, empresa__activa=True).first()
            evento = Auditoria.objects.create(empresa_id=perfil.empresa_id, usuario=user,
                accion='REINICIO_DATOS_MASISCAM', objeto_tipo='Empresa', objeto_id=str(perfil.empresa_id),
                detalle={'huella': fingerprint(plan), 'cantidades': plan['cantidades'], 'motivo': opts['motivo'],
                         'respaldo_sha256': backup_hash, 'custodia': str(ruta), 'resumen': resumen,
                         'archivos': [a['nombre'] for a in plan['archivos']]})
        borrados, conservados = purge_files(plan, receipt['archivos_locales'])
        self.stdout.write(canonical({'registros_eliminados': plan['cantidades']['registros'],
            'pdf_eliminados_o_archivos': len(borrados), 'archivos_conservados_sin_copia_o_alterados': conservados,
            'verificacion': resumen, 'auditoria': evento.pk, 'custodia': str(ruta),
            'usuarios_roles_y_credenciales': 'CONSERVADOS', 'secuencias': 'REINICIADAS'}))

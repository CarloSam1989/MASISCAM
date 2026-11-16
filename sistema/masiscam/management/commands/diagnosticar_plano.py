"""Diagnóstico de solo lectura; no conecta con Drive ni altera aplicabilidades."""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test import RequestFactory

from masiscam import tecnica_services as tecnica, flujo_views
from masiscam.models import ExpedienteMantenimiento, ReferenciaTecnica, Auditoria


class Command(BaseCommand):
    help = 'Explica el plano activo de EXP-E1-00000003 sin escribir datos.'

    def add_arguments(self, parser):
        parser.add_argument('numero', nargs='?', default='EXP-E1-00000003')

    def handle(self, *args, **opts):
        if connection.vendor != 'postgresql':
            raise CommandError('Requiere PostgreSQL para garantizar transacción de solo lectura.')
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            exp = ExpedienteMantenimiento._base_manager.select_related('registro__equipo', 'empresa').filter(
                numero=opts['numero']).first()
            if not exp:
                raise CommandError('Expediente no encontrado; no se sustituyen números ni IDs.')
            result = dict(expediente=exp.numero, id=exp.pk, empresa=exp.empresa_id,
                equipo=exp.registro.equipo_id, estado_equipo=exp.registro.equipo.estado,
                identidad=dict(expediente_id=exp.pk, registro_equipo_id=exp.registro_id,
                    registro_secuencia=exp.registro.secuencia, equipo_fisico_id=exp.registro.equipo_id,
                    nombre_equipo=exp.registro.equipo.nombre, serie=exp.registro.equipo.numero_serie),
                operativo=ExpedienteMantenimiento.objects.filter(pk=exp.pk).exists(),
                configuracion={name: getattr(settings, name, None) for name in (
                    'MASISCAM_TECNICA_LOCAL', 'MASISCAM_RECEPCION_LOCAL', 'MASISCAM_OPERACION_LOCAL')})
            refs = ReferenciaTecnica.objects.filter(empresa=exp.empresa, tipo='PLANO')
            result['planos'] = [dict(id=r.pk, codigo=r.codigo, revision=r.revision, estado=r.estado,
                origen=r.expediente_trabajo_id, origen_operativo=(None if not r.expediente_trabajo_id else
                    ExpedienteMantenimiento.objects.filter(pk=r.expediente_trabajo_id).exists()),
                documento=r.documento_id, archivo=r.documento.archivo.name,
                aplicabilidades=list(r.aplicabilidades.values('equipo_id', 'activa')),
                aplicable_al_equipo_actual=r.aplicabilidades.filter(equipo_id=exp.registro.equipo_id, activa=True).exists(),
                componentes=r.partes.count()) for r in refs.select_related('documento').order_by('codigo', '-revision')]
            result['aprobaciones'] = list(Auditoria.objects.filter(empresa=exp.empresa,
                accion='PLANO_REVISADO', objeto_id__in=[str(r['id']) for r in result['planos']])
                .values('objeto_id', 'usuario_id', 'creado_en', 'detalle'))
            result['taller'] = []
            for asignacion in exp.asignaciones_tecnicas.filter(fin__isnull=True).select_related('tecnico'):
                user = asignacion.tecnico
                entry = dict(usuario=user.pk, activo=user.is_active)
                try:
                    entry['visibles'] = list(tecnica.referencias_visibles(user, exp.empresa).filter(
                        tipo='PLANO').values_list('pk', flat=True))
                    request = RequestFactory().get('/')
                    request.user, request.empresa_activa = user, exp.empresa
                    ctx = flujo_views.contexto(request, exp)
                    entry['plano_activo'] = ctx['plano_activo'].pk if ctx['plano_activo'] else None
                except Exception as exc:
                    # Un error de permisos/configuración también es el resultado del diagnóstico.
                    entry['bloqueo'] = f'{type(exc).__name__}: {exc}'
                result['taller'].append(entry)
            template = Path(settings.BASE_DIR) / 'masiscam/templates/masiscam/flujo_detalle.html'
            result['codigo'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (Path(flujo_views.__file__), Path(tecnica.__file__), template)}
            result['mensaje_antiguo_en_plantilla'] = 'Sube el PDF para comenzar' in template.read_text(encoding='utf-8')
            self.stdout.write(json.dumps(result, default=str, ensure_ascii=False))

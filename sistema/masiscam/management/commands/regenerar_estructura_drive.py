from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from masiscam.models import Empresa, Equipo
from masiscam.services import GoogleDriveService, nombre_carpeta_drive
from masiscam.drive_rebuild import Plan, RebuildError, snapshot


class Command(BaseCommand):
    help = "Reubica el Drive de todos los equipos, sin copiar ni borrar documentos. Requiere --dry-run o --apply."

    def add_arguments(self, parser):
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--dry-run", action="store_true", help="Planificar sin escribir en Drive ni BD.")
        modes.add_argument("--apply", action="store_true", help="Ejecutar movimientos y actualizar vinculos Drive.")

    def handle(self, *args, **options):
        counts = dict(procesados=0, movidos=0, reutilizados=0, creados=0, omitidos=0, errores=0)
        apply = options["apply"]
        self.stdout.write("Modo: " + ("APPLY" if apply else "DRY-RUN (sin escrituras)"))
        # Same global lock as normal folder creation; serialize commands and workers.
        with transaction.atomic():
            if apply:
                Empresa.objects.select_for_update().order_by("pk").first()
            equipos = list(Equipo.objects.select_related("cliente", "proyecto").order_by("pk"))
            eligible = []
            for equipo in equipos:
                if not equipo.cliente_id or not equipo.numero_serie.strip():
                    counts["omitidos"] += 1
                    self.stdout.write(f"Equipo {equipo.pk}: OMITIDO (sin cliente o serie).")
                else:
                    eligible.append(equipo)
            equipos = eligible
            if not equipos:
                self.stdout.write("Sin equipos elegibles.")
            try:
                service = GoogleDriveService() if equipos else None
                nodes = snapshot(service) if equipos else {}
            except Exception:
                counts["errores"] += 1
                self.stdout.write("Resumen " + ", ".join(f"{key}={value}" for key, value in counts.items()))
                raise CommandError("No se pudo leer Drive. No se realizaron cambios.") from None
            resolved, paths = {}, {}
            for equipo in equipos:
                if equipo.cliente_id and equipo.numero_serie.strip():
                    key = (nombre_carpeta_drive(equipo.razon_social_cliente), equipo.tipo_producto, nombre_carpeta_drive(equipo.numero_serie))
                    paths.setdefault(key, []).append(equipo.pk)
            for equipo in equipos:
                counts["procesados"] += 1
                try:
                    key = (nombre_carpeta_drive(equipo.razon_social_cliente), equipo.tipo_producto, nombre_carpeta_drive(equipo.numero_serie))
                    if not equipo.razon_social_cliente.strip() or equipo.tipo_producto not in {"REDUCTOR", "BOMBA"}:
                        raise RebuildError("Razon social o tipo de producto invalido.")
                    if equipo.cliente.empresa_id != equipo.proyecto.empresa_id:
                        raise RebuildError("Cliente y equipo pertenecen a distintas empresas.")
                    if len(paths[key]) > 1:
                        raise RebuildError("Varios equipos comparten la misma razon social, tipo y serie.")
                    plan = Plan(nodes, equipo).build()
                except RebuildError as exc:
                    counts["errores"] += 1
                    self.stderr.write(f"Equipo {equipo.pk}: ERROR: {exc}")
                    continue
                moves = sum(op[0] == "move" for op in plan.operations)
                creates = sum(op[0] == "create" for op in plan.operations)
                self.stdout.write(f"Equipo {equipo.pk}: {' / '.join(key)}; movimientos={moves}, creaciones={creates}, reutilizados={plan.reused}")
                if apply:
                    try:
                        with transaction.atomic():
                            plan.execute(service, resolved)
                    except Exception:
                        counts["errores"] += 1
                        self.stderr.write(f"Equipo {equipo.pk}: ERROR de Drive/BD; ejecucion detenida. Reejecute para recuperar sin copias.")
                        break
                nodes = plan.nodes
                if apply:
                    # Substitute virtual IDs before planning the next equipment.
                    nodes = {resolved.get(id, id): dict(n, id=resolved.get(id, id), parents=[resolved.get(p, p) for p in n.get("parents", [])]) for id, n in nodes.items()}
                counts["movidos"] += moves
                counts["creados"] += creates
                counts["reutilizados"] += plan.reused
        self.stdout.write("Resumen " + ", ".join(f"{key}={value}" for key, value in counts.items()))
        if counts["errores"]:
            raise CommandError("Revise los equipos informados con errores. No se elimino ningun documento.")

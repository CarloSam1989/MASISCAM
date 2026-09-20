from django.core.management.base import BaseCommand, CommandError

from masiscam.models import Equipo
from masiscam.services import sincronizar_carpeta_equipo


class Command(BaseCommand):
    help = "Crea o recupera la carpeta Drive de un equipo MASISCAM sin duplicarla."

    def add_arguments(self, parser):
        parser.add_argument("equipo_id", type=int)

    def handle(self, *args, **options):
        equipo_id = options["equipo_id"]
        if not Equipo.objects.filter(pk=equipo_id).exists():
            raise CommandError("El equipo no existe.")
        try:
            carpeta_id = sincronizar_carpeta_equipo(equipo_id)
        except Exception:
            raise CommandError("No se pudo sincronizar la carpeta. Revise la configuración privada de Drive.") from None
        self.stdout.write(self.style.SUCCESS("Carpeta del equipo verificada y vínculo guardado."))

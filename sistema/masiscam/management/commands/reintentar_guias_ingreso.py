from django.core.management.base import BaseCommand, CommandError

from masiscam.models import EmisionGuiaIngreso
from masiscam.recepcion_tasks import generar_guia_ingreso


class Command(BaseCommand):
    help = "Lista emisiones de ingreso para recuperar tras caída del broker; solo encola con --encolar."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", type=int, required=True)
        parser.add_argument("--encolar", action="store_true")
        parser.add_argument("--limite", type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options["limite"] <= 1000:
            raise CommandError("El límite debe estar entre 1 y 1000.")
        # Generated PDFs are included so Drive errors can be retried using their
        # original reserved IDs. Never generate another version or number here.
        ids = list(EmisionGuiaIngreso.objects.filter(expediente__empresa_id=options["empresa"])
                   .order_by("pk").values_list("pk", flat=True)[:options["limite"]])
        for pk in ids:
            if options["encolar"]:
                try:
                    generar_guia_ingreso.apply_async(args=[pk], retry=False)
                except Exception:
                    raise CommandError("Cola no disponible; se conservan las emisiones.") from None
            self.stdout.write(f"Emisión {pk}: {'encolada' if options['encolar'] else 'sin cambios'}")

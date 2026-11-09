from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from masiscam.models import InformeFinal
from masiscam.cierre_tasks import generar_informe_final


class Command(BaseCommand):
    help='Lista reservas finales sin PDF; --encolar solo en sandbox local.'
    def add_arguments(self,parser): parser.add_argument('--encolar',action='store_true')
    def handle(self,*args,**options):
        if not getattr(settings,'MASISCAM_CIERRE_LOCAL',False): raise CommandError('Fase 6 local deshabilitada.')
        for em in InformeFinal.objects.filter(documento__isnull=True).order_by('pk'):
            self.stdout.write(f'{em.pk} {em.expediente.numero} v{em.version}')
            if options['encolar']: generar_informe_final.apply_async(args=[em.pk],retry=False)

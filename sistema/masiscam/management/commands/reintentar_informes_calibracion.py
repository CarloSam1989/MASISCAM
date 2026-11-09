from django.core.management.base import BaseCommand,CommandError
from django.conf import settings
from masiscam.models import EmisionCalibracion


class Command(BaseCommand):
    help='Lista reservas de calibración sin documento; --encolar solo en sandbox local.'
    def add_arguments(self,parser):
        parser.add_argument('--encolar',action='store_true')
    def handle(self,*args,**opts):
        if opts['encolar'] and not getattr(settings,'MASISCAM_CALIBRACION_LOCAL',False):
            raise CommandError('Calibración local deshabilitada.')
        from masiscam.calibracion_tasks import generar_informe_calibracion
        for em in EmisionCalibracion.objects.filter(documento__isnull=True).order_by('pk'):
            self.stdout.write(f'{em.pk} calibración {em.calibracion_id} PDF v{em.version} {em.estado}')
            if opts['encolar']:
                generar_informe_calibracion.apply_async(args=[em.pk],retry=False)

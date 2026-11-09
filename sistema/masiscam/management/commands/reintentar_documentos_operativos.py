from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from masiscam.models import EmisionOperativa


class Command(BaseCommand):
    help='Lista reservas operativas sin documento. --encolar solo para sandbox local.'

    def add_arguments(self,parser):
        parser.add_argument('--encolar',action='store_true')

    def handle(self,*args,**opts):
        if opts['encolar'] and not getattr(settings,'MASISCAM_OPERACION_LOCAL',False):
            raise CommandError('Fase 4 local deshabilitada.')
        from masiscam.operacion_tasks import generar_documento_operativo
        for em in EmisionOperativa.objects.filter(documento__isnull=True).order_by('pk'):
            self.stdout.write(f'{em.pk} {em.expediente.numero} {em.tipo} v{em.version} {em.estado}')
            if opts['encolar']:
                generar_documento_operativo.apply_async(args=[em.pk],retry=False)

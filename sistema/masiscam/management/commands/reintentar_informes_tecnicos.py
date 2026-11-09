from django.core.management.base import BaseCommand
from masiscam.models import EmisionInformeTecnico


class Command(BaseCommand):
    help='Lista informes técnicos recuperables; --encolar es explícito y solo local.'

    def add_arguments(self,parser):
        parser.add_argument('--encolar',action='store_true')

    def handle(self,*args,**options):
        from masiscam.tecnica_services import vigente
        from masiscam.tecnica_tasks import generar_informe_inicial
        from django.conf import settings
        for em in EmisionInformeTecnico.objects.select_related('diagnostico').all():
            if not vigente(em.diagnostico):
                continue
            self.stdout.write(f'{em.pk} · {em.estado} · {em.expediente.numero} v{em.version}')
            if options['encolar']:
                if not getattr(settings,'MASISCAM_TECNICA_LOCAL',False):
                    self.stderr.write('Operación local deshabilitada.')
                    return
                generar_informe_inicial.apply_async(args=[em.pk],retry=False)

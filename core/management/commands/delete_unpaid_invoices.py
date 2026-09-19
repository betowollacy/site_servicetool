from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Invoice

# Pagamento em andamento costuma ser confirmado em minutos: fatura criada a
# menos de 24h pode ter um checkout ainda aberto. --all ignora essa janela.
MIN_AGE = timedelta(hours=24)


class Command(BaseCommand):
    help = 'Exclui faturas de PEDIDO nao pagas (invoice_for=Order, status=Unpaid).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Apenas lista o que seria excluido, sem excluir.')
        parser.add_argument('--all', action='store_true',
                            help='Ignora a janela de 24h e exclui TODAS as faturas de pedido nao pagas.')

    def handle(self, *args, **options):
        qs = Invoice.objects.filter(invoice_for='Order', invoice_status='Unpaid')
        if not options['all']:
            qs = qs.filter(created_at__lt=timezone.now() - MIN_AGE)
        total = qs.count()

        if total == 0:
            self.stdout.write('Nenhuma fatura de pedido nao paga para excluir.')
            if not options['all']:
                self.stdout.write('(use --all para ignorar a janela de {}h)'.format(MIN_AGE.seconds // 3600))
            return

        linked_orders = qs.exclude(order_id__isnull=True).values('order_id').distinct().count()

        if options['dry_run']:
            for inv in qs.order_by('-created_at')[:50]:
                self.stdout.write('#{:<6} | {:<20} | R$ {} | {}'.format(
                    inv.id, (inv.customer_name or '')[:20],
                    inv.invoice_amount, inv.created_at.strftime('%d/%m/%Y %H:%M')))
            extra = total - 50
            if extra > 0:
                self.stdout.write('... e mais {} fatura(s).'.format(extra))
            self.stdout.write(self.style.WARNING(
                'Seria(m) excluida(s) {} fatura(s) vinculada(s) a {} pedido(s). '
                'Rode sem --dry-run para confirmar.'.format(total, linked_orders)))
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            'Excluida(s) {} fatura(s) de pedido nao paga(s) ({} pedido(s) desvinculado(s)).'.format(
                deleted, linked_orders)))
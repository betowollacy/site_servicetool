from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from core import notify, provider_api
from core.models import Api, CustomerOrder

# Janela em que um pedido Success ainda e reconsultado caso a resposta
# ainda nao contenha senha (provedores demoram a devolver a senha completa:
# na fonte direta a senha pode chegar muito depois da conclusao).
SUCCESS_CRED_GRACE = timedelta(hours=72)


class Command(BaseCommand):
    help = 'Sincroniza pedidos em andamento com o provedor e testa a conexao da API.'

    def add_arguments(self, parser):
        parser.add_argument('--test', type=int, help='Testa a conexao desta API (id).')
        parser.add_argument('--api', type=int, help='Sincroniza apenas pedidos desta API.')

    def handle(self, *args, **options):
        test_id = options.get('test')
        if test_id:
            api = Api.objects.filter(id=test_id).first()
            if not api:
                self.stderr.write('API {} nao encontrada.'.format(test_id))
                return
            try:
                info = provider_api.account_info(api)
            except provider_api.ProviderError as exc:
                self.stderr.write('Falha: {}'.format(exc))
                return
            self.stdout.write(self.style.SUCCESS(
                'Conectado. Conta: {} | Saldo: {}'.format(info['mail'], info['credit'])))
            return

        from core.notify import _split_creds
        pending = list(CustomerOrder.objects.filter(
            service_status__in=['In Process', 'Waiting Action']).select_related('service__inventory'))
        # Success recentes cuja resposta ainda nao tem senha: a senha do
        # provedor pode chegar minutos depois da conclusao.
        if not options.get('api'):
            recent_success = list(CustomerOrder.objects.filter(
                service_status='Success',
                updated_at__gte=timezone.now() - SUCCESS_CRED_GRACE,
            ).select_related('service__inventory'))
            pending += [
                o for o in recent_success
                if (o.trx_id or '').strip()
                and not _split_creds(((o.service_comments or '') or (o.replied_in or '')).strip())[1]
            ]
        qs = pending
        if options.get('api'):
            qs = [o for o in qs if o.service and o.service.api_id == options['api']]

        total = ok = 0
        for order in qs:
            total += 1
            try:
                if (order.trx_id or '').strip():
                    if provider_api.sync_local_order(order):
                        ok += 1
                elif order.service_status == 'In Process':
                    delivered, _ = provider_api.deliver_from_inventory(order)
                    if delivered:
                        ok += 1
                        notify.send_telegram(notify.completed_order_message(order))
            except Exception as exc:
                self.stderr.write('Erro no pedido #{}: {}'.format(order.id, exc))
        self.stdout.write('Sincronizados {} de {} pedidos.'.format(ok, total))
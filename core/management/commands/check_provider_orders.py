from django.core.management.base import BaseCommand

from core import provider_api
from core.models import Api, CustomerOrder


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

        qs = CustomerOrder.objects.filter(
            service_status='In Process',
        ).exclude(trx_id__isnull=True).exclude(trx_id='')
        if options.get('api'):
            qs = qs.filter(service__api_id=options['api'])

        total = ok = 0
        for order in qs:
            total += 1
            try:
                if provider_api.sync_local_order(order):
                    ok += 1
            except Exception as exc:
                self.stderr.write('Erro no pedido #{}: {}'.format(order.id, exc))
        self.stdout.write('Sincronizados {} de {} pedidos.'.format(ok, total))
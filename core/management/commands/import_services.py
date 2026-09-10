import json, os

from django.core.management.base import BaseCommand
from core.models import ServiceGroup, ServiceList

GROUPS = {
    'remote': ('Aluguel de Ferramentas Digitais', 'Server Service'),
    'imei': ('Serviço de IMEI e Consultas', 'IMEI Service'),
    'server': ('Ativação de Ferramentas e Créditos', 'Credit Service'),
    'file': ('Arquivos para Serviço', 'Server Service'),
}
DATA_FILE = os.path.join(os.path.dirname(__file__), 'services_reference.json')


class Command(BaseCommand):
    help = "Importa o catalogo de servicos de referencia (categorias, servicos e precos em BRL)."

    def handle(self, *args, **options):
        with open(DATA_FILE, encoding='utf-8') as f:
            items = json.load(f)

        groups = {}
        for cat, (name, _stype) in GROUPS.items():
            g, _ = ServiceGroup.objects.get_or_create(
                slug=cat,
                defaults={'name': name, 'status': 'Active'},
            )
            groups[cat] = g
        self.stdout.write('Grupos: %d' % len(groups))

        created = updated = 0
        for it in items:
            obj, was_created = ServiceList.objects.update_or_create(
                slug=it['slug'],
                defaults={
                    'service_type': GROUPS[it['cat']][1],
                    'service_group': groups[it['cat']],
                    'title': it['title'],
                    'thumbnail': it['img'],
                    'status': 'Active',
                    'duration': it['duration'],
                    'delivery_time': it['duration'],
                    'price_type': 'fixed_price',
                    'original_price': it['price_brl'],
                    'min_qnt': '1',
                    'max_qnt': '',
                    'process_type': 'Manual',
                    'recommended': 1,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            'Import concluido: %d criados, %d atualizados.' % (created, updated)))

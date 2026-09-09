from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import (
    Currency, PaymentGateway, ServiceGroup, ServiceInput, ServiceList, Slider,
    SystemSetting,
)


class Command(BaseCommand):
    help = "Populate inicial: configuracoes do site, moedas, gateways, sliders, grupos e servicos de exemplo."

    def handle(self, *args, **options):
        self.seed_settings()
        self.seed_currencies()
        self.seed_gateways()
        self.seed_sliders()
        self.seed_services()
        self.stdout.write(self.style.SUCCESS("Seed concluido com sucesso."))

    def set_setting(self, key, value):
        obj, created = SystemSetting.objects.get_or_create(key=key, defaults={'value': str(value)})
        if not created:
            obj.value = str(value)
            obj.save(update_fields=['value'])
        return obj

    def seed_settings(self):
        settings = {
            'siteTitle': 'SERVICETOOL',
            'siteMetaTitle': 'SERVICETOOL - Loja Digital GSM',
            'siteMetaDes': 'SERVICETOOL - Painel para revenda de serviços digitais: ativação, créditos e IMEI.',
            'siteKeyword': 'servicetool, smm, gsm, imei, credit, servidor, revenda, painel',
            'siteLogo': '/static/resource/logo.png',
            'siteFav': '/static/resource/fav.png',
            'siteWhatsappUrl': '',
            'siteTelegramUrl': '',
            'siteFacebookUrl': '',
            'siteTwitterUrl': '',
            'siteYoutubeUrl': '',
            'siteEmailAddress': 'support@servicetool.com',
            'sitePhoneNumber': '',
            'siteAddress': '',
            'headerCode': '',
            'themeMode': 'light',
            'themeColor': 'preset-1',
            'currency': 'USD',
            'currencyIcon': '$',
            'addAmount': '100',
        }
        for key, value in settings.items():
            self.set_setting(key, value)
        self.stdout.write(self.style.SUCCESS('  - SystemSettings: %d chaves' % len(settings)))

    def seed_currencies(self):
        currencies = [
            ('USD', 'US Dollar', '$', '1.0000'),
            ('BDT', 'Bangladeshi Taka', '৳', '110.0000'),
            ('EUR', 'Euro', '€', '0.9200'),
            ('GBP', 'British Pound', '£', '0.7900'),
            ('BRL', 'Brazilian Real', 'R$', '5.1000'),
            ('PKR', 'Pakistani Rupee', '₨', '280.0000'),
            ('INR', 'Indian Rupee', '₹', '84.0000'),
            ('CNY', 'Chinese Yuan', '¥', '7.2000'),
        ]
        for i, (code, name, icon, rate) in enumerate(currencies):
            Currency.objects.update_or_create(
                code=code,
                defaults={'name': name, 'icon': icon, 'rate': Decimal(rate), 'country': name, 'status': 'Active'},
            )
        self.stdout.write(self.style.SUCCESS('  - Moedas: %d' % len(currencies)))

    def seed_gateways(self):
        gateways = [
            ('bKash', 'BDT', '2.00', '/static/resource/bkash_logo.png'),
            ('Binance', 'USD', '1.00', '/static/resource/binance_logo.png'),
        ]
        for i, (name, currency, charge, logo) in enumerate(gateways):
            PaymentGateway.objects.update_or_create(
                name=name,
                defaults={
                    'logo': logo,
                    'currency_code': currency,
                    'charge': Decimal(charge),
                    'country': name,
                    'status': 'Active',
                },
            )
        self.stdout.write(self.style.SUCCESS('  - Gateways: %d' % len(gateways)))

    def seed_sliders(self):
        Slider.objects.update_or_create(
            img='/static/resource/slider.webp',
            defaults={'width': '1100', 'height': '400', 'status': 'Active', 'url': '/'},
        )
        self.stdout.write(self.style.SUCCESS('  - Slider OK'))

    def seed_services(self):
        group_server, _ = ServiceGroup.objects.get_or_create(
            slug='server', defaults={'name': 'Activation/Server', 'status': 'Active', 'thumbnail': ''},
        )
        group_credit, _ = ServiceGroup.objects.get_or_create(
            slug='credit', defaults={'name': "Tool's Credit Refill", 'status': 'Active', 'thumbnail': ''},
        )
        group_imei, _ = ServiceGroup.objects.get_or_create(
            slug='imei', defaults={'name': 'IMEI/SN Service', 'status': 'Active', 'thumbnail': ''},
        )

        def add_service(slug, title, stype, group, price, delivery, inputs=()):
            svc, created = ServiceList.objects.get_or_create(
                slug=slug,
                defaults={
                    'title': title,
                    'service_type': stype,
                    'service_group': group,
                    'original_price': Decimal(price),
                    'price_type': 'fixed_price',
                    'customer_profit_amount': Decimal('0.00'),
                    'process_type': 'Manual',
                    'status': 'Active',
                    'delivery_time': delivery,
                    'duration': delivery,
                    'recommended': 1,
                    'thumbnail': '/static/resource/default-thumb.png',
                },
            )
            for inp in inputs:
                ServiceInput.objects.get_or_create(service=svc, name=inp)
            return svc

        add_service('iphone-unlock', 'Serviço de Desbloqueio de iPhone', 'Server Service', group_server, '25.00', '24 horas',
                    inputs=['Nome de Usuário', 'Senha'])
        add_service('samsung-unlock', 'Serviço de Desbloqueio de Samsung', 'Server Service', group_server, '15.00', '12 horas',
                    inputs=['Número IMEI'])
        add_service('credit-refill-1000', 'Recarga de Créditos (1000 Créditos)', 'Credit Service', group_credit, '5.00', 'Instantâneo',
                    inputs=['Nome de Usuário da Ferramenta'])
        add_service('credit-refill-5000', 'Recarga de Créditos (5000 Créditos)', 'Credit Service', group_credit, '20.00', 'Instantâneo',
                    inputs=['Nome de Usuário da Ferramenta'])
        add_service('imei-repair', 'Reparo de IMEI', 'IMEI Service', group_imei, '10.00', '6 horas',
                    inputs=['Número IMEI', 'Modelo'])

        self.stdout.write(self.style.SUCCESS('  - Servicos de exemplo criados'))

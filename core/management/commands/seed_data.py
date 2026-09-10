from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import (
    Currency, PaymentGateway, Slider, SystemSetting,
)


class Command(BaseCommand):
    help = "Seed inicial de producao: configuracoes do site e moeda BRL (obrigatorios)."

    def handle(self, *args, **options):
        self.seed_settings()
        self.seed_currencies()
        self.seed_gateways()
        self.seed_sliders()
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
            'siteMetaTitle': 'SERVICETOOL - SERVIDOR BRASILEIRO',
            'siteMetaDes': 'SERVICETOOL - Painel para revenda de serviços digitais: ativação, créditos e Aluguel.',
            'siteKeyword': 'servicetool, smm, gsm, imei, credit, servidor, revenda, painel',
            'siteLogo': '/static/resource/logo_servicetool.png',
            'siteFav': '/static/resource/favicon.ico',
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
            'currency': 'BRL',
            'currencyIcon': 'R$',
            'addAmount': '100',
        }
        for key, value in settings.items():
            self.set_setting(key, value)
        self.stdout.write(self.style.SUCCESS('  - SystemSettings: %d chaves' % len(settings)))

    def seed_currencies(self):
        currencies = [
            ('BRL', 'Brazilian Real', 'R$', '1.0000'),
            ('USDT', 'USDT Stablecoin', '₮', '5.7000'),
        ]
        for i, (code, name, icon, rate) in enumerate(currencies):
            Currency.objects.update_or_create(
                code=code,
                defaults={'name': name, 'icon': icon, 'rate': Decimal(rate), 'country': name, 'status': 'Active'},
            )
        self.stdout.write(self.style.SUCCESS('  - Moedas: %d' % len(currencies)))

    def seed_gateways(self):
        gateways = [
            ('Asaas', 'BRL', '0.00', '/static/resource/asaas.svg'),
            ('Binance', 'USDT', '0.00', '/static/resource/binance_logo.png'),
            ('bKash', 'BRL', '0.00', '/static/resource/bkash_logo.png'),
        ]
        for name, currency, charge, logo in gateways:
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
        sliders = [
            ('/static/resource/slider_servicetool.webp', ''),
            ('/static/resource/promo_usbfix.webp', 'https://usbfix.site/'),
        ]
        for img, url in sliders:
            Slider.objects.update_or_create(
                img=img,
                defaults={'url': url, 'status': 'Active', 'width': '', 'height': ''},
            )
        self.stdout.write(self.style.SUCCESS('  - Sliders: %d' % len(sliders)))

    def seed_services(self):
        pass

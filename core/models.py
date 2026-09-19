import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.contrib.auth import hashers
from django.conf import settings


class User(AbstractUser):
    """Administrative / staff user (Laravel 'users' table)."""
    phone = models.CharField(max_length=30, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    status = models.BooleanField(default=True)
    theme = models.CharField(max_length=20, default='light')  # dark / light
    theme_color = models.CharField(max_length=20, default='preset-1')
    logo = models.CharField(max_length=500, blank=True, null=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'users'

    def __str__(self):
        return self.username


class Customer(models.Model):
    ROLES = [
        ('Web Owner', 'Web Owner'),
        ('Distributor', 'Distributor'),
        ('Reseller', 'Reseller'),
        ('Customer', 'Customer'),
    ]
    STATUS = [
        ('Active', 'Active'),
        ('Blocked', 'Blocked'),
    ]

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    mobile = models.CharField(max_length=30, blank=True, null=True)
    cpf_cnpj = models.CharField(max_length=30, blank=True, null=True)
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=50, choices=ROLES, default='Customer')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=10, default='USD')
    over_due = models.CharField(max_length=10, default='off')  # allow / off
    api_allow = models.CharField(max_length=10, default='on')  # on / ''
    api_key = models.CharField(max_length=255, blank=True, null=True)
    api_ip = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS, default='Active')
    google2fa_enabled = models.BooleanField(default=False)
    otp_enabled = models.BooleanField(default=False)
    google2fa_secret = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    session_token = models.CharField(max_length=64, blank=True, null=True)
    last_seen = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'customers'

    def __str__(self):
        return self.name

    @staticmethod
    def make_password(raw):
        return hashers.make_password(raw)

    def check_password(self, raw_password):
        return hashers.check_password(raw_password, self.password or '')

    def touch_activity(self, min_interval=60):
        """Marca o cliente como visto agora (no maximo a cada min_interval segundos)."""
        now = timezone.now()
        if self.last_seen is None or (now - self.last_seen).total_seconds() >= min_interval:
            self.last_seen = now
            self.save(update_fields=['last_seen'])

    @property
    def currency_icon(self):
        cur = Currency.objects.filter(code=self.currency).first()
        return cur.icon if cur else '$'


class Currency(models.Model):
    country = models.CharField(max_length=100, blank=True, null=True)
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=10, default='$')
    rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('1.0000'))
    type = models.CharField(max_length=20, blank=True, null=True)  # Default / N/A
    status = models.CharField(max_length=20, default='Active')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'currencies'

    def __str__(self):
        return f"{self.code} - {self.name}"


class ServiceGroup(models.Model):
    thumbnail = models.CharField(max_length=500, blank=True, null=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True, null=True)
    status = models.CharField(max_length=20, default='Active')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'service_groups'

    def __str__(self):
        return self.name


# Códigos de dados que podem ser solicitados na compra do serviço.
# 'text' vira um campo de texto; 'file' vira um upload de arquivo.
SERVICE_COLLECT_DATA_CHOICES = [
    ('user', 'Usuário'),
    ('email', 'E-mail'),
    ('senha', 'Senha'),
    ('serial', 'Serial Number'),
    ('ecid', 'Ecid'),
    ('imei', 'Imei'),
    ('anydesk', 'Pedir acesso do AnyDesk (ID e senha)'),
    ('lock_photo', 'Pedir foto da tela de bloqueio'),
    ('whatsapp', 'Pedir WhatsApp do cliente'),
    ('image_link', 'Link da Imagem'),
    ('upload_photo', 'Enviar foto'),
    ('upload_logo', 'Enviar logo'),
]

COLLECT_FIELD_TYPES = {
    'user': 'text',
    'email': 'text',
    'senha': 'text',
    'serial': 'text',
    'ecid': 'text',
    'imei': 'text',
    'anydesk': 'text',
    'whatsapp': 'text',
    'image_link': 'text',
    'lock_photo': 'file',
    'upload_photo': 'file',
    'upload_logo': 'file',
}

COLLECT_FIELD_NAMES = {
    'user': 'Usuário',
    'email': 'E-mail da Ferramenta',
    'senha': 'Senha',
    'serial': 'Serial Number',
    'ecid': 'Ecid',
    'imei': 'IMEI',
    'anydesk': 'Acesso do AnyDesk',
    'whatsapp': 'WhatsApp',
    'image_link': 'Link da Imagem',
    'lock_photo': 'Foto da tela de bloqueio',
    'upload_photo': 'Foto',
    'upload_logo': 'Logo',
}

# Alias de nomes de campo equivalentes a cada código de 'Dados a solicitar'.
# Campos importados da API (ex.: 'E-mail', 'Senha') recebem esses nomes nas
# 'Campos de Entrada'; ao renderizar, eles seguem as checkboxes do painel e
# não duplicam o pedido de dados quando desmarcados.
COLLECT_FIELD_ALIASES = {
    'user': ('Usuário', 'Usuario', 'Usuário da Ferramenta'),
    'email': ('E-mail', 'Email', 'E-mail da Ferramenta', 'Email da Ferramenta', 'E-mail da ferramenta'),
    'senha': ('Senha', 'Password', 'Senha da Ferramenta', 'Senha do Acesso', 'Password da Ferramenta'),
    'serial': ('Serial Number', 'Serial', 'Serial No', 'Número de Série', 'Numero de Serie'),
    'ecid': ('Ecid', 'ECID'),
    'imei': ('IMEI', 'Imei', 'Número do IMEI', 'Numero do IMEI'),
    'image_link': ('Link da Imagem', 'Link da imagem', 'Link da Foto', 'Image Link'),
    'anydesk': ('Acesso do AnyDesk', 'AnyDesk', 'ID e senha do AnyDesk'),
    'whatsapp': ('WhatsApp', 'Whatsapp', 'Número de WhatsApp'),
}

# Compatibilidade: opções do antigo campo `collect_extras` (não usado mais).
SERVICE_COLLECT_EXTRA_CHOICES = [
    ('anydesk', 'Acesso do AnyDesk'),
    ('lock_photo', 'Foto da tela de bloqueio'),
    ('whatsapp', 'WhatsApp'),
]


def collect_data_codes(value):
    return [c.strip() for c in (value or '').split(',') if c.strip()]


def collect_field_code_by_name(name):
    """Dado um nome de campo (ex.: 'E-mail'), devolve o código do painel que o
    gerencia (ex.: 'email'), ou None se for um campo livre personalizado."""
    key = (name or '').strip().lower()
    for code, aliases in COLLECT_FIELD_ALIASES.items():
        for alias in aliases:
            if alias.lower() == key:
                return code
    return None


class ServiceList(models.Model):
    SERVICE_TYPES = [
        ('Server Service', 'Server Service'),
        ('Credit Service', 'Credit Service'),
        ('Activation Service', 'Activation Service'),
        ('IMEI Service', 'IMEI Service'),
        ('Method Service', 'Method Service'),
    ]
    PRICE_TYPES = [
        ('fixed_price', 'Fixed Price'),
        ('fixed_profit', 'Fixed Profit'),
        ('percentage_base', 'Percentage Base'),
    ]
    PROCESS_TYPES = [
        ('Auto', 'Auto'),
        ('Manual', 'Manual'),
    ]
    COLLECT_FIELDS_CHOICES = [
        ('both', 'Usuário e e-mail'),
        ('user', 'Somente usuário'),
        ('email', 'Somente e-mail'),    ('serial', 'Serial Number'),
    ]
    COLLECT_DATA_CHOICES = SERVICE_COLLECT_DATA_CHOICES
    COLLECT_EXTRA_CHOICES = SERVICE_COLLECT_EXTRA_CHOICES  # legado

    service_type = models.CharField(max_length=50, choices=SERVICE_TYPES, default='Server Service')
    service_group = models.ForeignKey(ServiceGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='services')
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=500, blank=True, null=True)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    thumbnail = models.CharField(max_length=500, blank=True, null=True)
    status = models.CharField(max_length=20, default='Active')
    duration = models.CharField(max_length=100, blank=True, null=True)
    delivery_time = models.CharField(max_length=100, blank=True, null=True)

    price_type = models.CharField(max_length=30, choices=PRICE_TYPES, default='fixed_price')
    original_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    customer_profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    reseller_profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    distributor_profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    webowner_profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    min_qnt = models.CharField(max_length=30, blank=True, null=True)
    max_qnt = models.CharField(max_length=30, blank=True, null=True)
    screenshot = models.CharField(max_length=500, blank=True, null=True)
    width = models.CharField(max_length=20, blank=True, null=True)
    height = models.CharField(max_length=20, blank=True, null=True)
    tool_download = models.TextField(blank=True, null=True)
    login_url = models.TextField(blank=True, null=True)
    register_url = models.TextField(blank=True, null=True)
    article = models.TextField(blank=True, null=True)

    service_tags = models.CharField(max_length=500, blank=True, null=True)
    meta_description = models.CharField(max_length=500, blank=True, null=True)
    kw1 = models.CharField(max_length=255, blank=True, null=True)
    kw2 = models.CharField(max_length=255, blank=True, null=True)
    kw3 = models.CharField(max_length=255, blank=True, null=True)
    kw4 = models.CharField(max_length=255, blank=True, null=True)
    kw5 = models.CharField(max_length=255, blank=True, null=True)

    process_type = models.CharField(max_length=20, choices=PROCESS_TYPES, default='Manual')
    api = models.ForeignKey('Api', on_delete=models.SET_NULL, null=True, blank=True, related_name='services')
    api_enabled = models.BooleanField(default=True, verbose_name='API ativa')
    collect_login = models.BooleanField(default=True, verbose_name='Pedir usuário/e-mail na compra')
    collect_fields = models.CharField(max_length=10, choices=COLLECT_FIELDS_CHOICES, default='both', verbose_name='Dados a solicitar na compra')
    collect_extras = models.CharField(max_length=120, blank=True, default='', choices=COLLECT_EXTRA_CHOICES, verbose_name='Dados adicionais a solicitar na compra')
    collect_data = models.CharField(max_length=300, blank=True, default='', verbose_name='Dados a solicitar na compra')

    @property
    def collect_data_list(self):
        return collect_data_codes(self.collect_data)

    @staticmethod
    def collect_field_name(code):
        return COLLECT_FIELD_NAMES.get(code, '')

    @staticmethod
    def collect_field_type(code):
        return COLLECT_FIELD_TYPES.get(code, 'text')

    inventory = models.ForeignKey('Inventory', on_delete=models.SET_NULL, null=True, blank=True, related_name='services')
    referenceid = models.CharField(max_length=255, blank=True, null=True)
    CAROUSEL_CHOICES = [
        ('promocoes', 'Promoções do Dia'),
        ('desbloqueios', 'Métodos de Desbloqueio'),
    ]
    carousel = models.CharField(max_length=20, choices=CAROUSEL_CHOICES, blank=True, default='')
    sells = models.IntegerField(default=0)
    views = models.IntegerField(default=0)
    recommended = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'service_lists'

    def __str__(self):
        return self.title

    @property
    def tags_list(self):
        if not self.service_tags:
            return []
        return [t.strip() for t in self.service_tags.split(',') if t.strip()]

    @property
    def price_for(self):
        """Base (customer) price in USD from the configured price type."""
        if self.price_type == 'fixed_price':
            return self.original_price
        return self.original_price


CREDIT_SERVICE_EXTRA_FIELDS = ('Quantidade de Créditos', 'Usuário', 'E-mail da Ferramenta')

ACTIVATION_SERVICE_EXTRA_FIELDS = ('Usuário', 'E-mail da Ferramenta')

METHOD_SERVICE_EXTRA_FIELDS = ('Instruções',)


class ServiceInput(models.Model):
    service = models.ForeignKey(ServiceList, on_delete=models.CASCADE, related_name='service_fields')
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'service_inputs'

    def __str__(self):
        return self.name


class CustomPrice(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='custom_prices')
    service = models.ForeignKey(ServiceList, on_delete=models.CASCADE, related_name='custom_prices')
    profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'custom_prices'

    def __str__(self):
        return f"{self.customer} - {self.service}"


class TempRegister(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    mobile = models.CharField(max_length=30, blank=True, null=True)
    password = models.CharField(max_length=255)
    currency = models.CharField(max_length=10, default='USD')
    token = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'temp_registers'

    def __str__(self):
        return self.email


class PasswordReset(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, null=True, blank=True, related_name='password_resets')
    email = models.EmailField()
    token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'password_resets'

    def __str__(self):
        return self.email


class CustomerOrder(models.Model):
    SERVICE_STATUS = [
        ('Waiting Action', 'Waiting Action'),
        ('In Process', 'In Process'),
        ('Success', 'Success'),
        ('Rejected', 'Rejected'),
    ]
    SERVICE_TYPES = [
        ('server_service', 'Server'),
        ('credit_service', 'Credit'),
        ('activation_service', 'Activation'),
        ('imei_service', 'IMEI'),
        ('method_service', 'Method'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='orders')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='created_orders')
    service = models.ForeignKey(ServiceList, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    service_status = models.CharField(max_length=30, choices=SERVICE_STATUS, default='Waiting Action')
    service_type = models.CharField(max_length=30, choices=SERVICE_TYPES, default='server_service')
    service_qnt = models.CharField(max_length=30, blank=True, null=True)
    service_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    service_title = models.CharField(max_length=255, blank=True, null=True)
    service_input1 = models.CharField(max_length=500, blank=True, null=True)
    seen = models.CharField(max_length=10, default='false')
    payment_methode = models.CharField(max_length=100, blank=True, null=True)
    trx_id = models.CharField(max_length=255, blank=True, null=True)
    process_type = models.CharField(max_length=20, default='Manual')
    replied_in = models.CharField(max_length=500, blank=True, null=True)
    service_comments = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'customer_orders'
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} - {self.service_title}"


class OrderInput(models.Model):
    order = models.ForeignKey(CustomerOrder, on_delete=models.CASCADE, related_name='order_inputs')
    field_name = models.CharField(max_length=255)
    field_value = models.CharField(max_length=1000)

    class Meta:
        db_table = 'order_inputs'

    def __str__(self):
        return f"{self.field_name}: {self.field_value}"


class Statement(models.Model):
    TYPES = [
        ('Credit', 'Credit'),
        ('Debit', 'Debit'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='statements')
    description = models.CharField(max_length=500)
    type = models.CharField(max_length=10, choices=TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance = models.DecimalField(max_digits=12, decimal_places=2)
    order = models.ForeignKey(CustomerOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='statements')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'statements'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.type} {self.amount}"


class Invoice(models.Model):
    STATUS = [
        ('Paid', 'Paid'),
        ('Unpaid', 'Unpaid'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='invoices')
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    invoice_for = models.CharField(max_length=255, blank=True, null=True)
    invoice_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    customer_currency = models.CharField(max_length=10, blank=True, null=True)
    payment_gateway = models.CharField(max_length=100, blank=True, null=True)
    trx_id = models.CharField(max_length=255, blank=True, null=True)
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_currency = models.CharField(max_length=10, blank=True, null=True)
    invoice_status = models.CharField(max_length=20, choices=STATUS, default='Unpaid')
    invoice_title = models.CharField(max_length=255, blank=True, null=True)
    order = models.ForeignKey('CustomerOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    customer_mobile = models.CharField(max_length=30, blank=True, null=True)
    customer_email = models.EmailField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'
        ordering = ['-created_at']

    def __str__(self):
        return f"Invoice #{self.id}"


class SystemSetting(models.Model):
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'system_settings'

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=''):
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj and obj.value not in (None, '') else default


class Slider(models.Model):
    img = models.CharField(max_length=500, blank=True, null=True)
    width = models.CharField(max_length=20, blank=True, null=True)
    height = models.CharField(max_length=20, blank=True, null=True)
    status = models.CharField(max_length=20, default='Active')
    url = models.CharField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sliders'

    def __str__(self):
        return self.img or 'Slider'


class PaymentGateway(models.Model):
    logo = models.CharField(max_length=500, blank=True, null=True)
    name = models.CharField(max_length=100)  # noqa: N815
    currency_code = models.CharField(max_length=10, blank=True, null=True)
    charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    country = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, default='Active')
    bkash_app_key = models.CharField(max_length=255, blank=True, null=True)
    bkash_app_secret = models.CharField(max_length=255, blank=True, null=True)
    bkash_username = models.CharField(max_length=255, blank=True, null=True)
    bkash_password = models.CharField(max_length=255, blank=True, null=True)
    bkash_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    binance_api_key = models.CharField(max_length=255, blank=True, null=True)
    binance_secret_key = models.CharField(max_length=255, blank=True, null=True)
    binance_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    binance_private_key = models.TextField(blank=True, null=True)
    asaas_api_key = models.CharField(max_length=500, blank=True, null=True)
    asaas_sandbox = models.BooleanField(default=True)
    class Meta:
        db_table = 'payment_gateways'

    def __str__(self):
        return self.name

    # Theme/template compatibility aliases (blade used upright attributes)
    @property
    def NAME(self):  # noqa: N802
        return self.name

    @property
    def LOGO(self):  # noqa: N802
        return self.logo

    @property
    def CURRENCY_CODE(self):  # noqa: N802
        return self.currency_code

    @property
    def CHARGE(self):  # noqa: N802
        return self.charge

    @property
    def STATUS(self):  # noqa: N802
        return self.status


class Api(models.Model):
    api_name = models.CharField(max_length=255)
    api_type = models.CharField(max_length=50, blank=True, null=True)
    api_url = models.CharField(max_length=500, blank=True, null=True)
    api_username = models.CharField(max_length=255, blank=True, null=True)
    api_key = models.CharField(max_length=500, blank=True, null=True)
    api_pin = models.CharField(max_length=255, blank=True, null=True,
                               help_text='PIN mestre usado na autenticação de compras (ex.: RITUNLOCKER).')
    panel_url = models.CharField(
        max_length=500, blank=True, null=True,
        help_text='URL do painel web do provedor (ex.: https://ritunlocker.com). '
                  'Usado para buscar a resposta completa (login + senha) quando a '
                  'API não devolve a senha. Aceita {trx} para a URL do pedido.')
    panel_user = models.CharField(max_length=255, blank=True, null=True,
                                  help_text='Login do painel web (não vai pro cliente).')
    panel_pass = models.CharField(max_length=255, blank=True, null=True,
                                  help_text='Senha do painel web (não vai pro cliente).')
    status = models.CharField(max_length=20, default='Active')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    price_type = models.CharField(max_length=30, default='fixed_price')
    price_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'),
                                     help_text='Câmbio USD→BRL usado para sugerir preço. 0 = não converter.')
    price_markup = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                       help_text='Margem (%) adicionada sobre o custo do provedor.')
    customer_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    reseller_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    distributor_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    webowner_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        db_table = 'apis'

    def __str__(self):
        return self.api_name


class RemoteServiceList(models.Model):
    SERVICE_TYPES = [
        ('server_service', 'Server'),
        ('credit_service', 'Credit'),
        ('imei_service', 'IMEI'),
    ]
    api = models.ForeignKey(Api, on_delete=models.CASCADE, null=True, blank=True, related_name='remote_services')
    referenceid = models.CharField(max_length=255, blank=True, null=True)
    SERVICETYPE = models.CharField(max_length=50)
    SERVICENAME = models.CharField(max_length=255)
    CREDIT = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    added = models.BooleanField(default=False)

    class Meta:
        db_table = 'remote_service_lists'

    def __str__(self):
        return self.SERVICENAME


class RemoteServiceInput(models.Model):
    remote_service = models.ForeignKey(RemoteServiceList, on_delete=models.CASCADE, related_name='service_fields')
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'remote_service_inputs'

    def __str__(self):
        return self.name


class EmailConfig(models.Model):
    name = models.CharField(max_length=255)
    mail_driver = models.CharField(max_length=100, blank=True, null=True)
    mail_host = models.CharField(max_length=255, blank=True, null=True)
    mail_port = models.CharField(max_length=20, blank=True, null=True)
    encryption = models.CharField(max_length=20, blank=True, null=True)
    status = models.CharField(max_length=20, default='Active')
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = 'email_configs'

    def __str__(self):
        return self.name


class MailData(models.Model):
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'mail_data'

    def __str__(self):
        return self.key


class ApiLog(models.Model):
    log = models.TextField(blank=True, null=True)
    log_for = models.CharField(max_length=255, blank=True, null=True)
    api = models.ForeignKey(Api, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_logs'

    def __str__(self):
        return self.log_for or 'log'


class GatewayLog(models.Model):
    payment_gateway = models.CharField(max_length=100, blank=True, null=True)
    payment_for = models.CharField(max_length=255, blank=True, null=True)
    payment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True)
    invoice_status = models.CharField(max_length=20, blank=True, null=True)
    create_payment = models.TextField(blank=True, null=True)
    execute_payment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'gateway_logs'

    def __str__(self):
        return f"{self.payment_gateway} - {self.payment_amount}"


class PaymentDeposit(models.Model):
    name = models.CharField(max_length=100, blank=True, null=True)
    gateway_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    gateway_note = models.CharField(max_length=100, blank=True, null=True)
    qrcode_url = models.CharField(max_length=1000, blank=True, null=True)
    pix_code = models.TextField(blank=True, null=True)
    gateway_payment_id = models.CharField(max_length=500, blank=True, null=True)
    gateway_data = models.TextField(blank=True, null=True)
    cancel_url = models.CharField(max_length=1000, blank=True, null=True)
    checkout_url = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, default='Pending')
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                     help_text='Valor líquido recebido após as taxas do gateway (ex.: netValue do Asaas).')
    gateway_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                      help_text='Taxa cobrada pelo gateway sobre o depósito.')
    order = models.ForeignKey(CustomerOrder, on_delete=models.SET_NULL, null=True, blank=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payment_deposits'

    def __str__(self):
        return self.name or str(self.id)


class Inventory(models.Model):
    name = models.CharField(max_length=255)
    available_code = models.IntegerField(default=0)
    availableCount = models.IntegerField(default=0)
    soldOutCount = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'inventories'

    def __str__(self):
        return self.name


class InventoryDataQuerySet(models.QuerySet):
    def available(self):
        """Credenciais que ainda podem ser vendidas (status Available e com usos restantes)."""
        return self.filter(status='Available', uses_count__lt=models.F('max_uses'))


class InventoryData(models.Model):
    STATUS = [
        ('Available', 'Available'),
        ('Sold out', 'Sold out'),
    ]
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name='data')
    code = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS, default='Available')
    order = models.ForeignKey('CustomerOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_uses')
    max_uses = models.PositiveIntegerField(default=1)
    uses_count = models.PositiveIntegerField(default=0)

    objects = InventoryDataQuerySet.as_manager()

    class Meta:
        db_table = 'inventory_data'

    def __str__(self):
        return self.code

    @property
    def uses_left(self):
        return max((self.max_uses or 1) - (self.uses_count or 0), 0)

    @property
    def is_reusable(self):
        return (self.max_uses or 1) > 1

    @property
    def is_available(self):
        """Mesma regra do queryset available(): status Available e usos restantes."""
        return self.status == 'Available' and (self.uses_count or 0) < (self.max_uses or 1)


class Media(models.Model):
    name = models.CharField(max_length=500)
    size = models.CharField(max_length=50, blank=True, null=True)
    width = models.CharField(max_length=20, blank=True, null=True)
    height = models.CharField(max_length=20, blank=True, null=True)
    author = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'media'

    def __str__(self):
        return self.name


class Page(models.Model):
    page_title = models.CharField(max_length=255)
    page_article = models.TextField(blank=True, null=True)
    page_visibility = models.CharField(max_length=20, default='Active')
    page_slug = models.SlugField(max_length=255, blank=True, null=True)
    page_thumbnail = models.CharField(max_length=500, blank=True, null=True)
    page_meta_description = models.CharField(max_length=500, blank=True, null=True)
    page_kw1 = models.CharField(max_length=255, blank=True, null=True)
    page_kw2 = models.CharField(max_length=255, blank=True, null=True)
    page_kw3 = models.CharField(max_length=255, blank=True, null=True)
    page_kw4 = models.CharField(max_length=255, blank=True, null=True)
    page_kw5 = models.CharField(max_length=255, blank=True, null=True)
    page_author = models.CharField(max_length=255, blank=True, null=True)
    page_views = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pages'

    def __str__(self):
        return self.page_title

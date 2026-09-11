import base64
import json
from decimal import Decimal

from django.test import TestCase, override_settings

from core import asaas
from core.models import (
    Currency, Customer, CustomerOrder, Invoice, OrderInput, PaymentDeposit,
    PaymentGateway, ServiceGroup, ServiceInput, ServiceList, Statement,
)


class AsaasGateway:
    asaas_api_key = 'test-token'
    asaas_sandbox = True
    charge = Decimal('0.50')


class FakeInvoice:
    id = 1
    invoice_amount = Decimal('100.00')
    invoice_title = 'Adicionar Saldo'
    invoice_for = 'Deposit'

    class Customer:
        name = 'Teste'
        email = 'teste@exemplo.com'
        mobile = '5599999999999'
        cpf_cnpj = '12345678901'

    customer = Customer()


class AsaasFlowTests(TestCase):
    def setUp(self):
        self.gateway = AsaasGateway()
        self.responses = []

    def fake_call(self, gw, method, path, body=None, timeout=40):
        self.responses.append((method, path, body))
        if path.startswith('/customers?'):
            return {'data': []}
        if path == '/customers' and method == 'POST':
            return {'id': 'cus_001'}
        if path == '/payments' and method == 'POST':
            return {'id': 'pay_001', 'invoiceUrl': 'https://asaas.com/i/pay_001',
                    'status': 'PENDING', 'billingType': 'PIX',
                    'customer': 'cus_001', 'value': 100.0}
        if path == '/payments/pay_001/pixQrCode':
            return {'encodedImage': 'base64-xyz==', 'payload': '0002012658...123',
                    'expirationDate': '2099-01-01 23:59:59'}
        if path == '/payments/pay_001':
            return {'id': 'pay_001', 'status': 'CONFIRMED', 'billingType': 'PIX'}
        raise AssertionError(f'Unexpected call {method} {path}')

    @override_settings(DEBUG=False)
    def test_create_pix_payment_gets_qrcode(self):
        original_call = asaas.call
        asaas.call = self.fake_call
        try:
            payment = asaas.create_pix_payment(FakeInvoice(), self.gateway)
        finally:
            asaas.call = original_call
        self.assertEqual(payment['id'], 'pay_001')
        self.assertEqual(payment['encodedImage'], 'base64-xyz==')
        self.assertEqual(payment['payload'], '0002012658...123')
        self.assertEqual(payment['invoiceUrl'], 'https://asaas.com/i/pay_001')
        methods = [r[0] for r in self.responses]
        self.assertIn('POST', methods)
        self.assertIn('GET', methods)

    @override_settings(DEBUG=False)
    def test_create_pix_requires_cpf_in_customer(self):
        original_call = asaas.call
        asaas.call = self.fake_call
        try:
            asaas.get_or_create_customer(self.gateway, FakeInvoice.customer)
        finally:
            asaas.call = original_call
        post_customer = [b for m, p, b in self.responses if p == '/customers']
        self.assertEqual(post_customer[0]['cpfCnpj'], '12345678901')


class EndToEndPaymentTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.gateway = PaymentGateway.objects.create(
            name='Asaas', status='Active', asaas_api_key='test-token', asaas_sandbox=True,
        )
        self.customer = Customer.objects.create(
            name='Cliente Teste', email='cliente@teste.com', mobile='11999999999',
            cpf_cnpj='12345678901', password=Customer.make_password('senha123'),
            currency='BRL',
        )
        self.invoice = Invoice.objects.create(
            customer=self.customer, customer_name=self.customer.name,
            invoice_for='Deposit', invoice_amount=Decimal('50.00'),
            customer_currency='BRL', invoice_status='Unpaid', invoice_title='Adicionar Saldo',
        )

    def test_mark_paid_credits_balance(self):
        deposit = PaymentDeposit.objects.create(
            name='Asaas - PIX', gateway_amount=self.invoice.invoice_amount,
            gateway_payment_id='pay_001', status='Pending', invoice=self.invoice,
        )
        from core.views import _mark_paid
        _mark_paid(deposit, {'payment': {'id': 'pay_001', 'status': 'CONFIRMED'}})
        self.customer.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('50.00'))
        self.assertEqual(self.invoice.invoice_status, 'Paid')
        self.assertEqual(deposit.status, 'Paid')


class PublicApiTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.customer = Customer.objects.create(
            name='Cliente API', email='api@teste.com', mobile='11999999999',
            password=Customer.make_password('senha123'), currency='BRL',
            balance=Decimal('100.00'), api_allow='on', api_key='APIKEY-TESTE-1234',
        )
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='Unlock Tool 1 Ano',
            original_price=Decimal('20.00'), status='Active', delivery_time='1-3 days',
            min_qnt='1', max_qnt='5',
        )
        ServiceInput.objects.create(service=self.service, name='Email')
        ServiceInput.objects.create(service=self.service, name='Username')
        self.url = '/public/api/index.php'

    def post(self, action, parameters='', username=None, key=None):
        return self.client.post(self.url, {
            'username': username or self.customer.email,
            'apiaccesskey': key or self.customer.api_key,
            'action': action,
            'requestformat': 'JSON',
            'parameters': parameters,
        })

    def test_invalid_auth_returns_error(self):
        resp = self.post('accountinfo', key='errada')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('ERROR', data)

    def test_accountinfo(self):
        data = json.loads(self.post('accountinfo').content)
        info = data['SUCCESS'][0]['AccountInfo']
        self.assertEqual(info['mail'], self.customer.email)
        self.assertEqual(info['currency'], 'BRL')
        self.assertEqual(float(info['creditraw']), 100.0)
        self.assertEqual(data['apiversion'], '1.0')

    def test_service_list(self):
        data = json.loads(self.post('imeiservicelist').content)
        services = data['SUCCESS'][0]['LIST']['Ferramentas']['SERVICES']
        svc = services[str(self.service.id)]
        self.assertEqual(svc['SERVICENAME'], 'Unlock Tool 1 Ano')
        self.assertEqual(float(svc['CREDIT']), 20.0)
        self.assertEqual(len(svc['Requires.Custom']), 2)

    def test_place_order_debits_balance(self):
        data = json.loads(self.post('placeimeiorder', '<ID>{}</ID><QNT>2</QNT>'.format(self.service.id)).content)
        ref = data['SUCCESS'][0]['REFERENCEID']
        order = CustomerOrder.objects.get(id=ref)
        self.assertEqual(order.service_status, 'In Process')
        self.assertEqual(order.service_price, Decimal('40.00'))
        self.assertEqual(order.service_qnt, '2')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('60.00'))
        self.assertEqual(Statement.objects.filter(customer=self.customer, type='Debit').count(), 1)
        self.assertEqual(OrderInput.objects.filter(order=order).count(), 2)

    def test_place_order_insufficient_balance(self):
        self.customer.balance = Decimal('10.00')
        self.customer.save(update_fields=['balance'])
        data = json.loads(self.post('placeimeiorder', '<ID>{}</ID><QNT>1</QNT>'.format(self.service.id)).content)
        self.assertIn('ERROR', data)
        self.assertEqual(CustomerOrder.objects.count(), 0)

    def test_get_order_status(self):
        order = CustomerOrder.objects.create(
            customer=self.customer, service_status='Success', service_type='server_service',
            service_price=Decimal('20.00'), service_title='Unlock Tool 1 Ano',
        )
        data = json.loads(self.post('getimeiorder', '<ID>{}</ID>'.format(order.id)).content)
        self.assertEqual(data['SUCCESS'][0]['STATUS'], 4)

    def test_place_bulk_order(self):
        params = {
            '7': {'ID': self.service.id, 'QNT': 1, 'CUSTOMFIELD': base64.b64encode(json.dumps({'Email': 'a@b.com'}).encode()).decode()},
            '8': {'ID': self.service.id, 'QNT': 1, 'CUSTOMFIELD': ''},
        }
        encoded = base64.b64encode(json.dumps(params).encode()).decode()
        data = json.loads(self.post('placebulkorder', encoded).content)
        self.assertEqual(data['7']['status'], 'success')
        self.assertEqual(data['8']['status'], 'success')
        self.assertEqual(CustomerOrder.objects.count(), 2)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('60.00'))

    def test_get_order_bulk(self):
        order = CustomerOrder.objects.create(
            customer=self.customer, service_status='In Process', service_type='server_service',
            service_price=Decimal('20.00'), service_title='Unlock Tool 1 Ano',
        )
        encoded = base64.b64encode(json.dumps({'7': {'ID': order.id}}).encode()).decode()
        data = json.loads(self.post('getimeiorderbulk', encoded).content)
        self.assertEqual(data['7']['SUCCESS'][0]['STATUS'], 1)

    def test_invalid_action(self):
        data = json.loads(self.post('acao-desconhecida').content)
        self.assertIn('ERROR', data)

    def test_customfields_stored(self):
        params = {
            '7': {'ID': self.service.id, 'QNT': 1, 'CUSTOMFIELD': base64.b64encode(json.dumps({'Email': 'user@email.com', 'Username': 'alo'}).encode()).decode()},
        }
        encoded = base64.b64encode(json.dumps(params).encode()).decode()
        data = json.loads(self.post('placebulkorder', encoded).content)
        order = CustomerOrder.objects.get(id=data['7']['referenceid'])
        inputs = {i.field_name: i.field_value for i in order.order_inputs.all()}
        self.assertEqual(inputs['Email'], 'user@email.com')
        self.assertEqual(inputs['Username'], 'alo')
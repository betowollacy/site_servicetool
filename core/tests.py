import json
from decimal import Decimal

from django.test import TestCase, override_settings

from core import asaas
from core.models import Currency, Customer, Invoice, PaymentGateway, PaymentDeposit


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
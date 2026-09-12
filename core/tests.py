import base64
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core import asaas, provider_api
from core.models import (
    Api, Currency, Customer, CustomerOrder, Inventory, InventoryData, Invoice, OrderInput,
    PaymentDeposit, PaymentGateway, RemoteServiceInput, RemoteServiceList, ServiceGroup,
    ServiceInput, ServiceList, Statement, SystemSetting, User,
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


class ProviderApiTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.api = Api.objects.create(
            api_name='Expert Server', api_type='gsm',
            api_url='https://expertserver.com.br/public',
            api_username='enterserver@hotmail.com', api_key='CHAVE-TESTE', status='Active',
        )
        self.customer = Customer.objects.create(
            name='Cliente', email='cliente@teste.com', password=Customer.make_password('senha123'),
            currency='BRL', balance=Decimal('100.00'), api_allow='on', api_key='APIKEY-TESTE',
        )
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.service = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group, title='IMEI Unlock',
            original_price=Decimal('20.00'), status='Active', api=self.api,
            referenceid='9001', min_qnt='1', max_qnt='5', slug='imei-unlock',
        )
        ServiceInput.objects.create(service=self.service, name='IMEI')
        ServiceInput.objects.create(service=self.service, name='LINK')
        self.url = '/public/api/index.php'

    def _order(self):
        return CustomerOrder.objects.create(
            customer=self.customer, service=self.service, service_status='In Process',
            service_type='imei_service', service_qnt='1', service_price=Decimal('20.00'),
            service_title=self.service.title, payment_methode='Api',
        )

    def _place(self, customfield=''):
        params = '<ID>{}</ID><QNT>1</QNT>'.format(self.service.id)
        if customfield:
            params += '<CUSTOMFIELD>{}</CUSTOMFIELD>'.format(customfield)
        return self.client.post(self.url, {
            'username': self.customer.email,
            'apiaccesskey': self.customer.api_key,
            'action': 'placeimeiorder',
            'parameters': params,
        })

    def test_endpoint_normalization(self):
        self.assertEqual(provider_api.endpoint_for(self.api),
                         'https://expertserver.com.br/public/api/index.php')
        self.api.api_url = 'https://x.com/public/api'
        self.assertEqual(provider_api.endpoint_for(self.api),
                         'https://x.com/public/api/index.php')
        self.api.api_url = 'https://x.com/public/api/index.php'
        self.assertEqual(provider_api.endpoint_for(self.api),
                         'https://x.com/public/api/index.php')

    def test_provider_for_order_requires_link(self):
        order = self._order()
        self.assertIsNotNone(provider_api.provider_for_order(order))
        self.service.referenceid = ''
        self.service.save(update_fields=['referenceid'])
        self.assertIsNone(provider_api.provider_for_order(order))

    def test_endpoint_for_empty(self):
        self.api.api_url = ''
        self.api.save(update_fields=['api_url'])
        order = self._order()
        self.assertIsNone(provider_api.provider_for_order(order))

    @patch('core.provider_api._request')
    def test_submit_local_order_places_at_provider(self, req):
        def fake(api, action, parameters=''):
            self.assertEqual(action, 'placeimeiorder')
            self.assertIn('9001', parameters)
            self.assertIn('CUSTOMFIELD', parameters)
            return {'SUCCESS': [{'MESSAGE': 'Order received', 'REFERENCEID': '5550001'}], 'apiversion': '1.0'}
        req.side_effect = fake
        order = self._order()
        OrderInput.objects.create(order=order, field_name='IMEI', field_value='351234567890123')
        ok, ref = provider_api.submit_local_order(order)
        self.assertTrue(ok)
        self.assertEqual(ref, '5550001')
        order.refresh_from_db()
        self.assertEqual(order.trx_id, '5550001')
        self.assertEqual(order.process_type, 'Auto')

    @patch('core.provider_api._request')
    def test_sync_local_order_fetches_code(self, req):
        def fake(api, action, parameters=''):
            self.assertEqual(action, 'getimeiorder')
            return {'SUCCESS': [{'STATUS': 4, 'CODE': 'RESULTADO-123'}], 'apiversion': '1.0'}
        req.side_effect = fake
        order = self._order()
        order.trx_id = '5550001'
        order.save(update_fields=['trx_id'])
        provider_api.sync_local_order(order)
        order.refresh_from_db()
        self.assertEqual(order.service_status, 'Success')
        self.assertEqual(order.service_comments, 'RESULTADO-123')

    @patch('core.provider_api._request')
    def test_sync_refunds_when_provider_rejects(self, req):
        def fake(api, action, parameters=''):
            return {'SUCCESS': [{'STATUS': 3, 'CODE': 'Rejected by provider'}], 'apiversion': '1.0'}
        req.side_effect = fake
        order = self._order()
        order.trx_id = '5550001'
        order.save(update_fields=['trx_id'])
        provider_api.sync_local_order(order)
        order.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(order.service_status, 'Rejected')
        self.assertEqual(order.service_comments, 'Rejected by provider')
        self.assertEqual(self.customer.balance, Decimal('120.00'))
        self.assertEqual(Statement.objects.filter(customer=self.customer, type='Credit').count(), 1)

    @patch('core.provider_api._request')
    def test_place_order_via_public_api_forwards(self, req):
        def fake(api, action, parameters=''):
            return {'SUCCESS': [{'MESSAGE': 'Order received', 'REFERENCEID': '777'}], 'apiversion': '1.0'}
        req.side_effect = fake
        resp = self._place()
        data = json.loads(resp.content)
        ref = data['SUCCESS'][0]['REFERENCEID']
        order = CustomerOrder.objects.get(id=ref)
        self.assertEqual(order.trx_id, '777')
        self.assertEqual(order.service, self.service)
        self.assertEqual(order.service_status, 'In Process')

    @patch('core.provider_api._request')
    def test_place_order_refunds_when_provider_rejects(self, req):
        req.side_effect = provider_api.ProviderError('Insufficient balance')
        resp = self._place()
        data = json.loads(resp.content)
        ref = data['SUCCESS'][0]['REFERENCEID']
        order = CustomerOrder.objects.get(id=ref)
        self.customer.refresh_from_db()
        self.assertEqual(order.service_status, 'Rejected')
        self.assertEqual(self.customer.balance, Decimal('100.00'))
        self.assertIn('Insufficient balance', order.service_comments)

    @patch('core.provider_api._request')
    def test_unlinked_service_does_not_call_provider(self, req):
        other = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group, title='Outro',
            original_price=Decimal('5.00'), status='Active', referenceid='', slug='outro',
        )
        resp = self.client.post(self.url, {
            'username': self.customer.email,
            'apiaccesskey': self.customer.api_key,
            'action': 'placeimeiorder',
            'parameters': '<ID>{}</ID><QNT>1</QNT>'.format(other.id),
        })
        data = json.loads(resp.content)
        order = CustomerOrder.objects.get(id=data['SUCCESS'][0]['REFERENCEID'])
        self.assertEqual(order.service_status, 'In Process')
        req.assert_not_called()


class ProviderApiAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='adminapi', password='senha123', is_staff=True)
        self.api = Api.objects.create(
            api_name='Expert Server', api_url='https://expertserver.com.br/public',
            api_username='enterserver@hotmail.com', api_key='KEY', status='Active',
        )
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.service = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group, title='IMEI Unlock',
            original_price=Decimal('20.00'), status='Active', slug='imei-unlock',
        )
        ServiceInput.objects.create(service=self.service, name='IMEI')

    def _login(self):
        self.client.force_login(self.staff)

    @patch('core.provider_api._request')
    def test_fetch_catalog_parses_list(self, req):
        def fake(api, action, parameters=''):
            return {'SUCCESS': [{'LIST': {
                'Ferramentas': {'GROUPNAME': 'Ferramentas', 'GROUPTYPE': 'IMEI', 'SERVICES': {
                    '7': {'SERVICEID': '7', 'SERVICETYPE': 'IMEI', 'SERVICENAME': 'Unlock 1',
                          'CREDIT': 10, 'CUSTOM': {'customname': 'IMEI'}},
                    '9': {'SERVICEID': '9', 'SERVICETYPE': 'IMEI', 'SERVICENAME': 'Unlock 2',
                          'CREDIT': 15, 'Requires.Custom': [{'fieldname': 'ID'}, {'fieldname': 'LINK'}]},
                }},
            }}], 'apiversion': '1.0'}
        req.side_effect = fake
        catalog = provider_api.fetch_catalog(self.api)
        self.assertEqual(len(catalog), 2)
        self.assertEqual(catalog[0]['referenceid'], '7')
        self.assertEqual(catalog[0]['fields'], ['IMEI'])
        self.assertEqual(catalog[1]['fields'], ['ID', 'LINK'])

    @patch('core.provider_api.fetch_catalog')
    def test_admin_import_creates_remote_services(self, fetch):
        fetch.return_value = [
            {'referenceid': '7', 'name': 'Unlock 1', 'servicetype': 'IMEI', 'credit': 10,
             'group': 'Ferramentas', 'time': '', 'fields': ['IMEI']},
        ]
        self._login()
        resp = self.client.post(reverse('admin_api_import', args=[self.api.id]))
        self.assertEqual(resp.status_code, 302)
        remote = RemoteServiceList.objects.get(api=self.api)
        self.assertEqual(remote.referenceid, '7')
        self.assertEqual(remote.SERVICENAME, 'Unlock 1')
        self.assertEqual(list(remote.service_fields.values_list('name', flat=True)), ['IMEI'])

    @patch('core.provider_api.fetch_catalog')
    def test_admin_import_does_not_rewrite_unchanged_remote(self, fetch):
        fetch.return_value = [
            {'referenceid': '7', 'name': 'Unlock 1', 'servicetype': 'IMEI', 'credit': 10,
             'group': 'Ferramentas', 'time': '', 'fields': ['IMEI']},
        ]
        self._login()
        self.client.post(reverse('admin_api_import', args=[self.api.id]))
        remote = RemoteServiceList.objects.get(api=self.api)
        remote_id = remote.id
        self.client.post(reverse('admin_api_import', args=[self.api.id]))
        remote.refresh_from_db()
        self.assertEqual(remote.id, remote_id)
        self.assertEqual(RemoteServiceList.objects.filter(api=self.api).count(), 1)

    def test_admin_api_list_filters_remote_services_by_q(self):
        RemoteServiceList.objects.create(api=self.api, referenceid='7', SERVICETYPE='IMEI',
                                         SERVICENAME='Unlock 1', CREDIT=Decimal('10.00'))
        RemoteServiceList.objects.create(api=self.api, referenceid='9', SERVICETYPE='IMEI',
                                         SERVICENAME='Consulta Samsung', CREDIT=Decimal('15.00'))
        self._login()
        resp = self.client.get(reverse('admin_api_list'), {'q': 'unlock'})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Unlock 1', html)
        self.assertNotIn('Consulta Samsung', html)

    def test_admin_link_binds_service(self):
        remote = RemoteServiceList.objects.create(
            api=self.api, referenceid='7', SERVICETYPE='IMEI', SERVICENAME='Unlock 1',
            CREDIT=Decimal('10.00'))
        RemoteServiceInput.objects.create(remote_service=remote, name='IMEI')
        self._login()
        resp = self.client.post(
            reverse('admin_api_link'),
            {'remote_id': remote.id, 'service_id': self.service.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        self.assertEqual(self.service.api_id, self.api.id)
        self.assertEqual(self.service.referenceid, '7')
        self.assertEqual(self.service.process_type, 'Auto')
        self.assertEqual(list(self.service.service_fields.values_list('name', flat=True)), ['IMEI'])

    def test_admin_link_unlinks(self):
        self.service.api = self.api
        self.service.referenceid = '7'
        self.service.save(update_fields=['api', 'referenceid'])
        remote = RemoteServiceList.objects.create(
            api=self.api, referenceid='7', SERVICETYPE='IMEI', SERVICENAME='Unlock 1',
            CREDIT=Decimal('10.00'))
        self._login()
        resp = self.client.post(reverse('admin_api_link'), {'remote_id': remote.id, 'service_id': ''})
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        self.assertIsNone(self.service.api_id)
        self.assertEqual(self.service.referenceid, '')

    def test_admin_api_list_requires_staff(self):
        resp = self.client.get(reverse('admin_api_list'))
        self.assertEqual(resp.status_code, 302)
        self._login()
        resp = self.client.get(reverse('admin_api_list'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_api_detail_shows_only_selected(self):
        other = Api.objects.create(
            api_name='Outro Provedor', api_url='https://x.com/public',
            api_username='outro@x.com', api_key='KEY2', status='Active')
        self._login()
        resp = self.client.get(reverse('admin_api_detail', args=[other.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Outro Provedor')
        self.assertContains(resp, 'KEY2')
        resp = self.client.get(reverse('admin_api_detail', args=[99999]))
        self.assertEqual(resp.status_code, 302)


class AdminRefundTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='adminrefund', password='senha123', is_staff=True)
        self.customer = Customer.objects.create(
            name='Cliente', email='cliente@teste.com', password=Customer.make_password('senha123'),
            currency='BRL', balance=Decimal('10.00'),
        )
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.service = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group, title='IMEI Unlock',
            original_price=Decimal('20.00'), status='Active', slug='imei-unlock',
        )
        self.client.force_login(self.staff)

    def _order(self, status='In Process'):
        return CustomerOrder.objects.create(
            customer=self.customer, service=self.service, service_status=status,
            service_type='imei_service', service_qnt='1', service_price=Decimal('20.00'),
            service_title=self.service.title, payment_methode='Balance',
        )

    def test_order_refund_returns_balance_and_rejects(self):
        order = self._order()
        resp = self.client.post(reverse('admin_order_refund', args=[order.id]))
        self.assertEqual(resp.status_code, 302)
        self.customer.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.service_status, 'Rejected')
        self.assertEqual(self.customer.balance, Decimal('30.00'))
        stmt = Statement.objects.get(customer=self.customer, type='Credit')
        self.assertEqual(stmt.amount, Decimal('20.00'))
        self.assertEqual(stmt.order, order)

    def test_order_refund_does_not_double_refund(self):
        order = self._order(status='Rejected')
        resp = self.client.post(reverse('admin_order_refund', args=[order.id]))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('10.00'))
        self.assertEqual(Statement.objects.filter(customer=self.customer, type='Credit').count(), 0)

    def test_customer_refund_free_amount(self):
        resp = self.client.post(reverse('admin_customer_refund', args=[self.customer.id]),
                                {'amount': '5,50', 'reason': 'Compra errada'})
        self.assertEqual(resp.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('15.50'))
        stmt = Statement.objects.get(customer=self.customer, type='Credit')
        self.assertEqual(stmt.amount, Decimal('5.50'))
        self.assertEqual(stmt.description, 'Compra errada')

    def test_customer_refund_requires_positive_amount(self):
        resp = self.client.post(reverse('admin_customer_refund', args=[self.customer.id]),
                                {'amount': '0', 'reason': ''})
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('10.00'))

    def test_order_delete_removes_order(self):
        order = self._order()
        resp = self.client.post(reverse('admin_order_delete', args=[order.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerOrder.objects.filter(id=order.id).count(), 0)
        self.assertEqual(OrderInput.objects.filter(order_id=order.id).count(), 0)

    def test_customer_delete_removes_customer_and_dependents(self):
        order = self._order()
        resp = self.client.post(reverse('admin_customer_delete', args=[self.customer.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Customer.objects.filter(id=self.customer.id).count(), 0)
        self.assertEqual(CustomerOrder.objects.filter(id=order.id).count(), 0)


class InventoryDeliveryTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='admininv', password='senha123', is_staff=True)
        self.customer = Customer.objects.create(
            name='Cliente', email='cliente@teste.com', password=Customer.make_password('senha123'),
            currency='BRL', balance=Decimal('50.00'),
        )
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.client.force_login(self.staff)

    def _order(self, status='In Process'):
        return CustomerOrder.objects.create(
            customer=self.customer, service=self.service, service_status=status,
            service_type='server_service', service_qnt='1', service_price=Decimal('20.00'),
            service_title=self.service.title, payment_methode='Balance',
        )

    def test_parse_credentials(self):
        from core.views_admin import _parse_credentials
        creds = _parse_credentials('user1;senha1\nuser2:senha2\nuser3|senha3\n  \nvazio')
        self.assertEqual(creds, [
            'Usuario: user1 | Senha: senha1',
            'Usuario: user2 | Senha: senha2',
            'Usuario: user3 | Senha: senha3',
            'vazio',
        ])

    def test_create_inventory_links_service_and_adds_bulk(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-6h',
        )
        resp = self.client.post(reverse('admin_inventory_new'), {
            'name': 'AMT Aluguel 6h',
            'service_id': str(self.service.id),
        })
        self.assertEqual(resp.status_code, 302)
        inv = Inventory.objects.get(name='AMT Aluguel 6h')
        self.service.refresh_from_db()
        self.assertEqual(self.service.inventory_id, inv.id)

        resp = self.client.post(reverse('admin_inventory_add', args=[inv.id]), {
            'codes': 'login1;senha1\nlogin2:senha2\nlogin1;senha1',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(InventoryData.objects.filter(inventory=inv).count(), 2)
        inv.refresh_from_db()
        self.assertEqual(inv.availableCount, 2)
        self.assertEqual(inv.available_code, 2)
        self.assertEqual(inv.soldOutCount, 0)

    def test_deliver_credential_sets_replied_in_and_success(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-6h',
        )
        inv = Inventory.objects.create(name='AMT')
        self.service.inventory = inv
        self.service.save(update_fields=['inventory'])
        item = InventoryData.objects.create(inventory=inv, code='Usuario: login1 | Senha: senha1', status='Available')
        order = self._order()

        resp = self.client.post(reverse('admin_order_deliver_credential', args=[order.id]))
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        item.refresh_from_db()
        inv.refresh_from_db()
        self.assertEqual(order.service_status, 'Success')
        self.assertEqual(order.replied_in, 'Usuario: login1 | Senha: senha1')
        self.assertEqual(item.status, 'Sold out')
        self.assertEqual(item.order_id, order.id)
        self.assertEqual(inv.availableCount, 0)
        self.assertEqual(inv.soldOutCount, 1)

    def test_deliver_no_stock_keeps_order_unchanged(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-6h',
        )
        inv = Inventory.objects.create(name='AMT')
        self.service.inventory = inv
        self.service.save(update_fields=['inventory'])
        order = self._order()

        resp = self.client.post(reverse('admin_order_deliver_credential', args=[order.id]))
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.service_status, 'In Process')
        self.assertIn(order.replied_in or '', ['', None])

    def test_deliver_requires_service_inventory(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='Sem estoque',
            original_price=Decimal('20.00'), status='Active', slug='sem-estoque',
        )
        order = self._order()
        resp = self.client.post(reverse('admin_order_deliver_credential', args=[order.id]))
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.service_status, 'In Process')

    def test_submit_local_order_delivers_from_inventory_when_api_off(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-auto', api_enabled=False,
        )
        inv = Inventory.objects.create(name='AMT Auto')
        self.service.inventory = inv
        self.service.save(update_fields=['inventory'])
        InventoryData.objects.create(inventory=inv, code='Usuario: auto | Senha: auto123', status='Available')

        order = self._order(status='In Process')
        ok, code = provider_api.submit_local_order(order)
        order.refresh_from_db()
        self.assertTrue(ok)
        self.assertIn('auto123', code)
        self.assertEqual(order.service_status, 'Success')
        self.assertIn('auto123', order.replied_in)
        item = InventoryData.objects.get(inventory=inv)
        self.assertEqual(item.status, 'Sold out')
        self.assertEqual(item.order_id, order.id)
        inv.refresh_from_db()
        self.assertEqual(inv.availableCount, 0)
        self.assertEqual(inv.soldOutCount, 1)

    def test_submit_local_order_with_api_off_keeps_waiting_when_no_stock(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-semstock', api_enabled=False,
        )
        inv = Inventory.objects.create(name='AMT Vazio')
        self.service.inventory = inv
        self.service.save(update_fields=['inventory'])

        order = self._order(status='In Process')
        ok, code = provider_api.submit_local_order(order)
        order.refresh_from_db()
        self.assertIsNone(ok)
        self.assertEqual(order.service_status, 'In Process')

    def test_cron_delivers_from_inventory_when_stock_added_later(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-cron', api_enabled=False,
        )
        inv = Inventory.objects.create(name='AMT Cron')
        self.service.inventory = inv
        self.service.save(update_fields=['inventory'])

        order = self._order(status='In Process')
        ok, _ = provider_api.submit_local_order(order)
        self.assertIsNone(ok)

        InventoryData.objects.create(inventory=inv, code='Usuario: cron | Senha: cron123', status='Available')
        from django.core.management import call_command
        call_command('check_provider_orders')

        order.refresh_from_db()
        self.assertEqual(order.service_status, 'Success')
        self.assertIn('cron123', order.replied_in)

    def test_quick_add_creates_service_inventory_and_saves_logins(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-quick',
        )
        self.assertIsNone(self.service.inventory)
        resp = self.client.post(reverse('admin_inventory_quick_add'), {
            'service_id': self.service.id,
            'codes': 'login1;senha1\nlogin2:senha2\nlogin1;senha1',
        })
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        inv = self.service.inventory
        self.assertIsNotNone(inv)
        self.assertEqual(InventoryData.objects.filter(inventory=inv).count(), 2)
        inv.refresh_from_db()
        self.assertEqual(inv.availableCount, 2)

    def test_quick_add_groups_login_and_password_on_separate_lines(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-duaslinhas',
        )
        resp = self.client.post(reverse('admin_inventory_quick_add'), {
            'service_id': self.service.id,
            'codes': 'tool@amg.com\nsenha123\ntool2@amg.com\noutrasenha',
        })
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        inv = self.service.inventory
        codes = list(InventoryData.objects.filter(inventory=inv).values_list('code', flat=True))
        self.assertEqual(len(codes), 2)
        self.assertIn('Usuario: tool@amg.com | Senha: senha123', codes)
        self.assertIn('Usuario: tool2@amg.com | Senha: outrasenha', codes)
        inv.refresh_from_db()
        self.assertEqual(inv.availableCount, 2)

    def test_quick_add_requires_service(self):
        resp = self.client.post(reverse('admin_inventory_quick_add'), {
            'service_id': '',
            'codes': 'login1;senha1',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Inventory.objects.count(), 0)

    def test_toggle_returns_credential_to_available(self):
        self.service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='AMT Aluguel 6h',
            original_price=Decimal('20.00'), status='Active', slug='amt-6h',
        )
        inv = Inventory.objects.create(name='AMT')
        self.service.inventory = inv
        self.service.save(update_fields=['inventory'])
        item = InventoryData.objects.create(inventory=inv, code='Usuario: login1 | Senha: senha1', status='Available')
        order = self._order()
        self.client.post(reverse('admin_order_deliver_credential', args=[order.id]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'Sold out')

        resp = self.client.post(reverse('admin_inventory_toggle', args=[item.id]))
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        inv.refresh_from_db()
        self.assertEqual(item.status, 'Available')
        self.assertIsNone(item.order_id)
        self.assertEqual(inv.availableCount, 1)
        self.assertEqual(inv.soldOutCount, 0)

    def test_edit_credential_updates_code(self):
        inv = Inventory.objects.create(name='AMT')
        item = InventoryData.objects.create(inventory=inv, code='Usuario: login1 | Senha: senha1', status='Available')
        resp = self.client.post(reverse('admin_inventory_edit', args=[item.id]), {
            'code': 'Usuario: login1 | Senha: novaSenha',
        })
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.code, 'Usuario: login1 | Senha: novaSenha')


class CreditServiceFormTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.customer = Customer.objects.create(
            name='Cliente Crédito', email='credito@teste.com', mobile='11999999999',
            password=Customer.make_password('senha123'), currency='BRL',
            balance=Decimal('100.00'),
        )
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.service = ServiceList.objects.create(
            service_type='Credit Service', service_group=self.group,
            title='Phoenix Tool Créditos', original_price=Decimal('10.00'),
            status='Active', slug='phoenix-creditos',
        )

    def _login(self):
        session = self.client.session
        session['customer_id'] = self.customer.id
        session.save()

    def test_server_view_offers_quantity_user_and_email_for_credit(self):
        self._login()
        resp = self.client.get(reverse('service_view', args=[self.service.slug]))
        html = resp.content.decode()
        self.assertIn('name="Quantidade de Créditos"', html)
        self.assertIn('name="Usuário"', html)
        self.assertIn('name="E-mail da Ferramenta"', html)
        self.assertNotIn('name="Senha"', html)

    def test_imei_service_does_not_receive_credit_fields(self):
        self._login()
        imei = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group,
            title='Unlock', original_price=Decimal('5.00'), status='Active', slug='unlock',
        )
        ServiceInput.objects.create(service=imei, name='IMEI')
        resp = self.client.get(reverse('service_view', args=[imei.slug]))
        html = resp.content.decode()
        self.assertIn('name="IMEI"', html)
        self.assertIn('IMEI ou Serial', html)
        self.assertIn('Descreva o serviço', html)
        self.assertIn('tela de hello, tela passcode', html)
        self.assertNotIn('Quantidade de Créditos', html)

    def test_imei_service_without_fields_still_requires_imei_or_serial(self):
        self._login()
        imei = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group,
            title='Sem Campos', original_price=Decimal('4.00'), status='Active', slug='sem-campos',
        )
        resp = self.client.get(reverse('service_view', args=[imei.slug]))
        html = resp.content.decode()
        self.assertIn('name="IMEI"', html)
        self.assertIn('IMEI ou Serial', html)
        self.assertIn('Descreva o serviço', html)
        self.assertIn('tela de hello, tela passcode', html)

    def test_submit_order_requires_user_and_email(self):
        self._login()
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'Quantidade de Créditos': '5',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerOrder.objects.count(), 0)

    def test_submit_order_stores_user_and_email(self):
        self._login()
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'Quantidade de Créditos': '5',
            'Usuário': 'ferramenta.login',
            'E-mail da Ferramenta': 'ferramenta@email.com',
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        self.assertEqual(order.service_qnt, '5')
        inputs = {i.field_name: i.field_value for i in order.order_inputs.all()}
        self.assertEqual(inputs.get('Usuário'), 'ferramenta.login')
        self.assertEqual(inputs.get('E-mail da Ferramenta'), 'ferramenta@email.com')
        self.assertNotIn('Quantidade de Créditos', inputs)
        self.assertNotIn('Senha', inputs)


class ActivationServiceTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.customer = Customer.objects.create(
            name='Cliente Ativação', email='ativacao@teste.com', mobile='11999999999',
            password=Customer.make_password('senha123'), currency='BRL',
            balance=Decimal('100.00'),
        )
        self.group = ServiceGroup.objects.create(name='Ativação', slug='activation', status='Active')
        self.service = ServiceList.objects.create(
            service_type='Activation Service', service_group=self.group,
            title='Ativação Phoenix', original_price=Decimal('12.00'),
            status='Active', slug='ativacao-phoenix',
        )

    def _login(self):
        session = self.client.session
        session['customer_id'] = self.customer.id
        session.save()

    def test_admin_service_list_has_activation_pill(self):
        staff = User.objects.create_user(username='adminativ', password='senha123', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse('admin_service_list', args=['activation']))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('nav-link active" href="/admin-panel/services/activation/"', html)
        self.assertIn('Ativação', html)
        self.assertIn(self.service.title, html)

    def test_server_view_offers_user_email_and_registration_warning_for_activation(self):
        self._login()
        resp = self.client.get(reverse('service_view', args=[self.service.slug]))
        html = resp.content.decode()
        self.assertIn('name="Usuário"', html)
        self.assertIn('E-mail da Ferramenta', html)
        self.assertNotIn('name="Senha"', html)
        self.assertNotIn('Quantidade de Créditos', html)
        self.assertIn('o cliente precisa estar cadastrado na ferramenta', html)

    def test_activation_category_page_exists(self):
        resp = self.client.get(reverse('category', args=['activation-service']))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Ativação', html)
        self.assertIn(self.service.title, html)

    def test_submit_order_requires_user_and_email_for_activation(self):
        self._login()
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerOrder.objects.count(), 0)

    def test_submit_activation_order_stores_user_and_email(self):
        self._login()
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'Usuário': 'usuario.ferramenta',
            'E-mail da Ferramenta': 'conta@ferramenta.com',
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        self.assertEqual(order.service_type, 'activation_service')
        inputs = {i.field_name: i.field_value for i in order.order_inputs.all()}
        self.assertEqual(inputs.get('Usuário'), 'usuario.ferramenta')
        self.assertEqual(inputs.get('E-mail da Ferramenta'), 'conta@ferramenta.com')
        self.assertNotIn('Senha', inputs)


class ImeiOrderFlowTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.customer = Customer.objects.create(
            name='Cliente IMEI', email='imei@teste.com', mobile='11999999999',
            password=Customer.make_password('senha123'), currency='BRL',
            balance=Decimal('100.00'),
        )
        self.group = ServiceGroup.objects.create(name='Serviço de IMEI e Consultas', slug='imei', status='Active')
        self.service = ServiceList.objects.create(
            service_type='IMEI Service', service_group=self.group,
            title='Consulta IMEI', original_price=Decimal('5.00'),
            status='Active', slug='consulta-imei',
        )
        self.staff = User.objects.create_user(username='adminflow', password='senha123', is_staff=True)

    def _customer_login(self):
        session = self.client.session
        session['customer_id'] = self.customer.id
        session.save()

    def test_imei_order_lands_in_admin_with_inputs(self):
        self._customer_login()
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'IMEI': '356938035643809',
            'Descreva o serviço': 'tela de hello',
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        self.assertEqual(order.service_type, 'imei_service')
        self.assertEqual(order.service_status, 'Waiting Action')
        inputs = {i.field_name: i.field_value for i in order.order_inputs.all()}
        self.assertEqual(inputs.get('IMEI'), '356938035643809')
        self.assertEqual(inputs.get('Descreva o serviço'), 'tela de hello')

        self.client.logout()
        self.client.force_login(self.staff)
        page = self.client.get(reverse('admin_orders', args=['waiting']))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('356938035643809', html)
        self.assertIn('tela de hello', html)
        self.assertIn('Descrição do Serviço', html)

    def test_submit_order_pokes_telegram(self):
        SystemSetting.objects.create(key='tgBotToken', value='123:abc')
        SystemSetting.objects.create(key='tgChatId', value='-100')
        self._customer_login()
        with patch('core.notify.send_telegram') as send:
            resp = self.client.post(reverse('submit_order'), {
                'serviceID': self.service.id,
                'IMEI': '356938035643809',
                'Descreva o serviço': 'tela de hello',
            })
        self.assertEqual(resp.status_code, 302)
        send.assert_called_once()
        text = send.call_args.args[0]
        self.assertIn('NOVO PEDIDO', text)
        self.assertIn('Consulta IMEI', text)
        self.assertIn('tela de hello', text)
        self.assertIn('Pago via saldo', text)
        order = CustomerOrder.objects.latest('id')
        self.assertEqual(order.seen, 'false')

    def test_admin_pending_bell_and_seen(self):
        self._customer_login()
        self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'IMEI': '356938035643809',
            'Descreva o serviço': 'tela de hello',
        })
        self.client.logout()
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('admin_orders_unseen'))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['orders'][0]['customer'], 'Cliente IMEI')
        self.assertEqual(data['orders'][0]['service'], 'Consulta IMEI')
        page = self.client.get(reverse('admin_orders', args=['waiting']))
        self.assertEqual(page.status_code, 200)
        resp2 = self.client.get(reverse('admin_orders_unseen'))
        self.assertEqual(json.loads(resp2.content)['count'], 0)

    def test_unpaid_order_notifies_only_after_payment(self):
        SystemSetting.objects.create(key='tgBotToken', value='123:abc')
        SystemSetting.objects.create(key='tgChatId', value='-100')
        self.customer.balance = Decimal('2.00')
        self.customer.save(update_fields=['balance'])
        self._customer_login()
        with patch('core.notify.send_telegram') as send:
            resp = self.client.post(reverse('submit_order'), {
                'serviceID': self.service.id,
                'IMEI': '356938035643809',
                'Descreva o serviço': 'tela de hello',
            })
        self.assertEqual(resp.status_code, 302)
        send.assert_not_called()
        order = CustomerOrder.objects.latest('id')
        self.assertEqual(order.service_status, 'Waiting Action')
        invoice = Invoice.objects.get(order=order)
        deposit = PaymentDeposit.objects.create(
            name='Asaas - PIX', gateway_amount=invoice.invoice_amount,
            gateway_payment_id='pay_002', status='Pending', invoice=invoice,
        )
        from core.views import _mark_paid
        with patch('core.notify.send_telegram') as send:
            _mark_paid(deposit, {'payment': {'id': 'pay_002', 'status': 'CONFIRMED'}})
        send.assert_called_once()
        text = send.call_args.args[0]
        self.assertIn('NOVO PEDIDO', text)
        self.assertIn('Pago via saldo', text)


class MethodServiceTests(TestCase):
    def setUp(self):
        Currency.objects.create(code='BRL', name='Brazilian Real', icon='R$', rate=Decimal('1.0000'), status='Active')
        self.customer = Customer.objects.create(
            name='Cliente Métodos', email='metodo@teste.com', mobile='11999999999',
            password=Customer.make_password('senha123'), currency='BRL',
            balance=Decimal('100.00'),
        )
        self.group = ServiceGroup.objects.create(name='Métodos', slug='method', status='Active')
        self.service = ServiceList.objects.create(
            service_type='Method Service', service_group=self.group,
            title='Firmware Unlock', original_price=Decimal('15.00'),
            status='Active', slug='firmware-unlock',
        )

    def _login(self):
        session = self.client.session
        session['customer_id'] = self.customer.id
        session.save()

    def test_admin_service_list_has_methods_pill(self):
        staff = User.objects.create_user(username='adminmetodo', password='senha123', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse('admin_service_list', args=['method']))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('nav-link active" href="/admin-panel/services/method/"', html)
        self.assertIn('Métodos', html)
        self.assertIn(self.service.title, html)

    def test_move_select_renders_on_all_category_lists(self):
        staff = User.objects.create_user(username='adminmoveall', password='senha123', is_staff=True)
        self.client.force_login(staff)
        for idx, (svtype, stype) in enumerate([
            ('server', 'Server Service'), ('credit', 'Credit Service'),
            ('activation', 'Activation Service'),
            ('imei', 'IMEI Service'), ('method', 'Method Service'),
        ]):
            ServiceList.objects.create(
                service_type=stype, service_group=self.group,
                title='Serviço {}'.format(svtype), original_price=Decimal('5.00'),
                status='Active', slug='serv-{}-{}'.format(svtype, idx),
            )
            resp = self.client.get(reverse('admin_service_list', args=[svtype]))
            self.assertEqual(resp.status_code, 200)
            html = resp.content.decode()
            self.assertIn('/move/', html)
            self.assertIn('target_svtype', html)
            self.assertIn('Mover para categoria', html)
            self.assertIn('<option value=', html)

    def test_server_view_offers_free_instructions_field(self):
        self._login()
        resp = self.client.get(reverse('service_view', args=[self.service.slug]))
        html = resp.content.decode()
        self.assertIn('name="Instruções"', html)
        self.assertIn('Escreva sua solicitação', html)
        self.assertIn('envie o link dessa firmware', html)

    def test_submit_order_stores_instructions_and_keeps_manual_delivery(self):
        self._login()
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'Instruções': 'Envie o link dessa firmware',
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        self.assertEqual(order.service_type, 'method_service')
        inputs = {i.field_name: i.field_value for i in order.order_inputs.all()}
        self.assertEqual(inputs.get('Instruções'), 'Envie o link dessa firmware')
        self.assertEqual(order.service_input1, 'Envie o link dessa firmware')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('85.00'))

    def test_admin_moves_service_between_categories(self):
        staff = User.objects.create_user(username='adminmove', password='senha123', is_staff=True)
        self.client.force_login(staff)
        self.service.service_type = 'Server Service'
        self.service.save(update_fields=['service_type'])
        resp = self.client.post(reverse('admin_service_move', args=['server', self.service.id]), {
            'target_svtype': 'method',
        })
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        self.assertEqual(self.service.service_type, 'Method Service')
        self.assertEqual(self.service.service_group.slug, 'method')

    def test_admin_move_rejects_invalid_target(self):
        staff = User.objects.create_user(username='adminmove2', password='senha123', is_staff=True)
        self.client.force_login(staff)
        self.service.service_type = 'Server Service'
        self.service.save(update_fields=['service_type'])
        resp = self.client.post(reverse('admin_service_move', args=['server', self.service.id]), {
            'target_svtype': 'inexistente',
        })
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        self.assertEqual(self.service.service_type, 'Server Service')
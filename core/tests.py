import base64
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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

    def test_checkout_shows_pix_symbol_for_asaas(self):
        session = self.client.session
        session['customer_id'] = self.customer.id
        session.save()
        resp = self.client.get(reverse('checkout', args=[self.invoice.id]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('resource/pix.svg', html)
        self.assertNotIn('asaas.svg', html)


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
            self.assertIn('351234567890123', parameters)
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
    def test_submit_blocks_recent_duplicate(self, req):
        def fake(api, action, parameters=''):
            return {'SUCCESS': [{'MESSAGE': 'Order received', 'REFERENCEID': '100001'}], 'apiversion': '1.0'}
        req.side_effect = fake
        first = self._order()
        OrderInput.objects.create(order=first, field_name='IMEI', field_value='351234567890123')
        ok, ref = provider_api.submit_local_order(first)
        self.assertTrue(ok)
        self.assertEqual(req.call_count, 1)
        duplicate = self._order()
        ok, msg = provider_api.submit_local_order(duplicate)
        self.assertFalse(ok)
        self.assertIn('duplicada', msg.lower())
        self.assertEqual(req.call_count, 1)

    @patch('core.provider_api._request')
    def test_submit_permits_after_duplicate_window(self, req):
        def fake(api, action, parameters=''):
            return {'SUCCESS': [{'MESSAGE': 'Order received', 'REFERENCEID': '100002'}], 'apiversion': '1.0'}
        req.side_effect = fake
        first = self._order()
        OrderInput.objects.create(order=first, field_name='IMEI', field_value='351234567890123')
        provider_api.submit_local_order(first)
        CustomerOrder.objects.filter(id=first.id).update(
            created_at=timezone.now() - provider_api.DUPLICATE_WINDOW - timedelta(seconds=1))
        second = self._order()
        OrderInput.objects.create(order=second, field_name='IMEI', field_value='351234567890123')
        ok, ref = provider_api.submit_local_order(second)
        self.assertTrue(ok)
        self.assertEqual(req.call_count, 2)

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

    def _rit_api(self):
        return Api.objects.create(
            api_name='Rit Unlocker', api_type='ritunlocker',
            api_url='https://ritunlocker.com/api',
            api_username='', api_key='CHAVE-RIT-123', status='Active',
        )

    def test_ritunlocker_endpoint_normalization(self):
        api = self._rit_api()
        self.assertEqual(provider_api.endpoint_for(api, 'accountinfo'),
                         'https://ritunlocker.com/api/accountinfo')
        self.assertEqual(provider_api.endpoint_for(api, 'imeistatus'),
                         'https://ritunlocker.com/api/imeistatus')
        self.assertEqual(provider_api.endpoint_for(api, 'placeimeiorder'),
                         'https://ritunlocker.com/api/placeimeiorder')
        api.api_url = 'https://ritunlocker.com/api/index.php'
        api.save(update_fields=['api_url'])
        self.assertEqual(provider_api.endpoint_for(api, 'accountinfo'),
                         'https://ritunlocker.com/api/accountinfo')

    @patch('core.provider_api.urllib.request.urlopen')
    def test_ritunlocker_request_uses_key(self, urlopen):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def read(self):
                return b'{"SUCCESS": [{"AccountInfo": {"credit": "100.00", "currency": "USD"}}]}'
        urlopen.return_value = _Resp()

        info = provider_api.account_info(self._rit_api())
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, 'https://ritunlocker.com/api/accountinfo')
        self.assertIn(b'key=CHAVE-RIT-123', req.data)
        self.assertNotIn(b'apiaccesskey', req.data)
        self.assertNotIn(b'username', req.data)
        self.assertEqual(info['credit'], '100.00')

    @patch('core.provider_api._request')
    def test_ritunlocker_submit_sends_imei_instead_of_customfield(self, req):
        def fake(api, action, parameters=''):
            self.assertEqual(action, 'placeimeiorder')
            self.assertNotIn('CUSTOMFIELD', parameters)
            parsed = json.loads(parameters)
            self.assertEqual(parsed['ID'], '9001')
            self.assertEqual(parsed['IMEI'], '351234567890123')
            return {'SUCCESS': [{'MESSAGE': 'Order placed successfully', 'ORDERID': '8888'}]}
        req.side_effect = fake
        service = ServiceList.objects.get(id=self.service.id)
        service.api = self._rit_api()
        service.save(update_fields=['api'])
        self.service = service
        order = self._order()
        OrderInput.objects.create(order=order, field_name='IMEI', field_value='351234567890123')
        ok, ref = provider_api.submit_local_order(order)
        self.assertTrue(ok)
        self.assertEqual(ref, '8888')
        order.refresh_from_db()
        self.assertEqual(order.trx_id, '8888')

    @patch('core.provider_api._request')
    def test_ritunlocker_submit_sends_master_pin_when_configured(self, req):
        def fake(api, action, parameters=''):
            self.assertEqual(action, 'placeimeiorder')
            parsed = json.loads(parameters)
            self.assertEqual(parsed['PIN'], '123987')
            return {'SUCCESS': [{'MESSAGE': 'Order placed successfully', 'ORDERID': '999000'}]}
        req.side_effect = fake
        service = ServiceList.objects.get(id=self.service.id)
        api = self._rit_api()
        api.api_pin = '123987'
        api.save(update_fields=['api_pin'])
        service.api = api
        service.save(update_fields=['api'])
        self.service = service
        order = self._order()
        ok, ref = provider_api.submit_local_order(order)
        self.assertTrue(ok)
        self.assertEqual(ref, '999000')

    @patch('core.provider_api._request')
    def test_ritunlocker_submit_sends_customer_email_when_no_input(self, req):
        def fake(api, action, parameters=''):
            self.assertEqual(action, 'placeimeiorder')
            parsed = json.loads(parameters)
            self.assertEqual(parsed['IMEI'], self.customer.email)
            return {'SUCCESS': [{'MESSAGE': 'Order placed successfully', 'ORDERID': '999111'}]}
        req.side_effect = fake
        service = ServiceList.objects.get(id=self.service.id)
        service.api = self._rit_api()
        service.save(update_fields=['api'])
        self.service = service
        order = self._order()
        ok, ref = provider_api.submit_local_order(order)
        self.assertTrue(ok)
        self.assertEqual(ref, '999111')

    @patch('core.provider_api._request')
    def test_ritunlocker_sync_uses_imeistatus(self, req):
        def fake(api, action, parameters=''):
            self.assertEqual(action, 'imeistatus')
            return {'SUCCESS': [{'STATUS': '4', 'CODE': 'complete', 'ORDERID': '5550001'}]}
        req.side_effect = fake
        service = ServiceList.objects.get(id=self.service.id)
        service.api = self._rit_api()
        service.save(update_fields=['api'])
        self.service = service
        order = self._order()
        order.trx_id = '5550001'
        order.save(update_fields=['trx_id'])
        provider_api.sync_local_order(order)
        order.refresh_from_db()
        self.assertEqual(order.service_status, 'Success')
        self.assertEqual(order.service_comments, 'complete')

    @patch('core.provider_api._request')
    def test_ritunlocker_fetch_catalog_parses_list(self, req):
        def fake(api, action, parameters=''):
            return {'SUCCESS': [{'LIST': [
                {'GROUPNAME': 'iPhone Services', 'SERVICES': [
                    {'SERVICEID': '123', 'SERVICENAME': 'iPhone X Unlock',
                     'CREDIT': 5.0, 'SERVICETYPE': 'IMEI', 'TIME': '10 Minutes',
                     'CUSTOM': {'bulk': '0', 'allow': '1', 'customname': 'IMEI'}},
                ]},
            ]}]}
        req.side_effect = fake
        catalog = provider_api.fetch_catalog(self._rit_api())
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]['referenceid'], '123')
        self.assertEqual(catalog[0]['name'], 'iPhone X Unlock')
        self.assertEqual(catalog[0]['fields'], ['IMEI'])

    def test_detect_duration_reconhece_6_hurs(self):
        self.assertEqual(provider_api._detect_duration('UNLOCK TOOL RENT (6-Hurs)-API -india'), '6h')
        self.assertEqual(provider_api._detect_duration('ALUGUEL FERRAMENTA 6 HORAS'), '6h')
        self.assertEqual(provider_api._detect_duration('Tool Rent 6hrs'), '6h')
        self.assertEqual(provider_api._detect_duration('Unlock Rent 12h'), '12h')

    def test_auto_link_nao_casa_6h_com_12h(self):
        api = Api.objects.create(
            api_name='Rit Unlocker', api_type='ritunlocker',
            api_url='https://ritunlocker.com/api', api_username='', api_key='CHAVE-RIT-123',
            status='Active', api_pin='', price_rate=Decimal('0'), price_markup=Decimal('0'),
        )
        RemoteServiceList.objects.create(
            api=api, referenceid='9001', SERVICETYPE='SERVER',
            SERVICENAME='UNLOCK TOOL RENT (12-Hurs)-API', CREDIT=Decimal('0.90'),
        )
        local = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group,
            title='UNLOCK TOOL RENT (6-Hurs)-API -india',
            original_price=Decimal('5.00'), status='Active', slug='rent-6h',
        )
        remote, score = provider_api.auto_link_service(local)
        self.assertIsNone(remote)

    def test_detect_kind_licencas_sao_ativacao(self):
        self.assertEqual(provider_api._detect_kind('Chimera Tool Basic Licence 1 year'), 'activation')
        self.assertEqual(provider_api._detect_kind('Cheetah Pro License Ativação'), 'activation')
        self.assertEqual(provider_api._detect_kind('HW-Key Credits'), 'credits')
        self.assertEqual(provider_api._detect_kind('Tool Rent 6h'), 'rent')
        self.assertEqual(provider_api._detect_kind('Chimera Tool Premium Renovação 1 Ano'), 'renew')

    def test_auto_link_creditos_nao_casa_com_ativacao(self):
        api = Api.objects.create(
            api_name='Rit Unlocker', api_type='ritunlocker',
            api_url='https://ritunlocker.com/api', api_username='', api_key='CHAVE-RIT-123',
            status='Active', api_pin='', price_rate=Decimal('0'), price_markup=Decimal('0'),
        )
        RemoteServiceList.objects.create(
            api=api, referenceid='1', SERVICETYPE='SERVER',
            SERVICENAME='Chimera Tool Basic Licence 1 year (100 Phone Connection)',
            CREDIT=Decimal('5.00'),
        )
        local = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group,
            title='Chimera Tool - Créditos Para Serviço',
            original_price=Decimal('10.00'), status='Active', slug='chimera-creditos',
        )
        self.assertEqual(provider_api._detect_kind(local.title), 'credits')
        remote, score = provider_api.auto_link_service(local)
        self.assertIsNone(remote)

    def test_suggested_price_applies_rate_and_markup(self):
        api = self._rit_api()
        api.price_rate = Decimal('5.35')
        api.price_markup = Decimal('30')
        self.assertEqual(provider_api.suggested_price(api, Decimal('2.00')), Decimal('13.91'))
        api.price_rate = Decimal('0')
        api.price_markup = Decimal('0')
        self.assertEqual(provider_api.suggested_price(api, Decimal('5.00')), Decimal('5.00'))
        self.assertEqual(provider_api.suggested_price(None, Decimal('4.00')), Decimal('4.00'))


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

    def test_admin_api_delete_unlinks_services_and_removes_remote(self):
        self.service.api = self.api
        self.service.referenceid = '7'
        self.service.save(update_fields=['api', 'referenceid'])
        RemoteServiceList.objects.create(
            api=self.api, referenceid='7', SERVICETYPE='IMEI', SERVICENAME='Unlock 1',
            CREDIT=Decimal('10.00'))
        self._login()
        resp = self.client.post(reverse('admin_api_delete', args=[self.api.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Api.objects.filter(id=self.api.id).exists())
        self.assertFalse(RemoteServiceList.objects.filter(api_id=self.api.id).exists())
        self.service.refresh_from_db()
        self.assertIsNone(self.service.api_id)
        self.assertEqual(self.service.referenceid, '')
        self.assertEqual(self.service.process_type, 'Manual')

    def test_admin_api_delete_requires_post(self):
        self._login()
        resp = self.client.get(reverse('admin_api_delete', args=[self.api.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Api.objects.filter(id=self.api.id).exists())

    def test_admin_api_update_saves_price_fields(self):
        self._login()
        self.client.post(reverse('admin_api_update', args=[self.api.id]), {
            'api_name': self.api.api_name,
            'status': 'Active',
            'price_rate': '5.35',
            'price_markup': '30',
        })
        self.api.refresh_from_db()
        self.assertEqual(self.api.price_rate, Decimal('5.3500'))
        self.assertEqual(self.api.price_markup, Decimal('30.00'))

    def test_admin_link_sets_auto_price(self):
        remote = RemoteServiceList.objects.create(
            api=self.api, referenceid='7', SERVICETYPE='IMEI', SERVICENAME='Unlock 1',
            CREDIT=Decimal('10.00'))
        RemoteServiceInput.objects.create(remote_service=remote, name='IMEI')
        self.api.price_rate = Decimal('5.00')
        self.api.price_markup = Decimal('10')
        self.api.save(update_fields=['price_rate', 'price_markup'])
        self._login()
        self.client.post(reverse('admin_api_link'), {
            'remote_id': remote.id, 'service_id': self.service.id, 'auto_price': '1',
        })
        self.service.refresh_from_db()
        self.assertEqual(self.service.original_price, Decimal('55.00'))
        self.assertEqual(self.service.api_id, self.api.id)
        self.assertEqual(self.service.referenceid, '7')


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

    def test_service_view_hides_user_email_when_collect_login_off(self):
        self._login()
        self.service.collect_login = False
        self.service.save(update_fields=['collect_login'])
        resp = self.client.get(reverse('service_view', args=[self.service.slug]))
        html = resp.content.decode()
        self.assertNotIn('name="Usuário"', html)
        self.assertNotIn('E-mail da Ferramenta', html)
        self.assertNotIn('o cliente precisa estar cadastrado na ferramenta', html)

    def test_service_view_offers_only_username_when_collect_fields_user(self):
        self._login()
        self.service.collect_fields = 'user'
        self.service.save(update_fields=['collect_fields'])
        resp = self.client.get(reverse('service_view', args=[self.service.slug]))
        html = resp.content.decode()
        self.assertIn('name="Usuário"', html)
        self.assertNotIn('E-mail da Ferramenta', html)

    def test_submit_order_requires_only_username_when_collect_fields_user(self):
        self._login()
        self.service.collect_fields = 'user'
        self.service.save(update_fields=['collect_fields'])
        resp = self.client.post(reverse('submit_order'), {'serviceID': self.service.id})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerOrder.objects.count(), 0)
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'Usuário': 'usuario.ferramenta',
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        inputs = {i.field_name for i in order.order_inputs.all()}
        self.assertIn('Usuário', inputs)
        self.assertNotIn('E-mail da Ferramenta', inputs)

    def test_submit_order_requires_email_only_when_collect_fields_email(self):
        self._login()
        self.service.collect_fields = 'email'
        self.service.save(update_fields=['collect_fields'])
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
            'E-mail da Ferramenta': 'conta@ferramenta.com',
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        inputs = {i.field_name for i in order.order_inputs.all()}
        self.assertIn('E-mail da Ferramenta', inputs)
        self.assertNotIn('Usuário', inputs)

    def test_admin_set_fields(self):
        staff = User.objects.create_user(username='adminfields', password='senha123', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.post(reverse('admin_service_set_fields', args=['activation', self.service.id]),
                                {'collect_fields': 'user'})
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        self.assertEqual(self.service.collect_fields, 'user')

    def test_submit_order_without_login_when_collect_login_off(self):
        self._login()
        self.service.collect_login = False
        self.service.save(update_fields=['collect_login'])
        resp = self.client.post(reverse('submit_order'), {
            'serviceID': self.service.id,
        })
        self.assertEqual(resp.status_code, 302)
        order = CustomerOrder.objects.latest('id')
        inputs = {i.field_name for i in order.order_inputs.all()}
        self.assertNotIn('Usuário', inputs)
        self.assertNotIn('E-mail da Ferramenta', inputs)

    def test_admin_toggle_login(self):
        staff = User.objects.create_user(username='adminlogin', password='senha123', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.post(reverse('admin_service_toggle_login', args=['activation', self.service.id]))
        self.assertEqual(resp.status_code, 302)
        self.service.refresh_from_db()
        self.assertFalse(self.service.collect_login)
        resp = self.client.post(reverse('admin_service_toggle_login', args=['activation', self.service.id]))
        self.service.refresh_from_db()
        self.assertTrue(self.service.collect_login)

    def test_service_form_saves_collect_login_off(self):
        staff = User.objects.create_user(username='adminform', password='senha123', is_staff=True)
        self.client.force_login(staff)
        resp = self.client.post(reverse('admin_service_new', args=['activation']), {
            'title': 'Ativação Sem Login',
            'original_price': '10.00',
            'fake': '1',
        })
        self.assertEqual(resp.status_code, 302)
        service = ServiceList.objects.get(title='Ativação Sem Login')
        self.assertFalse(service.collect_login)


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

class AdminPromoteTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='owner', password='senha123', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)

    def _edit(self, customer_id, extra=None):
        data = {'name': 'Novo Admin', 'email': 'novoadmin@teste.com'}
        if extra:
            data.update(extra)
        return self.client.post(reverse('admin_customer_edit', args=[customer_id]), data)

    def test_promote_creates_staff_user_with_customer_password(self):
        customer = Customer.objects.create(
            name='Novo Admin', email='novoadmin@teste.com',
            password=Customer.make_password('minhasenha'), currency='BRL',
        )
        resp = self._edit(customer.id, {'promote_to_admin': '1'})
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username=customer.email)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password('minhasenha'))

    def test_promote_existing_staff_account_is_kept(self):
        customer = Customer.objects.create(
            name='Ja Admin', email='jaadmin@teste.com',
            password=Customer.make_password('minhasenha'), currency='BRL',
        )
        user = User.objects.create_user(username='jaadmin@teste.com', email='jaadmin@teste.com', password='outra', is_staff=True)
        data = {'name': 'Ja Admin', 'email': 'jaadmin@teste.com', 'promote_to_admin': '1'}
        resp = self.client.post(reverse('admin_customer_edit', args=[customer.id]), data)
        self.assertEqual(resp.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertEqual(user.username, 'jaadmin@teste.com')
        self.assertTrue(user.check_password('outra'))

    def test_edit_changes_password_when_filled(self):
        customer = Customer.objects.create(
            name='Cli', email='cli@teste.com',
            password=Customer.make_password('antiga'), currency='BRL',
        )
        data = {'name': 'Cli', 'email': 'cli@teste.com', 'new_password': 'novasenha'}
        resp = self.client.post(reverse('admin_customer_edit', args=[customer.id]), data)
        self.assertEqual(resp.status_code, 302)
        customer.refresh_from_db()
        self.assertTrue(customer.check_password('novasenha'))

    def test_edit_rejects_short_password(self):
        customer = Customer.objects.create(
            name='Cli', email='cli@teste.com',
            password=Customer.make_password('antiga'), currency='BRL',
        )
        data = {'name': 'Cli', 'email': 'cli@teste.com', 'new_password': '123'}
        resp = self.client.post(reverse('admin_customer_edit', args=[customer.id]), data)
        self.assertEqual(resp.status_code, 302)
        customer.refresh_from_db()
        self.assertTrue(customer.check_password('antiga'))

    def test_list_shows_admin_badge_for_promoted(self):
        customer = Customer.objects.create(
            name='Com Conta', email='comconta@teste.com',
            password=Customer.make_password('minhasenha'), currency='BRL',
        )
        User.objects.create_user(username='comconta@teste.com', email='comconta@teste.com', password='x', is_staff=True)
        resp = self.client.get(reverse('admin_customer_list'))
        self.assertContains(resp, 'comconta@teste.com')
        self.assertContains(resp, 'Administrador')


class AdminServiceBulkDeleteTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='adminbulk', password='senha123', is_staff=True)
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')
        self.s1 = ServiceList.objects.create(service_type='Server Service', service_group=self.group, title='Produto A', slug='produto-a', price_type='fixed_price', original_price=Decimal('10.00'))
        self.s2 = ServiceList.objects.create(service_type='Server Service', service_group=self.group, title='Produto B', slug='produto-b', price_type='fixed_price', original_price=Decimal('20.00'))
        self.s3 = ServiceList.objects.create(service_type='Server Service', service_group=self.group, title='Produto C', slug='produto-c', price_type='fixed_price', original_price=Decimal('30.00'))
        self.imei = ServiceList.objects.create(service_type='IMEI Service', service_group=self.group, title='Produto IMEI', slug='produto-imei', price_type='fixed_price', original_price=Decimal('5.00'))

    def test_bulk_delete_removes_only_selected_of_type(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            reverse('admin_service_bulk_delete', args=['server']),
            {'service_ids': '{},{},{}'.format(self.s1.id, self.s2.id, self.s3.id)},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ServiceList.objects.filter(id__in=[self.s1.id, self.s2.id, self.s3.id]).exists())
        self.assertTrue(ServiceList.objects.filter(id=self.imei.id).exists())

    def test_bulk_delete_ignores_other_type_ids(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            reverse('admin_service_bulk_delete', args=['server']),
            {'service_ids': '{},{}'.format(self.s1.id, self.imei.id)},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ServiceList.objects.filter(id=self.s1.id).exists())
        self.assertTrue(ServiceList.objects.filter(id=self.imei.id).exists())

    def test_bulk_delete_empty_selection_keeps_all(self):
        self.client.force_login(self.staff)
        resp = self.client.post(reverse('admin_service_bulk_delete', args=['server']), {'service_ids': ''})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ServiceList.objects.filter(service_type='Server Service').count(), 3)


class MaintenanceModeTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='adminmanu', password='senha123', is_staff=True)
        SystemSetting.objects.filter(
            key__in=['siteMaintenanceMode', 'siteMaintenanceMsg']
        ).delete()
        self.customer = Customer.objects.create(
            name='Cliente Man', email='climan@teste.com', password=Customer.make_password('senha'),
            currency='BRL', balance=Decimal('50.00'), api_allow='on', api_key='API-MANUT',
        )
        self.group = ServiceGroup.objects.create(name='Ferramentas', slug='server', status='Active')

    def tearDown(self):
        SystemSetting.objects.filter(
            key__in=['siteMaintenanceMode', 'siteMaintenanceMsg']
        ).delete()

    def _login_customer(self):
        session = self.client.session
        session['customer_id'] = self.customer.id
        session.save()

    def test_site_normal_without_setting(self):
        resp = self.client.get(reverse('homepage'))
        self.assertEqual(resp.status_code, 200)

    def test_public_blocked_when_maintenance_on(self):
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        SystemSetting.objects.create(key='siteMaintenanceMsg', value='Volte em 1 hora')
        resp = self.client.get(reverse('homepage'))
        self.assertEqual(resp.status_code, 503)
        self.assertContains(resp, 'Volte em 1 hora', status_code=503)
        self.assertContains(resp, 'Estamos em manutenção', status_code=503)

    def test_webhook_stays_live_during_maintenance(self):
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        resp = self.client.get(reverse('binance_webhook'))
        self.assertNotEqual(resp.status_code, 503)

    def test_admin_panel_stays_live_during_maintenance(self):
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('admin_maintenance'))
        self.assertEqual(resp.status_code, 200)

    def test_staff_can_browse_public_during_maintenance(self):
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('homepage'))
        self.assertEqual(resp.status_code, 200)

    def test_toggle_on_off(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            reverse('admin_maintenance'), {'action': 'on', 'message': 'Trocando API'}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SystemSetting.get('siteMaintenanceMode'), 'on')
        self.assertEqual(SystemSetting.get('siteMaintenanceMsg'), 'Trocando API')
        # site publico bloqueado agora
        self.assertEqual(self.client.get(reverse('homepage')).status_code, 200)  # staff logado
        self.client.logout()
        self.assertEqual(self.client.get(reverse('homepage')).status_code, 503)

        self.client.force_login(self.staff)
        resp = self.client.post(reverse('admin_maintenance'), {'action': 'off'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SystemSetting.get('siteMaintenanceMode'), 'off')
        self.assertEqual(self.client.get(reverse('homepage')).status_code, 200)

    def test_web_order_blocked_during_maintenance(self):
        service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='Aluguel Teste',
            slug='aluguel-teste', status='Active', original_price=Decimal('10.00'),
        )
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        self._login_customer()
        resp = self.client.post(reverse('submit_order'), {'serviceID': service.id})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(CustomerOrder.objects.count(), 0)

    def test_public_api_order_blocked_during_maintenance(self):
        service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='Aluguel API',
            slug='aluguel-api', status='Active', original_price=Decimal('10.00'),
        )
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        resp = self.client.post('/public/api/index.php', {
            'username': self.customer.email,
            'apiaccesskey': self.customer.api_key,
            'action': 'placeimeiorder',
            'parameters': '<ID>{}</ID><QNT>1</QNT>'.format(service.id),
        })
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(CustomerOrder.objects.count(), 0)

    def test_admin_direct_order_blocked_during_maintenance(self):
        service = ServiceList.objects.create(
            service_type='Server Service', service_group=self.group, title='Aluguel Admin',
            slug='aluguel-admin', status='Active', original_price=Decimal('10.00'),
        )
        SystemSetting.objects.create(key='siteMaintenanceMode', value='on')
        self.client.force_login(self.staff)
        resp = self.client.post(reverse('admin_administrator'), {
            'serviceID': service.id,
            'customerID': self.customer.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerOrder.objects.count(), 0)

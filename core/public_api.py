import base64
import json
import re
from datetime import timedelta
from decimal import Decimal
from xml.etree import ElementTree

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Customer, CustomerOrder, OrderInput, ServiceGroup, ServiceList, Statement,
    SystemSetting,
)
from . import provider_api

API_VERSION = '1.0'
RATE_LIMIT_MINUTES = 5

STATUS_MAP = {
    'Success': 4,
    'Rejected': 3,
    'In Process': 1,
    'Waiting Action': 0,
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _json_response(data, status=200):
    payload = json.dumps(data, ensure_ascii=False, cls=DecimalEncoder)
    resp = HttpResponse(payload, status=status, content_type='application/json; charset=utf-8')
    resp['X-Powered-By'] = 'GSM-THEME'
    resp['gsmtheme-api-version'] = API_VERSION
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp['Pragma'] = 'no-cache'
    resp['Expires'] = '0'
    return resp


def _api_error(message):
    return _json_response({'ERROR': [{'MESSAGE': message}], 'apiversion': API_VERSION})


def _api_success(data):
    data = dict(data)
    data['apiversion'] = API_VERSION
    return _json_response(data)


def _api_bulk(data):
    data = dict(data)
    data['apiversion'] = API_VERSION
    return _json_response(data)


def _param(request, name, default=''):
    value = request.POST.get(name)
    if value is None:
        value = request.GET.get(name)
    return default if value is None else value


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _authenticate(request):
    username = _param(request, 'username')
    apiaccesskey = _param(request, 'apiaccesskey')
    if not username or not apiaccesskey:
        return None, 'Invalid username or api access key.'
    customer = Customer.objects.filter(email__iexact=username).first()
    if not customer or customer.status != 'Active':
        return None, 'Invalid username or api access key.'
    if str(customer.api_allow or '').strip().lower() not in ('on', '1', 'true', 'yes'):
        return None, 'Invalid username or api access key.'
    if not customer.api_key or customer.api_key.strip() != apiaccesskey.strip():
        return None, 'Invalid username or api access key.'
    if customer.api_ip:
        allowed = [x.strip() for x in customer.api_ip.replace(';', ',').split(',') if x.strip()]
        if allowed and _client_ip(request) not in allowed:
            return None, 'Your IP is not allowed to access the API.'
    return customer, None


def _group_info(service_type):
    if service_type == 'IMEI Service':
        return ('IMEI', 0)
    if service_type == 'Credit Service':
        return ('REMOTE', 2)
    return ('SERVER', 1)


def _service_price(customer, service):
    return service.original_price


def _params_dict(parameters):
    if not parameters:
        return {}
    text = str(parameters).strip()
    if text.startswith('{'):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    try:
        root = ElementTree.fromstring(text)
        if len(root):
            return {child.tag: (child.text or '') for child in root}
        return {root.tag: (root.text or '')}
    except Exception:
        pass
    try:
        return dict(re.findall(r'<(\w+)>([^<]*)</\1>', text))
    except Exception:
        return {}


def _decode_base64_json(value):
    if not value:
        return {}
    try:
        raw = base64.b64decode(str(value)).decode('utf-8')
        data = json.loads(raw)
    except Exception:
        return {}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}
    return {}


def _decode_bulk(parameters):
    if not parameters:
        return None, 'Parameters required for bulk orders.'
    text = str(parameters).strip()
    if text.startswith('{'):
        try:
            data = json.loads(text)
        except Exception:
            data = None
    else:
        try:
            data = json.loads(base64.b64decode(text).decode('utf-8'))
        except Exception:
            data = None
    if not isinstance(data, dict):
        return None, 'Parameters must be encoded with base64 and be a valid JSON object.'
    return data, None


def _to_int(value, fallback=1):
    try:
        return int(float(str(value or fallback)))
    except Exception:
        return fallback


def _order_permission(customer, service, qnt):
    if not service or service.status != 'Active':
        return False, 'Service not found or inactive.'
    qnt = max(1, _to_int(qnt, 1))
    if service.min_qnt:
        try:
            if qnt < int(service.min_qnt):
                return False, f'Minimum quantity is {service.min_qnt}.'
        except Exception:
            pass
    if service.max_qnt:
        try:
            if qnt > int(service.max_qnt):
                return False, f'Maximum quantity is {service.max_qnt}.'
        except Exception:
            pass
    return True, qnt


def _create_paid_order(customer, service, qnt, fields):
    qnt = max(1, _to_int(qnt, 1))
    price_per = _service_price(customer, service)
    total = Decimal(price_per) * qnt
    with transaction.atomic():
        if customer.balance < total:
            return None, 'Not enough balance.'
        order = CustomerOrder.objects.create(
            customer=customer,
            service=service,
            service_status='In Process',
            service_type=_service_type_key(service.service_type),
            service_qnt=str(qnt),
            service_price=total,
            service_title=service.title,
            payment_methode='Api',
            seen='false',
        )
        for inp in service.service_fields.all():
            value = str(fields.get(inp.name, '') or '')
            OrderInput.objects.create(order=order, field_name=inp.name, field_value=value)
            if not order.service_input1:
                order.service_input1 = value
                order.save(update_fields=['service_input1'])
        customer.balance = customer.balance - total
        customer.save(update_fields=['balance'])
        Statement.objects.create(
            customer=customer,
            description=f'Order #{order.id} - {service.title}',
            type='Debit', amount=total, balance=customer.balance, order=order,
        )
    return order, None


def _service_type_key(service_type):
    if service_type == 'Credit Service':
        return 'credit_service'
    if service_type == 'IMEI Service':
        return 'imei_service'
    return 'server_service'


def _account_info(customer, parameters=''):
    return _api_success({
        'SUCCESS': [{
            'MESSAGE': 'Your Account Info',
            'AccountInfo': {
                'credit': '{} {}'.format(Decimal(customer.balance).quantize(Decimal('0.01')), customer.currency),
                'creditraw': customer.balance,
                'mail': customer.email,
                'currency': customer.currency,
            },
        }],
    })


def _service_list(customer, parameters=''):
    key = 'api_sync_{}'.format(customer.id)
    last_raw = SystemSetting.get(key, '')
    if last_raw:
        try:
            last = timezone.datetime.fromisoformat(last_raw)
            if last.tzinfo is None:
                last = timezone.make_aware(last, timezone.get_current_timezone())
            elapsed = timezone.now() - last
            if elapsed < timedelta(minutes=RATE_LIMIT_MINUTES):
                remaining = int((timedelta(minutes=RATE_LIMIT_MINUTES) - elapsed).total_seconds() // 60) + 1
                return _api_error('You are calling this API too frequently! Please try after {} minutes.'.format(remaining))
        except Exception:
            pass
    obj, _ = SystemSetting.objects.get_or_create(key=key, defaults={'value': ''})
    obj.value = timezone.now().isoformat()
    obj.save()

    service_list = {}
    for group in ServiceGroup.objects.filter(status='Active').order_by('name'):
        services = list(ServiceList.objects.filter(service_group=group, status='Active').order_by('-created_at'))
        if not services:
            continue
        stype, server_val = _group_info(services[0].service_type)
        services_payload = {}
        for service in services:
            inputs = list(service.service_fields.all())
            qnt = 1 if service.min_qnt else 0
            payload = {
                'SERVICEID': service.id,
                'SERVICETYPE': stype,
                'SERVER': server_val,
                'QNT': qnt,
                'MINQNT': str(service.min_qnt or ''),
                'MAXQNT': str(service.max_qnt or ''),
                'SERVICENAME': service.title,
                'CREDIT': _service_price(customer, service),
                'TIME': str(service.delivery_time or ''),
                'INFO': '',
            }
            if service.service_type == 'IMEI Service' and inputs:
                first_input = inputs[0]
                payload['CUSTOM'] = {
                    'allow': '1',
                    'bulk': '0',
                    'customname': first_input.name,
                    'custominfo': '',
                    'customlen': '1',
                    'maxlength': '300',
                    'regex': '',
                    'isalpha': '1',
                }
                custom_fields = inputs[1:]
            else:
                custom_fields = inputs
            if custom_fields:
                fields = []
                for i, inp in enumerate(custom_fields):
                    fields.append({
                        'type': 'serviceimei',
                        'fieldname': inp.name,
                        'fieldtype': 'text',
                        'description': '',
                        'fieldoptions': '',
                        'required': 'on',
                    })
                payload['Requires.Custom'] = fields
            services_payload[str(service.id)] = payload
        service_list[group.name] = {
            'GROUPNAME': group.name,
            'GROUPTYPE': stype,
            'SERVICES': services_payload,
        }

    return _api_success({
        'SUCCESS': [{
            'MESSAGE': 'Service List',
            'LIST': service_list,
            'ACCOUNTINFO': {
                'credit': '{} {}'.format(Decimal(customer.balance).quantize(Decimal('0.01')), customer.currency),
                'creditraw': customer.balance,
                'mail': customer.email,
                'currency': customer.currency,
            },
        }],
    })


def _status_code(service_status):
    return STATUS_MAP.get(service_status, 0)


def _get_order(customer, parameters):
    params = _params_dict(parameters)
    order_id = params.get('ID')
    if order_id is None or str(order_id) == '':
        return _api_error('Parameter required.')
    try:
        order = CustomerOrder.objects.filter(customer=customer, id=_to_int(order_id, 0)).first()
    except Exception:
        order = None
    if not order:
        return _api_error('Order ID not found!')
    try:
        provider_api.sync_local_order(order)
        order.refresh_from_db()
    except Exception:
        pass
    return _api_success({
        'SUCCESS': [{
            'STATUS': _status_code(order.service_status),
            'CODE': order.service_comments or '',
        }],
    })


def _get_order_bulk(customer, parameters):
    data, err = _decode_bulk(parameters)
    if err:
        return _api_error(err)
    bulk = {}
    for ref_id, para in data.items():
        order_id = para.get('ID') if isinstance(para, dict) else None
        order = None
        if order_id is not None and str(order_id) != '':
            order = CustomerOrder.objects.filter(customer=customer, id=_to_int(order_id, 0)).first()
            try:
                provider_api.sync_local_order(order)
                order.refresh_from_db()
            except Exception:
                pass
        bulk[ref_id] = {
            'SUCCESS': [{
                'STATUS': _status_code(order.service_status) if order else 0,
                'CODE': (order.service_comments or '') if order else '',
            }],
        }
    return _api_bulk(bulk)


def _place_order(customer, parameters):
    params = _params_dict(parameters)
    service_id = params.get('ID')
    if service_id is None or str(service_id) == '':
        return _api_error('Parameter or Service <ID> missing.')
    service = ServiceList.objects.filter(id=_to_int(service_id, 0)).first()
    qnt = _to_int(params.get('QNT', 1), 1)
    fields = _decode_base64_json(params.get('CUSTOMFIELD', ''))
    allowed, message = _order_permission(customer, service, qnt)
    if not allowed:
        return _api_error(message)
    order, err = _create_paid_order(customer, service, qnt, fields)
    if err:
        return _api_error(err)
    forwarded, msg = provider_api.submit_local_order(order)
    if forwarded is False:
        provider_api.refund_order(order, msg or 'Falha ao enviar para o provedor.')
    return _api_success({
        'SUCCESS': [{
            'MESSAGE': 'Order received',
            'REFERENCEID': order.id,
        }],
    })


def _place_bulk_order(customer, parameters):
    data, err = _decode_bulk(parameters)
    if err:
        return _api_error(err)
    bulk = {}
    for ref_id, req in data.items():
        if not isinstance(req, dict):
            bulk[ref_id] = {'status': 'error', 'message': 'Invalid order request.'}
            continue
        service_id = req.get('ID')
        if not service_id or str(service_id) == '':
            bulk[ref_id] = {'status': 'error', 'message': 'Missing targeted service id.'}
            continue
        service = ServiceList.objects.filter(id=_to_int(service_id, 0)).first()
        qnt = _to_int(req.get('QNT', 1), 1)
        fields = _decode_base64_json(req.get('CUSTOMFIELD', ''))
        allowed, message = _order_permission(customer, service, qnt)
        if not allowed:
            bulk[ref_id] = {'status': 'error', 'message': message}
            continue
        order, err = _create_paid_order(customer, service, qnt, fields)
        if err:
            bulk[ref_id] = {'status': 'error', 'message': err}
        else:
            forwarded, msg = provider_api.submit_local_order(order)
            if forwarded is False:
                provider_api.refund_order(order, msg or 'Falha ao enviar para o provedor.')
            bulk[ref_id] = {'status': 'success', 'message': 'Order received', 'referenceid': order.id}
    return _api_bulk(bulk)


ACTIONS = {
    'accountinfo': _account_info,
    'imeiservicelist': _service_list,
    'getimeiorder': _get_order,
    'getimeiorderbulk': _get_order_bulk,
    'placeimeiorder': _place_order,
    'placebulkorder': _place_bulk_order,
}


@csrf_exempt
def public_api(request):
    customer, auth_error = _authenticate(request)
    if auth_error:
        return _api_error(auth_error)
    action = str(_param(request, 'action', '')).strip().lower()
    handler = ACTIONS.get(action)
    if not handler:
        return _api_error('Invalid Action')
    parameters = _param(request, 'parameters', '')
    try:
        return handler(customer, parameters)
    except Exception as exc:
        return _api_error(str(exc)[:1000])


def generate_api_key():
    return get_random_string(32)
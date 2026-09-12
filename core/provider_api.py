import base64
import json
import logging
import urllib.parse
import urllib.request

from .models import Api, ApiLog, CustomerOrder, InventoryData, RemoteServiceList, Statement

logger = logging.getLogger(__name__)

TIMEOUT = 30

PROVIDER_ACTIONS = {
    'Server Service': {
        'list': 'serverservicelist',
        'place': 'placeserverorder',
        'get': 'getserverorder',
    },
    'Credit Service': {
        'list': 'creditservicelist',
        'place': 'placecreditorder',
        'get': 'getcreditorder',
    },
    'IMEI Service': {
        'list': 'imeiservicelist',
        'place': 'placeimeiorder',
        'get': 'getimeiorder',
    },
}

LOCAL_TO_PROVIDER_STATUS = {
    'Waiting Action': 0,
    'In Process': 1,
    'Rejected': 3,
    'Success': 4,
}

PROVIDER_TO_LOCAL_STATUS = {v: k for k, v in LOCAL_TO_PROVIDER_STATUS.items()}


class ProviderError(Exception):
    pass


def actions_for(service):
    """Acoes do provedor para o servico. Decide primeiro pelo SERVICETYPE do
    produto remoto vinculado (fonte da verdade), depois pelo tipo local."""
    if service.api_id and str(service.referenceid or '').strip():
        remote = RemoteServiceList.objects.filter(
            api_id=service.api, referenceid=str(service.referenceid).strip(),
        ).first()
        if remote:
            rtype = (remote.SERVICETYPE or '').strip().upper()
            if rtype == 'REMOTE':
                return PROVIDER_ACTIONS['Credit Service']
            if rtype == 'IMEI':
                return PROVIDER_ACTIONS['IMEI Service']
            if rtype == 'SERVER':
                return PROVIDER_ACTIONS['Server Service']
    return PROVIDER_ACTIONS.get(service.service_type, PROVIDER_ACTIONS['Server Service'])


def endpoint_for(api):
    """Normaliza api_url para o endpoint index.php do provedor."""
    url = (api.api_url or '').strip().rstrip('/')
    if not url:
        return ''
    if url.endswith('.php'):
        return url
    if url.endswith('/api'):
        return url + '/index.php'
    return url + '/api/index.php'


def _log(api, message, log_for='provider_api'):
    try:
        ApiLog.objects.create(log=str(message)[:2000], log_for=log_for, api=api)
    except Exception:
        logger.exception('Falha ao gravar ApiLog')


def _request(api, action, parameters=''):
    url = endpoint_for(api)
    if not url:
        raise ProviderError('API URL nao configurada.')
    payload = urllib.parse.urlencode({
        'username': (api.api_username or '').strip(),
        'apiaccesskey': (api.api_key or '').strip(),
        'action': action,
        'parameters': parameters or '',
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode('utf-8')
    except Exception as exc:
        raise ProviderError('Falha ao conectar no provedor: {}'.format(exc))
    try:
        data = json.loads(body)
    except ValueError:
        raise ProviderError('Resposta invalida do provedor: {}'.format(str(body)[:200]))
    if isinstance(data, dict) and 'ERROR' in data:
        errs = data.get('ERROR') or []
        message = errs[0].get('MESSAGE', 'Erro do provedor') if errs else 'Erro do provedor'
        raise ProviderError(str(message))
    return data


def _success_rows(data):
    rows = data.get('SUCCESS') or []
    if not rows:
        raise ProviderError('Resposta sem SUCCESS do provedor.')
    return rows


def account_info(api):
    data = _request(api, 'accountinfo')
    row = _success_rows(data)[0]
    info = row.get('AccountInfo') or {}
    return {
        'credit': info.get('credit', ''),
        'creditraw': info.get('creditraw', 0),
        'mail': info.get('mail', ''),
        'currency': info.get('currency', ''),
    }


def service_list(api):
    data = _request(api, PROVIDER_ACTIONS['IMEI Service']['list'])
    row = _success_rows(data)[0]
    return row.get('LIST') or {}


def _extract_service_fields(svc):
    fields = []
    custom = svc.get('CUSTOM') or {}
    if custom.get('customname'):
        fields.append(str(custom.get('customname')))
    for f in (svc.get('Requires.Custom') or []) or []:
        name = (f or {}).get('fieldname')
        if name and name not in fields:
            fields.append(str(name))
    return fields


def fetch_catalog(api):
    """Baixa a lista de servicos do provedor e devolve uma lista plana."""
    data = _request(api, PROVIDER_ACTIONS['IMEI Service']['list'])
    row = _success_rows(data)[0]
    listing = row.get('LIST') or {}
    catalog = []
    for group_name, group in (listing or {}).items():
        services = group.get('SERVICES') or {}
        group_type = group.get('GROUPTYPE') or ''
        for sid, svc in (services or {}).items():
            catalog.append({
                'referenceid': str(sid),
                'name': (svc.get('SERVICENAME') or ''),
                'servicetype': (svc.get('SERVICETYPE') or group_type),
                'credit': svc.get('CREDIT') or 0,
                'group': group_name,
                'time': (svc.get('TIME') or ''),
                'fields': _extract_service_fields(svc),
            })
    catalog.sort(key=lambda x: (x['servicetype'], x['name']))
    return catalog


def _fields_dict(order):
    from .models import OrderInput
    return {i.field_name: i.field_value for i in OrderInput.objects.filter(order=order)}


def _extract_credentials(row):
    """Extrai credenciais (email/usuario e senha) devolvidas pelo provedor."""
    pairs = []
    for k, v in (row or {}).items():
        low = str(k).lower().replace('_', '')
        text = str(v or '').strip()
        if not text or text in ('0', 'None', ''):
            continue
        if 'pass' in low or 'senha' in low or 'credential' in low:
            pairs.append(('Senha', text))
        elif 'mail' in low or 'email' in low or 'user' in low or 'login' in low or 'account' in low:
            label = 'Email' if ('mail' in low or 'email' in low) else 'Usuario'
            pairs.append((label, text))
    seen = set()
    result = []
    for label, value in pairs:
        if value.lower() not in seen:
            seen.add(value.lower())
            result.append('{}: {}'.format(label, value))
    return ' | '.join(result)


def provider_for_order(order):
    """Retorna a Api vinculada ao servico do pedido, ou None se nao for automático."""
    service = order.service
    if not service:
        return None
    if not service.api_enabled:
        return None
    api = service.api
    if not api or api.status != 'Active':
        return None
    if not (api.api_url or '').strip() or not (api.api_username or '').strip() or not (api.api_key or '').strip():
        return None
    if not (service.referenceid or '').strip():
        return None
    return api


def deliver_from_inventory(order):
    """Entrega automaticamente o próximo login/senha disponível do estoque do serviço.

    Retorna (True, código) se entregou, (False, motivo) se não foi possível.
    Preenche a resposta do pedido, marca como Success e consome a credencial.
    """
    service = order.service
    if not service:
        return False, 'Serviço não vinculado ao pedido.'
    inventory = service.inventory
    if not inventory:
        return False, 'Serviço sem estoque vinculado.'
    item = InventoryData.objects.filter(inventory=inventory, status='Available').order_by('id').first()
    if not item:
        return False, 'Nenhuma credencial disponível no estoque.'
    item.status = 'Sold out'
    item.order = order
    item.save(update_fields=['status', 'order'])
    order.replied_in = (item.code or '')[:500]
    order.service_status = 'Success'
    order.service_comments = (order.service_comments or '') + ' Login/senha entregues do estoque #{}.'.format(item.id)
    order.save(update_fields=['replied_in', 'service_status', 'service_comments'])
    try:
        from .views_admin import _refresh_inventory_counts
        _refresh_inventory_counts(inventory)
    except Exception:
        pass
    return True, item.code


def submit_local_order(order):
    """Envia o pedido local ao provedor. Retorna (None, '') se não automático,
    (False, erro) se falhou, (True, ref) se enviado."""
    api = provider_for_order(order)
    if api is None:
        delivered, code = deliver_from_inventory(order)
        return (True, code) if delivered else (None, '')
    service = order.service
    actions = actions_for(service)
    fields = _fields_dict(order)
    custom = base64.b64encode(json.dumps(fields).encode('utf-8')).decode('utf-8') if fields else ''
    params = json.dumps({
        'ID': (service.referenceid or '').strip(),
        'QNT': str(order.service_qnt or 1),
        'CUSTOMFIELD': custom,
    })
    try:
        data = _request(api, actions['place'], params)
        row = _success_rows(data)[0]
    except ProviderError as exc:
        _log(api, 'place fail order #{}: {}'.format(order.id, exc))
        return False, str(exc)
    ref = row.get('REFERENCEID') or row.get('referenceid')
    if not str(ref or '').strip():
        _log(api, 'place empty ref order #{}: {}'.format(order.id, row))
        return False, 'Provedor nao retornou numero do pedido.'
    code = (row.get('CODE', '') or row.get('code', '') or '').strip()
    status = row.get('STATUS') or row.get('status') or ''
    order.trx_id = str(ref)
    order.process_type = 'Auto'
    updates = ['trx_id', 'process_type']
    if code:
        order.service_comments = code
        order.replied_in = code
        updates += ['service_comments', 'replied_in']
    order.save(update_fields=updates)
    try:
        _log(api, 'place OK order #{} status={} ref={}: {}'.format(
            order.id, status, ref, json.dumps(row, ensure_ascii=False)[:1800]))
    except Exception:
        pass
    return True, str(ref)


def refund_order(order, message):
    customer = order.customer
    amount = order.service_price
    customer.balance = customer.balance + amount
    customer.save(update_fields=['balance'])
    Statement.objects.create(
        customer=customer,
        description='Reembolso Order #{}'.format(order.id),
        type='Credit', amount=amount, balance=customer.balance, order=order,
    )
    order.service_status = 'Rejected'
    order.service_comments = message
    order.save(update_fields=['service_status', 'service_comments'])


def sync_local_order(order):
    """Consulta o status do pedido no provedor e atualiza o pedido local."""
    api = provider_for_order(order)
    if api is None or not (order.trx_id or '').strip():
        return False
    service = order.service
    actions = actions_for(service)
    params = json.dumps({'ID': (order.trx_id or '').strip()})
    try:
        data = _request(api, actions['get'], params)
        row = _success_rows(data)[0]
        status = int(float(row.get('STATUS', 0) or 0))
        code = row.get('CODE', '') or ''
    except ProviderError as exc:
        _log(api, 'sync fail order #{}: {}'.format(order.id, exc))
        return False

    target = PROVIDER_TO_LOCAL_STATUS.get(status, order.service_status)
    changed = False

    if target == 'Rejected' and order.service_status != 'Rejected':
        refund_order(order, code or 'Pedido rejeitado pelo provedor.')
        return True

    if target != order.service_status:
        order.service_status = target
        changed = True
    if code and code != (order.service_comments or ''):
        order.service_comments = code
        changed = True
    if changed:
        order.save(update_fields=['service_status', 'service_comments'])
    return True
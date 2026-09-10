import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date


class AsaasError(Exception):
    def __init__(self, payload, code):
        self.payload = payload
        self.code = code
        self.errors = payload.get('errors', []) if isinstance(payload, dict) else []
        super().__init__(str(payload)[:1000])


def api_base(gateway):
    if getattr(gateway, 'asaas_sandbox', True):
        return 'https://api-sandbox.asaas.com/v3'
    return 'https://api.asaas.com/v3'


def call(gateway, method, path, body=None, timeout=40):
    url = api_base(gateway) + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('access_token', (gateway.asaas_api_key or '').strip())
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8'))
        except Exception:
            detail = {'message': str(exc)}
        raise AsaasError(detail, exc.code) from exc


def get_or_create_customer(gateway, customer):
    email = urllib.parse.quote(customer.email or '')
    data = call(gateway, 'GET', f'/customers?email={email}')
    for row in data.get('data', []):
        if row.get('email') and row['email'].lower() == (customer.email or '').lower():
            if customer.cpf_cnpj:
                row_cpf = (''.join(ch for ch in (row.get('cpfCnpj') or '') if ch.isdigit()))
                new_cpf = (''.join(ch for ch in customer.cpf_cnpj if ch.isdigit()))
                if row_cpf and row_cpf != new_cpf:
                    call(gateway, 'POST', f'/customers/{row["id"]}', body={
                        'name': customer.name or 'Cliente',
                        'cpfCnpj': new_cpf,
                        'mobilePhone': customer.mobile or '',
                    })
            return row['id']
    body = {
        'name': customer.name or 'Cliente',
        'cpfCnpj': (''.join(ch for ch in (customer.cpf_cnpj or '') if ch.isdigit())) or None,
        'email': customer.email,
        'mobilePhone': customer.mobile or '',
        'notificationDisabled': False,
    }
    resp = call(gateway, 'POST', '/customers', body)
    return resp.get('id')


def create_pix_payment(invoice, gateway):
    customer_id = get_or_create_customer(gateway, invoice.customer)
    body = {
        'customer': customer_id,
        'billingType': 'PIX',
        'value': float(invoice.invoice_amount),
        'dueDate': date.today().isoformat(),
        'description': f'Fatura #{invoice.id} - {invoice.invoice_title or invoice.invoice_for or "Deposito"}',
        'externalReference': f'invoice-{invoice.id}',
    }
    payment = call(gateway, 'POST', '/payments', body)
    if not payment.get('id'):
        raise AsaasError(payment, 200)
    payment.update(get_pix_qr_code(gateway, payment['id']))
    return payment


def get_pix_qr_code(gateway, payment_id):
    return call(gateway, 'GET', f'/payments/{payment_id}/pixQrCode')


def get_payment(gateway, payment_id):
    return call(gateway, 'GET', f'/payments/{payment_id}')
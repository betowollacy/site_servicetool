import json
import time
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

from django.db import models


class VepayError(Exception):
    pass


def _amount_kz(aoa, rate):
    """Converte o valor (em reais) para Kwanzas inteiros usando a taxa BRL→Kz do gateway."""
    total = (Decimal(str(aoa)) * Decimal(str(rate))).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return int(total)


def _request(base_url, path, payload, api_key, timeout=40):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(base_url.rstrip('/') + path, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer {}'.format(api_key))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8'))
        except Exception:
            detail = {'message': str(exc)}
        raise VepayError(json.dumps(detail)[:1000]) from exc
    except urllib.error.URLError as exc:
        raise VepayError(str(exc)[:500]) from exc


def _pick(data, keys):
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    return ''


def create_payment(invoice, gateway, reference=None):
    """Cria um payment Vepay (cobrança Multicaixa Express) e devolve o link de checkout.

    Retorna um dict com: id, reference, amount_kz, checkout_url e data (resposta crua)."""
    api_key = (gateway.vepay_api_key or '').strip()
    base_url = (gateway.vepay_base_url or '').strip()
    phone = (gateway.vepay_receiving_number or '').strip()
    if not api_key:
        raise VepayError('API Key Vepay nao configurada.')
    if not base_url:
        raise VepayError('URL base da API Vepay nao configurada.')
    if not phone:
        raise VepayError('Numero de recebimento Vepay nao configurado.')

    rate = Decimal(gateway.vepay_rate or 0)
    if rate <= 0:
        raise VepayError('Taxa de cambio BRL->Kwanza nao configurada.')
    amount = _amount_kz(invoice.invoice_amount, rate)
    if amount <= 0:
        raise VepayError('Valor em Kwanzas invalido para esta fatura.')

    reference = reference or 'INV{}{}'.format(invoice.id, int(time.time() * 1000))
    payload = {
        'amount': amount,
        'phone': phone,
        'reference': reference,
    }
    data = _request(base_url, '/v1/payments', payload, api_key)
    checkout_url = _pick(data, (
        'checkout_url', 'checkoutUrl', 'checkout_link', 'checkoutLink',
        'payment_url', 'paymentUrl', 'url', 'link',
    ))
    payment_id = _pick(data, ('payment_id', 'paymentId', 'id', 'uuid', 'reference'))
    if not checkout_url:
        raise VepayError('Resposta Vepay sem link de checkout: {}'.format(json.dumps(data)[:1000]))
    return {
        'id': payment_id or reference,
        'reference': reference,
        'amount_kz': amount,
        'checkout_url': checkout_url,
        'data': data,
    }
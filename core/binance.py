import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

OPENAPI = 'https://bpay.binanceapi.com/binancepay/openapi'


class BinanceError(Exception):
    pass


def hmac_signature(payload_str, secret_key):
    """Assinatura Binance Pay: hex(HMAC-SHA512(payload, secret)).toUpperCase()."""
    digest = hmac.new(
        secret_key.encode('utf-8'),
        payload_str.encode('utf-8'),
        hashlib.sha512,
    ).hexdigest()
    return digest.upper()


def _sign(payload_str, gateway):
    secret = (gateway.binance_secret_key or '').strip()
    if not secret:
        raise BinanceError('Chave secreta (API Secret) da Binance nao configurada.')
    return hmac_signature(payload_str, secret)


def _request(gateway, path, body, timeout=40):
    body_str = json.dumps(body)
    ts = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex[:32]
    payload_str = '{}\n{}\n{}\n'.format(ts, nonce, body_str)
    signature = _sign(payload_str, gateway)
    req = urllib.request.Request(OPENAPI + path, data=body_str.encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('BinancePay-Timestamp', ts)
    req.add_header('BinancePay-Nonce', nonce)
    req.add_header('BinancePay-Certificate-SN', (gateway.binance_api_key or '').strip())
    req.add_header('BinancePay-Signature', signature)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8'))
        except Exception:
            detail = {'message': str(exc)}
        raise BinanceError(json.dumps(detail)) from exc


def create_order(invoice, gateway):
    merchant_trade_no = f'INV{invoice.id}T{int(time.time() * 1000)}'
    body = {
        'env': {'terminalType': 'WEB'},
        'merchantTradeNo': merchant_trade_no,
        'orderAmount': float(invoice.invoice_amount),
        'currency': 'USDT',
        'walletType': 'CASH',
        'goods': {
            'goodsType': '01',
            'goodsCategory': 'Z000',
            'referenceGoodsId': f'invoice-{invoice.id}',
            'goodsName': invoice.invoice_title or invoice.invoice_for or 'Deposito',
        },
        'tradeType': 'WEB',
    }
    resp = _request(gateway, '/v2/order', body)
    if resp.get('status') != 'SUCCESS':
        raise BinanceError(json.dumps(resp)[:1000])
    data = resp.get('data') or {}
    data['merchantTradeNo'] = merchant_trade_no
    return data


def query_order(gateway, merchant_trade_no):
    body = {'merchantTradeNo': merchant_trade_no}
    resp = _request(gateway, '/v2/order/query', body)
    if resp.get('status') != 'SUCCESS':
        raise BinanceError(json.dumps(resp)[:1000])
    rows = resp.get('data') or []
    if isinstance(rows, list):
        for row in rows:
            if row.get('merchantTradeNo') == merchant_trade_no:
                return row
    return None


def verify_notification(gateway, signature, payload_str, timestamp='', nonce=''):
    """Verifica a assinatura de um webhook da Binance Pay.

    Payload: timestamp + '\\n' + nonce + '\\n' + body + '\\n'.
    Primeiro tenta HMAC-SHA512 (segredo do merchant); se falhar, tenta RSA com a
    chave publica da Binance consultada no endpoint /certificates.
    """
    if not signature:
        return False
    payload = '{}\n{}\n{}\n'.format(timestamp, nonce, payload_str)

    secret = (gateway.binance_secret_key or '').strip()
    if secret:
        expected = hmac_signature(payload, secret)
        if hmac.compare_digest(expected, signature.strip()):
            return True

    try:
        cert_resp = _request(gateway, '/certificates', {})
    except Exception:
        return False
    if cert_resp.get('status') != 'SUCCESS':
        return False
    data = cert_resp.get('data') or {}
    cert_list = data.get('certificates') or (data if isinstance(data, list) else [])
    cert = ''
    for entry in cert_list:
        entry = entry or {}
        cert = str(entry.get('certPublic') or entry.get('certificate') or '').strip()
        if cert:
            break
    if not cert:
        return False
    try:
        public_key = serialization.load_pem_public_key(cert.encode('utf-8'))
        public_key.verify(
            base64.b64decode(signature),
            payload.encode('utf-8'),
            asym_padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
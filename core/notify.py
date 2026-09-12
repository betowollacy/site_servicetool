import logging
import urllib.parse
import urllib.request

from .models import SystemSetting

logger = logging.getLogger(__name__)


def send_telegram(text):
    token = SystemSetting.get('tgBotToken', '').strip()
    chat_id = SystemSetting.get('tgChatId', '').strip()
    if not token or not chat_id:
        return False
    try:
        payload = urllib.parse.urlencode({
            'chat_id': chat_id,
            'text': text,
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.telegram.org/bot{}/sendMessage'.format(token),
            data=payload,
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning('Falha ao enviar notificacao Telegram: %s', exc)
        return False


def new_order_message(order, paid):
    lines = [
        'NOVO PEDIDO #{}'.format(order.id),
        '-' * 30,
        'Cliente: {}'.format(order.customer.name),
        'E-mail: {}'.format(order.customer.email),
        'Servico: {}'.format(order.service_title),
    ]
    for oi in order.order_inputs.all():
        if oi.field_value:
            lines.append('{}: {}'.format(oi.field_name, oi.field_value))
    lines.append('Valor: R$ {}'.format(order.service_price))
    lines.append('Pagamento: {}'.format('Pago via saldo' if paid else 'Aguardando pagamento'))
    return '\n'.join(lines)
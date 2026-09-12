import logging
import urllib.parse
import urllib.request

from .models import SystemSetting

logger = logging.getLogger(__name__)

STATUS_DISPLAY = {
    'Waiting Action': 'Aguardando',
    'In Process': 'Em processamento',
    'Success': 'Finalizado',
    'Rejected': 'Rejeitado',
}


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


def _order_body(order):
    lines = [
        'Cliente: {}'.format(order.customer.name),
        'E-mail: {}'.format(order.customer.email),
        'Servico: {}'.format(order.service_title),
    ]
    for oi in order.order_inputs.all():
        if oi.field_value:
            lines.append('{}: {}'.format(oi.field_name, oi.field_value))
    return lines


def _order_result(order):
    return ((order.replied_in or '') or (order.service_comments or '')).strip()


def new_order_message(order, paid):
    lines = ['NOVO PEDIDO #{}'.format(order.id), '-' * 30] + _order_body(order)
    lines.append('Valor: R$ {}'.format(order.service_price))
    lines.append('Pagamento: {}'.format('Pago via saldo' if paid else 'Aguardando pagamento'))
    lines.append('Status: {}'.format(STATUS_DISPLAY.get(order.service_status, order.service_status)))
    result = _order_result(order)
    if result:
        lines.append('Resultado: {}'.format(result))
    return '\n'.join(lines)


def completed_order_message(order):
    lines = ['PEDIDO #{} FINALIZADO'.format(order.id), '-' * 30] + _order_body(order)
    lines.append('Resultado: {}'.format(_order_result(order) or '(sem resposta do provedor)'))
    return '\n'.join(lines)


def rejected_order_message(order, motivo):
    lines = ['PEDIDO #{} REJEITADO'.format(order.id), '-' * 30]
    lines.append('Cliente: {}'.format(order.customer.name))
    lines.append('Servico: {}'.format(order.service_title))
    lines.append('Motivo: {}'.format((motivo or '').strip() or '(sem motivo informado)'))
    lines.append('Valor estornado ao saldo: R$ {}'.format(order.service_price))
    return '\n'.join(lines)

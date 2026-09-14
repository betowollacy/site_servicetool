import html
import logging
import re
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

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


def _brl(value):
    try:
        raw = '{:,.2f}'.format(float(value or 0))
    except (TypeError, ValueError):
        raw = '0.00'
    return 'R$ ' + raw.replace(',', '@').replace('.', ',').replace('@', '.')


def _dt(value):
    if not value:
        return '-'
    try:
        return value.strftime('%d-%m-%Y %H:%M')
    except AttributeError:
        return '-'


def _site_name():
    return SystemSetting.get('siteTitle', 'SERVICETOOL')


def _history_url():
    try:
        base = (settings.SITE_URL or 'http://127.0.0.1:8000').rstrip('/')
        return '{}{}'.format(base, reverse('customer_order_history'))
    except Exception:
        return '#'


_PAIR_RE = re.compile(
    r'^\s*(?:usuario|username|user)\s*[:\-=]\s*(.+?)\s*[|]\s*'
    r'(?:senha|password|pass|passwd|pwd)\s*[:\-=]\s*(.+?)\s*$', re.I)
_LABEL_USER_RE = re.compile(r'^\s*(?:usuario|username|user|login)\s*[:\-=]\s*(.+?)\s*$', re.I)
_LABEL_PASS_RE = re.compile(r'^\s*(?:senha|password|pass|passwd|pwd)\s*[:\-=]\s*(.+?)\s*$', re.I)


def _split_creds(text):
    """Tenta extrair usuário/senha da resposta do provedor.

    Aceita formatos como 'Usuario: u | Senha: s', 'Username: u\\nPassword: s',
    'user||pass', 'user:pass' e 'user;pass'. Retorna (None, None) se não conseguir.
    """
    if not text:
        return None, None
    text = re.sub(r'<br\s*/?>', ' ', str(text), flags=re.IGNORECASE).strip()
    m = _PAIR_RE.match(text)
    if m:
        user, passw = m.group(1).strip(), m.group(2).strip()
        return (user, passw) if user and passw else (None, None)
    user = passw = None
    for line in text.replace('\r', '').split('\n'):
        line = line.strip()
        if not line or '|' in line:
            continue
        if user is None:
            m = _LABEL_USER_RE.match(line)
            if m and m.group(1).strip():
                user = m.group(1).strip()
        if passw is None:
            m = _LABEL_PASS_RE.match(line)
            if m and m.group(1).strip():
                passw = m.group(1).strip()
    if user and passw:
        return user, passw
    for sep in ('||', '|', ';'):
        parts = [p.strip() for p in text.split(sep) if p.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1]
    m = re.match(r'^\s*([^:\s]+)\s*:\s*(.+?)\s*$', text)
    if m and m.group(2).strip():
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def completed_order_email(order):
    """Constrói o e-mail de conclusão enviado ao cliente.

    Retorna dict com 'subject', 'text' (plain) e 'html'."""
    customer = getattr(order, 'customer', None)
    name = (customer.name if customer and customer.name else '').strip()
    if not name and customer and customer.email:
        name = customer.email.split('@')[0]
    name = name or 'Cliente'
    site = _site_name()
    service_title = (
        (order.service_title or '')
        or (order.service.title if order.service else '')
        or 'Pedido #{}'.format(order.id)
    ).strip()
    reply = ((order.replied_in or '') or (order.service_comments or '')).strip()
    user, passw = _split_creds(reply)
    submitted = _dt(order.created_at)
    replied = _dt(order.updated_at)
    price = _brl(order.service_price)
    platform = order.process_type or 'Manual'
    url = _history_url()
    year = timezone.now().year

    subject = 'Pedido #{} concluído com sucesso'.format(order.id)

    detail_rows = [
        ('Pedido', '#{}'.format(order.id)),
        ('Preço', price),
        ('Enviado em', submitted),
        ('Respondido em', replied),
        ('Plataforma', platform),
        ('Status', 'Concluído ✅'),
    ]
    cred_rows = []
    if user:
        cred_rows = [('Usuário', user), ('Senha', passw or '')]
    elif reply:
        cred_rows = [('Resposta', reply)]

    text_lines = [
        'Olá, {},'.format(name),
        '',
        'Temos o prazer de informar que seu pedido foi concluído com sucesso. Confira os detalhes completos abaixo:',
        '',
        service_title,
        '',
    ]
    table = ['{}: {}'.format(k, v) for k, v in detail_rows + cred_rows]
    if table:
        text_lines += table[:6] + ([''] if len(table) > 6 else []) + table[6:]
    text_lines += [
        '',
        'Ver histórico completo: {}'.format(url),
        '',
        'Obrigado por escolher {}.'.format(site),
        'Agradecemos sua confiança em nossos serviços.',
        '',
        'Termos de Uso | Política de Reembolso | Política de Privacidade',
        '',
        '© {} {}. Todos os direitos reservados.'.format(year, site),
    ]
    text = '\n'.join(text_lines)

    def row_cells(k, v):
        return (
            '<tr>'
            '<td style="padding:4px 14px 4px 0;color:#475569;font-weight:600;'
            'white-space:nowrap;vertical-align:top;">{}</td>'
            '<td style="padding:4px 0;color:#0f172a;">{}</td>'
            '</tr>'
        ).format(html.escape(k), html.escape(v))

    details_html = ''.join(row_cells(k, v) for k, v in detail_rows)
    creds_html = ''
    if cred_rows:
        creds_html = (
            '<div style="border:1px solid #22c55e;border-radius:8px;padding:14px 16px;margin:14px 0;background:#f0fdf4;">'
            '<table cellpadding="0" cellspacing="0" border="0">{}</table></div>'
        ).format(''.join(row_cells(k, v) for k, v in cred_rows))

    html_body = (
        '<html><body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a;">'
        '<div style="max-width:600px;margin:0 auto;background:#ffffff;">'
        '<div style="background:#111827;color:#ffffff;padding:22px;text-align:center;">'
        '<span style="font-size:20px;font-weight:bold;">{site}</span></div>'
        '<div style="padding:26px;">'
        '<p style="margin:0 0 12px;">Olá, <b>{name}</b>,</p>'
        '<p style="margin:0 0 16px;">Temos o prazer de informar que seu pedido foi concluído '
        'com sucesso. Confira os detalhes completos abaixo:</p>'
        '<div style="border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;margin:0 0 6px;">'
        '<div style="font-size:17px;font-weight:bold;color:#111827;margin-bottom:8px;">{service_title}</div>'
        '<table cellpadding="0" cellspacing="0" border="0">{details}</table></div>'
        '{creds}'
        '<a href="{url}" style="display:inline-block;background:#111827;color:#ffffff;'
        'padding:11px 20px;border-radius:6px;text-decoration:none;margin-top:14px;">'
        'Ver histórico completo</a>'
        '<p style="margin:22px 0 0;">Obrigado por escolher <b>{site}</b>.<br>'
        'Agradecemos sua confiança em nossos serviços.</p>'
        '</div>'
        '<div style="padding:16px;background:#f8fafc;border-top:1px solid #e2e8f0;'
        'text-align:center;color:#64748b;font-size:12px;">'
        'Termos de Uso | Política de Reembolso | Política de Privacidade<br>'
        '© {year} {site}. Todos os direitos reservados.</div>'
        '</div></body></html>'
    ).format(
        site=html.escape(site),
        name=html.escape(name),
        service_title=html.escape(service_title),
        details=details_html,
        creds=creds_html,
        url=html.escape(url),
        year=year,
    )

    return {'subject': subject, 'text': text, 'html': html_body}


def _smtp_send(to, subject, text, html_body):
    """Envia um e-mail pelo SMTP configurado. Retorna True se enviado.

    Sem mailHost configurado, retorna False sem erro."""
    to = (to or '').strip()
    if not to:
        return False
    host = SystemSetting.get('mailHost', '').strip()
    if not host:
        return False
    try:
        port = int(SystemSetting.get('mailPort', '587') or '587')
    except (TypeError, ValueError):
        port = 587
    user = SystemSetting.get('mailUser', '').strip()
    password = SystemSetting.get('mailPass', '')
    sender = SystemSetting.get('mailFrom', '').strip() or (user or to)
    from_name = SystemSetting.get('mailFromName', '').strip() or _site_name()
    use_tls = SystemSetting.get('mailUseTls', '1').strip().lower() in ('1', 'true', 'yes', 'on')
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = '{} <{}>'.format(html.escape(from_name), sender)
    msg['To'] = to
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype='html')
    try:
        if port == 465:
            conn = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            conn = smtplib.SMTP(host, port, timeout=20)
        try:
            if use_tls and port != 465:
                conn.starttls()
            if user:
                conn.login(user, password)
            conn.sendmail(sender, [to], msg.as_string())
        finally:
            try:
                conn.quit()
            except Exception:
                pass
        logger.info('E-mail enviado para %s: %s', to, subject)
        return True
    except Exception as exc:
        logger.warning('Falha ao enviar e-mail "%s" para %s: %s', subject, to, exc)
        return False


def new_order_email(order, paid):
    """Constrói o e-mail de novo pedido enviado ao dono da loja.""" 
    site = _site_name()
    subject = 'NOVO PEDIDO #{}'.format(order.id)
    details = [
        ('Cliente', order.customer.name or '-'),
        ('E-mail', order.customer.email or '-'),
        ('Serviço', order.service_title or (order.service.title if order.service else '') or '-'),
    ]
    for oi in order.order_inputs.all():
        if oi.field_value:
            details.append((oi.field_name, oi.field_value))
    details += [
        ('Valor', _brl(order.service_price)),
        ('Pagamento', 'Pago via saldo' if paid else 'Aguardando pagamento'),
        ('Status', STATUS_DISPLAY.get(order.service_status, order.service_status)),
    ]
    result = _order_result(order)
    if result:
        details.append(('Resultado', result))

    text = '\n'.join(
        ['NOVO PEDIDO #{}'.format(order.id), '-' * 30]
        + ['{}: {}'.format(k, v) for k, v in details]
    )
    rows = ''.join(
        '<tr>'
        '<td style="padding:4px 14px 4px 0;color:#475569;font-weight:600;'
        'white-space:nowrap;vertical-align:top;">{}</td>'
        '<td style="padding:4px 0;color:#0f172a;">{}</td>'
        '</tr>'.format(html.escape(k), html.escape(str(v)))
        for k, v in details
    )
    html_body = (
        '<html><body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a;">'
        '<div style="max-width:600px;margin:0 auto;background:#ffffff;">'
        '<div style="background:#111827;color:#ffffff;padding:22px;text-align:center;">'
        '<span style="font-size:20px;font-weight:bold;">{site}</span></div>'
        '<div style="padding:26px;">'
        '<div style="font-size:18px;font-weight:bold;color:#111827;margin-bottom:14px;">NOVO PEDIDO #{oid}</div>'
        '<table cellpadding="0" cellspacing="0" border="0">{rows}</table>'
        '</div>'
        '<div style="padding:16px;background:#f8fafc;border-top:1px solid #e2e8f0;'
        'text-align:center;color:#64748b;font-size:12px;">'
        '© {year} {site}. Todos os direitos reservados.</div>'
        '</div></body></html>'
    ).format(site=html.escape(site), oid=order.id, rows=rows, year=timezone.now().year)
    return {'subject': subject, 'text': text, 'html': html_body}


def send_new_order_email(order, paid):
    """Envia o e-mail de novo pedido para o dono da loja (SystemSetting orderNotifyTo)."""
    to = SystemSetting.get('orderNotifyTo', '').strip()
    if not to:
        return False
    mail = new_order_email(order, paid)
    return _smtp_send(to, mail['subject'], mail['text'], mail['html'])


def send_order_email(order):
    """Envia o e-mail de conclusão ao cliente do pedido via SMTP."""
    customer = getattr(order, 'customer', None)
    to = (customer.email if customer else '').strip()
    if not to:
        return False
    mail = completed_order_email(order)
    return _smtp_send(to, mail['subject'], mail['text'], mail['html'])

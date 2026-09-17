"""Busca a resposta completa (login + senha) no painel web de qualquer API.

Alguns provedores (ex.: RITUNLOCKER) devolvem no status apenas o login, sem a
senha; a senha aparece somente na pagina do pedido no painel web. Este modulo
loga no painel configurado na propria API (``panel_url`` / ``panel_user`` /
``panel_pass``) e extrai o texto da resposta, devolvendo no mesmo formato que
o site usa (``Username: ...<br>Password: ...``) para que o parser
``notify._split_creds`` reconheca usuario e senha. Vale para qualquer API: se
os campos do painel estiverem preenchidos, a busca e feita automaticamente
sempre que a API concluir um pedido sem devolver a senha.

Formato de ``panel_url``:
- URL base do painel (ex.: ``https://ritunlocker.com``): o login fica em
  ``/login`` e o pedido em ``/account/orders/{trx}``.
- Template da pagina do pedido com ``{trx}`` (ex.:
  ``https://painel.com/orders/{trx}``): login continua sendo ``/login`` no
  mesmo host.

As credenciais do painel sao separadas das credenciais da API e nunca vao para
o cliente.
"""

import html as _html
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .models import SystemSetting

# Sessao (cookies) reaproveitada entre execucoes; o login e refeito quando
# expira.
SESSION_TTL = 15 * 60
# Nao reconsultar o painel para o mesmo pedido antes deste intervalo.
FETCH_TTL = 30 * 60

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

_USER_LABEL_RE = re.compile(
    r'(?:usuario|username|user|login|email|mail)\s*[:\-=]\s*(.+)$', re.I)
_PASS_LABEL_RE = re.compile(
    r'(?:senha|password|pass|passwd|pwd)\s*[:\-=]\s*(.+)$', re.I)

# Blocos onde os paineis costumam guardar a resposta do pedido.
_REPLY_CONTAINER_RES = (
    re.compile(r'id="orderReplyContent"[^>]*>(.*?)</div>', re.S | re.I),
    re.compile(r'id="adminNoteText"[^>]*>(.*?)</div>', re.S | re.I),
    re.compile(r'class="[^"]*(?:order-reply|reply-text|note-content)[^"]*"[^>]*>(.*?)</div>',
               re.S | re.I),
)


def _configured(api):
    return bool((getattr(api, 'panel_url', '') or '').strip()
                and (getattr(api, 'panel_user', '') or '').strip()
                and (getattr(api, 'panel_pass', '') or ''))


def _login_url(panel_url):
    panel_url = (panel_url or '').strip()
    if '{' in panel_url:
        parts = urllib.parse.urlsplit(panel_url)
        return '{}://{}'.format(parts.scheme, parts.netloc).rstrip('/') + '/login'
    return panel_url.rstrip('/') + '/login'


def _order_url(panel_url, trx_id):
    panel_url = (panel_url or '').strip()
    trx = urllib.parse.quote(str(trx_id or '').strip())
    if '{' in panel_url:
        return (panel_url.replace('{trx}', trx).replace('{id}', trx)
                .replace('{order}', trx))
    return panel_url.rstrip('/') + '/account/orders/' + trx


def _new_cookiejar():
    return http.cookiejar.CookieJar()


def _opener_for(cookies=None):
    jar = _new_cookiejar()
    for c in cookies or []:
        try:
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=c['name'], value=c.get('value', ''),
                port=None, port_specified=False,
                domain=c.get('domain', ''), domain_specified=bool(c.get('domain')),
                domain_initial_dot=str(c.get('domain') or '').startswith('.'),
                path=c.get('path', '/'), path_specified=True,
                secure=bool(c.get('secure')), expires=c.get('expires'),
                discard=True, comment=None, comment_url=None, rest={},
                rfc2109=False))
        except Exception:
            continue
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _session_key(api):
    return 'panelSession_{}'.format(api.id)


def _fetch_key(api, order):
    return 'panelFetch_{}_{}'.format(api.id, order.id)


def _load_session_cookies(api):
    raw = SystemSetting.get(_session_key(api), '')
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if time.time() - float(data.get('ts') or 0) > SESSION_TTL:
        return []
    return data.get('cookies') or []


def _save_session_cookies(api, cookies):
    try:
        obj, _ = SystemSetting.objects.get_or_create(
            key=_session_key(api), defaults={'value': ''})
        obj.value = json.dumps({'ts': time.time(), 'cookies': cookies})
        obj.save()
    except Exception:
        pass


def _parse_login_form(page_html):
    """Le os campos do form de login para preencher de forma generica.

    Devolve (campos_ocultos, nomes) — o login funciona com paineis que usam
    ``username`` ou ``email`` e envia o token CSRF quando existir."""
    hidden = {}
    names = set()
    for tag in re.findall(r'<input\b[^>]*>', page_html, re.I):
        name_m = re.search(r'name="([^"]+)"', tag, re.I) or \
            re.search(r"name='([^']+)'", tag, re.I)
        if not name_m:
            continue
        name = name_m.group(1)
        names.add(name)
        type_m = re.search(r'type="([^"]+)"', tag, re.I)
        value_m = re.search(r'value="([^"]*)"', tag, re.I)
        is_hidden = (type_m and type_m.group(1).lower() == 'hidden') or \
            name.lower() in ('_token', 'csrf_token', '_csrf', 'csrfmiddlewaretoken')
        if is_hidden:
            hidden[name] = value_m.group(1) if value_m else ''
    return hidden, names


def _login(api):
    """Loga no painel da API e devolve os cookies da sessao autenticada.

    Retorna [] se os campos do painel nao estiverem configurados ou se o login
    falhar (pagina fora do ar, captcha, senha trocada etc.)."""
    if not _configured(api):
        return []
    user = (api.panel_user or '').strip()
    password = api.panel_pass or ''
    login_url = _login_url(api.panel_url)

    jar = _new_cookiejar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        login_html = opener.open(
            urllib.request.Request(login_url, headers=_HEADERS),
            timeout=30).read().decode('utf-8', 'replace')
    except Exception:
        return []

    hidden, names = _parse_login_form(login_html)
    user_field = next(
        (n for n in ('username', 'email', 'user', 'login', 'email_address') if n in names),
        'username')
    pass_field = next(
        (n for n in ('password', 'pass', 'senha', 'passwd') if n in names),
        'password')
    data = dict(hidden)
    data[user_field] = user
    data[pass_field] = password
    if 'remember' in names:
        data['remember'] = 'on'

    body = urllib.parse.urlencode(data).encode('utf-8')
    headers = dict(_HEADERS)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    headers['Referer'] = login_url
    try:
        opener.open(urllib.request.Request(
            login_url, data=body, headers=headers, method='POST'),
            timeout=30).read()
    except Exception:
        return []

    cookies = [
        {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path,
         'secure': bool(c.secure), 'expires': c.expires}
        for c in jar
    ]
    _save_session_cookies(api, cookies)
    return cookies


def _extract_reply(page_html):
    """Extrai a resposta do pedido no painel.

    Procura os blocos mais comuns (``#orderReplyContent``, ``#adminNoteText``,
    classes com "reply") e, como ultimo recurso, varre a pagina inteira atras
    de rotulos de usuario/senha. Devolve ``Username: ...<br>Password: ...``
    (ou so o usuario, se a senha ainda nao saiu). Retorna '' se nada achar."""
    block = ''
    for pattern in _REPLY_CONTAINER_RES:
        match = pattern.search(page_html)
        if match:
            block = match.group(1)
            break
    if not block and re.search(r'(?:password|senha)\s*[:\-=]', page_html, re.I):
        block = page_html
    if not block:
        return ''
    return _labels_to_reply(block)


def _labels_to_reply(raw_html):
    text = _html.unescape(raw_html)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    user = passw = None
    for line in text.replace('\r', '').split('\n'):
        line = ' '.join(line.split())
        if not line:
            continue
        if user is None:
            m = _USER_LABEL_RE.search(line)
            if m and m.group(1).strip():
                user = m.group(1).strip()
        if passw is None:
            m = _PASS_LABEL_RE.search(line)
            if m and m.group(1).strip():
                passw = m.group(1).strip()
    parts = []
    if user:
        parts.append('Username: {}'.format(user))
    if passw:
        parts.append('Password: {}'.format(passw))
    return '<br>'.join(parts)


def _fetch_page(api, trx_id, cookies):
    opener = _opener_for(cookies)
    req = urllib.request.Request(_order_url(api.panel_url, trx_id), headers=_HEADERS)
    try:
        return opener.open(req, timeout=30).read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        # Sessao expirada costuma responder 401/403 na pagina do pedido.
        if exc.code in (401, 403):
            return None
        return ''
    except Exception:
        return ''


def _throttled(api, order):
    raw = SystemSetting.get(_fetch_key(api, order), '')
    try:
        last = float(raw) if raw else 0
    except (TypeError, ValueError):
        last = 0
    return bool(last) and (time.time() - last) < FETCH_TTL


def _mark_fetch(api, order):
    try:
        obj, _ = SystemSetting.objects.get_or_create(
            key=_fetch_key(api, order), defaults={'value': ''})
        obj.value = str(time.time())
        obj.save()
    except Exception:
        pass


def order_reply(api, order, force=False):
    """Resposta completa (login + senha) do pedido no painel, ou ''.

    Faz no maximo uma consulta ao painel por pedido a cada ``FETCH_TTL``,
    salvo se ``force`` for True. Retorna '' quando o painel nao esta
    configurado para a API ou nao ha senha."""
    if api is None or order is None or not _configured(api):
        return ''
    trx_id = (getattr(order, 'trx_id', '') or '').strip()
    if not trx_id:
        return ''
    if not force and _throttled(api, order):
        return ''

    cookies = _load_session_cookies(api)
    if not cookies:
        cookies = _login(api)
        if not cookies:
            return ''

    page = _fetch_page(api, trx_id, cookies)
    if page is None:  # sessao caiu: reloga uma vez
        cookies = _login(api)
        if not cookies:
            return ''
        page = _fetch_page(api, trx_id, cookies)
    _mark_fetch(api, order)
    if not page:
        return ''
    return _extract_reply(page)

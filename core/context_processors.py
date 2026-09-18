import json

from django.conf import settings

from .models import (
    SystemSetting, Currency, Customer, ServiceList, CustomerOrder, Api,
    RemoteServiceList, User, Inventory,
)


THEME_BLOCK_KEYS = (
    ('banner', 'themeBlockBanner'),
    ('products', 'themeBlockProducts'),
    ('footer', 'themeBlockFooter'),
)


_DEFAULT_MARQUEE_TEXT = "\n".join([
    '⚡ Ativação automática e entrega de logins em minutos',
    '🔐 Ferramentas GSM: AMT, UnlockTool, iRemoval e mais',
    '💳 Pagamento via PIX com confirmação automática 24/7',
    '🛡️ Suporte 24/7 para resolver qualquer dúvida rapidamente',
    '🚀 Servidor estável e seguro para você trabalhar',
])


def _load_theme_blocks():
    blocks = {}
    for key, setting_key in THEME_BLOCK_KEYS:
        raw = SystemSetting.get(setting_key, '{}')
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        blocks[key] = {
            'enabled': _bool(data.get('enabled')),
            'template_type': data.get('template_type', 'default'),
            'note': data.get('note', ''),
            'html_override': data.get('html_override', ''),
        }
    return blocks


PAGE_EFFECTS = (
    {'key': 'pageEffectSnow', 'name': 'Neve de Natal', 'icon': '❄️',
     'desc': 'Neve caindo suavemente sobre toda a página.'},
    {'key': 'pageEffectMatrix', 'name': 'Matrix', 'icon': '💻',
     'desc': 'Chuva digital verde no estilo Matrix.'},
    {'key': 'pageEffectConfetti', 'name': 'Confete', 'icon': '🎉',
     'desc': 'Confetes coloridos caindo pela página.'},
    {'key': 'pageEffectHearts', 'name': 'Corações', 'icon': '❤️',
     'desc': 'Corações flutuando para cima.'},
    {'key': 'pageEffectFireworks', 'name': 'Fogos de Artifício', 'icon': '🎆',
     'desc': 'Fogos explodindo em cores pelo céu da página.'},
    {'key': 'pageEffectStars', 'name': 'Estrelas Cadentes', 'icon': '🌠',
     'desc': 'Estrelas caindo com rastro luminoso.'},
    {'key': 'pageEffectPetals', 'name': 'Pétalas de Flores', 'icon': '🌸',
     'desc': 'Pétalas rosas caindo suavemente.'},
    {'key': 'pageEffectBubbles', 'name': 'Bolhas', 'icon': '🫧',
     'desc': 'Bolhas de sabão subindo pela tela.'},
    {'key': 'pageEffectRain', 'name': 'Chuva', 'icon': '🌧️',
     'desc': 'Chuva fina caindo sobre a página.'},
    {'key': 'pageEffectSparkles', 'name': 'Brilhos', 'icon': '✨',
     'desc': 'Pequenos brilhos dourados piscando pela página.'},
    {'key': 'pageEffectLights', 'name': 'Luzes de Natal', 'icon': '🎄',
     'desc': 'Pisca-pisca colorido no topo da página.'},
    {'key': 'pageEffectStarField', 'name': 'Estrelas Brilhando', 'icon': '⭐',
     'desc': 'Estrelas cintilantes na parte superior.'},
)


COLOR_PRESETS = (
    {'key': 'roxo', 'name': 'Roxo (padrão)', 'primary': '#673ab7', 'secondary': '#512da8'},
    {'key': 'azul', 'name': 'Azul Royal', 'primary': '#1e5eff', 'secondary': '#0f3fd0'},
    {'key': 'verde', 'name': 'Verde Esmeralda', 'primary': '#0f9d6b', 'secondary': '#067a50'},
    {'key': 'teal', 'name': 'Teal', 'primary': '#0097a7', 'secondary': '#00616e'},
    {'key': 'vermelho', 'name': 'Vermelho Vinho', 'primary': '#c62828', 'secondary': '#8e1515'},
    {'key': 'rosa', 'name': 'Rosa Neon', 'primary': '#e91e8c', 'secondary': '#c0166f'},
    {'key': 'laranja', 'name': 'Laranja', 'primary': '#f57c00', 'secondary': '#c25e00'},
    {'key': 'dourado', 'name': 'Dourado', 'primary': '#b08d13', 'secondary': '#8a6d0a'},
    {'key': 'blackgold', 'name': 'Preto & Dourado', 'primary': '#d4af37', 'secondary': '#a67c00'},
    {'key': 'grafite', 'name': 'Grafite', 'primary': '#3b4252', 'secondary': '#262b36'},
)

FONT_OPTIONS = (
    {'key': '', 'name': 'Padrão (DM Sans)', 'family': 'DM Sans, sans-serif', 'url': ''},
    {'key': 'Inter', 'name': 'Inter', 'family': "'Inter', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap'},
    {'key': 'Poppins', 'name': 'Poppins', 'family': "'Poppins', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap'},
    {'key': 'Nunito', 'name': 'Nunito', 'family': "'Nunito', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Nunito:wght@300;400;500;600;700;800&display=swap'},
    {'key': 'Roboto', 'name': 'Roboto', 'family': "'Roboto', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;600;700&display=swap'},
    {'key': 'Montserrat', 'name': 'Montserrat', 'family': "'Montserrat', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700;800&display=swap'},
    {'key': 'Lato', 'name': 'Lato', 'family': "'Lato', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap'},
    {'key': 'Open Sans', 'name': 'Open Sans', 'family': "'Open Sans', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Open+Sans:wght@300;400;500;600;700;800&display=swap'},
    {'key': 'Raleway', 'name': 'Raleway', 'family': "'Raleway', 'DM Sans', sans-serif",
     'url': 'https://fonts.googleapis.com/css2?family=Raleway:wght@300;400;500;600;700;800&display=swap'},
)


TEXT_COLOR_PRESETS = (
    {'key': '', 'name': 'Padrão (automático)', 'value': ''},
    {'key': 'branca', 'name': 'Branca', 'value': '#ffffff'},
    {'key': 'suave', 'name': 'Branca suave', 'value': '#e6edf3'},
    {'key': 'escura', 'name': 'Escura', 'value': '#111827'},
    {'key': 'cinza', 'name': 'Cinza escuro', 'value': '#374151'},
    {'key': 'dourada', 'name': 'Dourada', 'value': '#d4af37'},
)


def _hex_rgb(hexcolor):
    h = (hexcolor or '').strip().lstrip('#')
    if len(h) != 6:
        return '103, 58, 183'
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return '103, 58, 183'
    return '{}, {}, {}'.format(r, g, b)


def _active_page_effects():
    return [e['key'] for e in PAGE_EFFECTS if _bool(SystemSetting.get(e['key'], 'off'))]


PAGE_EFFECT_SPEEDS = ('very_slow', 'slow', 'normal', 'fast')


def _page_effect_speed():
    speed = str(SystemSetting.get('pageEffectSpeed', 'normal')).strip().lower()
    if speed not in PAGE_EFFECT_SPEEDS:
        speed = 'normal'
    return speed


def _bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _wa_url(raw):
    """Normaliza siteWhatsappUrl: numero puro vira https://wa.me/<digits> (evita link relativo 404)."""
    raw = str(raw or '').strip()
    if not raw:
        return ''
    if raw.startswith('http://') or raw.startswith('https://') or raw.startswith('wa.me/') or raw.startswith('api.whatsapp.com'):
        return raw
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if digits:
        return 'https://wa.me/' + digits
    return raw


def _parse_wa_contacts(raw):
    """Formato via SystemSetting: 'Nome|5511999999999,Nome2|5521999999998'."""
    contacts = []
    for part in str(raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        if '|' in part:
            name, _, number = part.rpartition('|')
        else:
            name, number = '', part
        digits = ''.join(ch for ch in number if ch.isdigit())
        if digits and digits not in [c['number'] for c in contacts]:
            contacts.append({'name': name.strip(), 'number': digits})
    return contacts


def _site_css():
    color = SystemSetting.get('themeColor', 'roxo')
    preset = next((p for p in COLOR_PRESETS if p['key'] == color), COLOR_PRESETS[0])
    font_key = SystemSetting.get('themeFont', '')
    font = next((f for f in FONT_OPTIONS if f['key'] == font_key), FONT_OPTIONS[0])
    text_key = SystemSetting.get('themeTextColor', '')
    text_preset = next((t for t in TEXT_COLOR_PRESETS if t['key'] == text_key), TEXT_COLOR_PRESETS[0])
    return {
        'primary': preset['primary'],
        'secondary': preset['secondary'],
        'rgb': _hex_rgb(preset['primary']),
        'font_family': font['family'],
        'font_url': font.get('url') or '',
        'text_color': text_preset['value'],
    }


def site_context(request):
    site_title = SystemSetting.get('siteTitle', 'SERVICETOOL')
    currencies = list(Currency.objects.filter(status='Active').values('id', 'code', 'icon', 'name', 'rate', 'created_at'))

    customer = None
    customer_id = request.session.get('customer_id')
    if customer_id:
        customer = Customer.objects.filter(id=customer_id).first()
        if customer:
            token = request.session.get('customer_session_token', '')
            if customer.session_token and token != customer.session_token:
                customer = None
                request.session.pop('customer_id', None)
                request.session.pop('customer_session_token', None)
            else:
                try:
                    customer.touch_activity()
                except Exception:  # noqa: BLE001 - presenca nao deve quebrar a pagina
                    pass

    them_mode = SystemSetting.get('themeMode', 'dark')
    theme_color = SystemSetting.get('themeColor', 'roxo')
    if them_mode != 'dark':
        them_mode = 'light'

    return {
        'siteTitle': site_title,
        'siteLogo': SystemSetting.get('siteLogo', '/static/resource/logo.png'),
        'siteFav': SystemSetting.get('siteFav', '/static/resource/fav.png'),
        'siteWaUrl': _wa_url(SystemSetting.get('siteWhatsappUrl', '')),
        'siteWaNumber': SystemSetting.get('siteWhatsappNumber', ''),
        'siteWaContacts': _parse_wa_contacts(SystemSetting.get('siteWhatsappNumber', '')),
        'siteTeleUrl': SystemSetting.get('siteTelegramUrl', ''),
        'siteFbUrl': SystemSetting.get('siteFacebookUrl', ''),
        'siteXUrl': SystemSetting.get('siteTwitterUrl', ''),
        'siteYtUrl': SystemSetting.get('siteYoutubeUrl', ''),
        'siteEmail': SystemSetting.get('siteEmailAddress', ''),
        'sitePhone': SystemSetting.get('sitePhoneNumber', ''),
        'siteAddress': SystemSetting.get('siteAddress', ''),
        'headerCode': SystemSetting.get('headerCode', ''),
        'siteMarqueeText': SystemSetting.get('siteMarqueeText', _DEFAULT_MARQUEE_TEXT),
        'siteMarqueeLines': [line.strip() for line in SystemSetting.get('siteMarqueeText', _DEFAULT_MARQUEE_TEXT).splitlines() if line.strip()],
        'site_meta_des': SystemSetting.get('siteMetaDes', ''),
        'site_keyword': SystemSetting.get('siteKeyword', ''),
        'site_meta_title': SystemSetting.get('siteMetaTitle', ''),
        'currency_icon': 'R$',
        'themMode': them_mode,
        'themeColor': theme_color,
        'theme_font': SystemSetting.get('themeFont', ''),
        'site_css': _site_css(),
        'theme_blocks': _load_theme_blocks(),
        'page_effects': _active_page_effects(),
        'page_effect_speed': _page_effect_speed(),
        'currencies_list': currencies,
        'currency_code': 'BRL',
        'site_url': getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
        'customer': customer,
        'currencyList': currencies,
    }


def admin_context(request):
    if not request.user.is_authenticated:
        return {}
    return {
        'serverServiceCount': ServiceList.objects.filter(service_type='Server Service').count(),
        'creditServiceCount': ServiceList.objects.filter(service_type='Credit Service').count(),
        'activationServiceCount': ServiceList.objects.filter(service_type='Activation Service').count(),
        'imeiServiceCount': ServiceList.objects.filter(service_type='IMEI Service').count(),
        'methodServiceCount': ServiceList.objects.filter(service_type='Method Service').count(),
        'waitingActionCount': CustomerOrder.objects.filter(service_status='Waiting Action').count(),
        'inProcessCount': CustomerOrder.objects.filter(service_status='In Process').count(),
        'successCount': CustomerOrder.objects.filter(service_status='Success').count(),
        'rejectedCount': CustomerOrder.objects.filter(service_status='Rejected').count(),
        'coustomerCount': Customer.objects.count(),
        'userCount': User.objects.filter(is_staff=True).count(),
        'apiCount': Api.objects.count(),
        'inventoryCount': Inventory.objects.count(),
        'apis': list(Api.objects.all().order_by('-id')),
        'buildinApiCount': RemoteServiceList.objects.count(),
        'is_admin_panel': True,
        'maintenance_on': _bool(SystemSetting.get('siteMaintenanceMode', 'off')),
    }

from django.conf import settings

from .models import (
    SystemSetting, Currency, Customer, ServiceList, CustomerOrder, Api,
    RemoteServiceList, User, Inventory,
)


def _bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


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

    them_mode = SystemSetting.get('themeMode', 'dark')
    theme_color = SystemSetting.get('themeColor', 'preset-1')
    if them_mode != 'dark':
        them_mode = 'light'

    return {
        'siteTitle': site_title,
        'siteLogo': SystemSetting.get('siteLogo', '/static/resource/logo.png'),
        'siteFav': SystemSetting.get('siteFav', '/static/resource/fav.png'),
        'siteWaUrl': SystemSetting.get('siteWhatsappUrl', ''),
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
        'site_meta_des': SystemSetting.get('siteMetaDes', ''),
        'site_keyword': SystemSetting.get('siteKeyword', ''),
        'site_meta_title': SystemSetting.get('siteMetaTitle', ''),
        'currency_icon': 'R$',
        'themMode': them_mode,
        'themeColor': theme_color,
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

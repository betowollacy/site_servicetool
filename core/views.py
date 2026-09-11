import json
import random
import string
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import asaas, binance, provider_api, public_api
from .models import (
    Api, ApiLog, CREDIT_SERVICE_EXTRA_FIELDS, Currency, Customer, CustomerOrder,
    GatewayLog, Invoice, OrderInput, Page, PaymentDeposit, PaymentGateway,
    ServiceGroup, ServiceInput, ServiceList, Slider, Statement, SystemSetting,
)

CATEGORY_SLUGS = {
    'server-service': {
        'type': 'Server Service',
        'display': 'Aluguel',
        'title': 'Aluguel',
    },
    'credit-service': {
        'type': 'Credit Service',
        'display': 'Créditos',
        'title': 'Ativações e Créditos',
    },
    'imei-service': {
        'type': 'IMEI Service',
        'display': 'IMEI',
        'title': 'IMEI/SN Service',
    },
}

GROUP_CATEGORY_MAP = {
    'remote': 'server-service',
    'server': 'credit-service',
    'imei': 'imei-service',
}


def _get_customer(request):
    customer_id = request.session.get('customer_id')
    if not customer_id:
        return None
    return Customer.objects.filter(id=customer_id).first()


def _service_dict(service):
    display = CATEGORY_SLUGS.get(_slug_for_type(service.service_type), {})
    return {
        'id': service.id,
        'slug': service.slug,
        'title': service.title,
        'thumbnail': service.thumbnail,
        'price': service.original_price,
        'delivery_time': service.delivery_time,
        'service_type': service.service_type,
        'service_type_display': display.get('display', ''),
        'category_slug': _slug_for_type(service.service_type),
    }


def _slug_for_type(service_type):
    for slug, cfg in CATEGORY_SLUGS.items():
        if cfg['type'] == service_type:
            return slug
    return 'server-service'


def _active_services():
    return ServiceList.objects.filter(status='Active')


def _service_tags(service):
    return service.tags_list


def _base_ctx(request):
    groups = []
    for g in ServiceGroup.objects.filter(status='Active'):
        cat_slug = GROUP_CATEGORY_MAP.get(g.slug)
        if cat_slug:
            groups.append({'slug': g.slug, 'name': g.name, 'cat_slug': cat_slug})
    return {
        'currency_icon': 'R$',
        'sliders': Slider.objects.filter(status='Active').order_by('id'),
        'groups': groups,
        'activeGateway': PaymentGateway.objects.filter(name__iexact='Asaas', status='Active'),
    }


def homepage(request):
    hot_services = _active_services().filter(recommended=1).order_by('-sells')
    trending_services = _active_services().order_by('-sells')
    recent_services = _active_services().order_by('-created_at')
    carousel = list(_active_services().filter(home_carousel=True).order_by('-sells'))
    carousel_groups = [carousel[i:i + 3] for i in range(0, len(carousel), 3)]
    ctx = {
        'hot_services': [_service_dict(s) for s in hot_services],
        'trending_services': [_service_dict(s) for s in trending_services],
        'recent_services': [_service_dict(s) for s in recent_services],
        'recent_top': [_service_dict(s) for s in recent_services[:4]],
        'carousel_groups': [[_service_dict(s) for s in group] for group in carousel_groups],
        'has_carousel': bool(carousel),
        'total_services': ServiceList.objects.filter(status='Active').count(),
        'total_customers': Customer.objects.count(),
    }
    ctx.update(_base_ctx(request))
    return render(request, 'frontend/homepage.html', ctx)


def category(request, slug):
    cfg = CATEGORY_SLUGS.get(slug)
    if not cfg:
        return redirect('homepage')
    services = _active_services().filter(service_type=cfg['type']).order_by('-created_at')
    ctx = {
        'category_title': cfg['title'],
        'services': [_service_dict(s) for s in services],
    }
    ctx.update(_base_ctx(request))
    return render(request, 'frontend/category.html', ctx)


def _service_input_fields(service):
    """Campos de entrada do servico. Para Credit Service garante Email e Password."""
    names = list(service.service_fields.values_list('name', flat=True))
    if service.service_type == 'Credit Service':
        for extra in CREDIT_SERVICE_EXTRA_FIELDS:
            if extra not in names:
                names.append(extra)
    return names


def _service_input_objects(service):
    """Objetos de entrada usados no template (ServiceInput + extras sinteticos)."""
    names = _service_input_fields(service)
    db = {si.name: si for si in service.service_fields.all()}
    return [db.get(name, SimpleNamespace(name=name)) for name in names]


def server_view(request, slug):
    service = get_object_or_404(ServiceList, slug=slug)
    if service.status != 'Active':
        return redirect('homepage')
    tags = _service_tags(service)
    ctx = {
        'serviceData': service,
        'serviceInputs': _service_input_objects(service),
        'serviceTags': tags,
        'Price': service.original_price,
        'keyWord': ','.join(t for t in [service.kw1, service.kw2, service.kw3, service.kw4, service.kw5] if t),
        'item_catslug': _slug_for_type(service.service_type),
        'hot_services': [_service_dict(s) for s in _active_services().filter(recommended=1).order_by('-sells')[:10]],
        'trending_services': [_service_dict(s) for s in _active_services().order_by('-sells')[:10]],
        'recent_services': [_service_dict(s) for s in _active_services().order_by('-created_at')[:10]],
    }
    ctx.update(_base_ctx(request))
    return render(request, 'frontend/server_view.html', ctx)


def change_theme_mode(request):
    return redirect('homepage')


def page_view(request, slug):
    page = get_object_or_404(Page, page_slug=slug)
    ctx = {'page': page}
    ctx.update(_base_ctx(request))
    return render(request, 'frontend/page.html', ctx)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        customer = Customer.objects.filter(email__iexact=email).first()
        if customer and customer.check_password(password) and customer.status == 'Active':
            request.session['customer_id'] = customer.id
            if request.POST.get('remember_login') == 'on':
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                request.session.set_expiry(0)
            return redirect('homepage')
        ctx = {'login_error': 'E-mail ou senha inválidos.'}
        ctx.update(_base_ctx(request))
        return render(request, 'frontend/homepage.html', ctx)
    return redirect('homepage')


def register_view(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        mobile = request.POST.get('mobile', '').strip()
        password = request.POST.get('password', '')
        cpf_cnpj = request.POST.get('cpf_cnpj', '').strip()
        currency = 'BRL'
        if Customer.objects.filter(email__iexact=email).exists():
            ctx = {'register_error': 'E-mail já cadastrado.'}
            ctx.update(_base_ctx(request))
            return render(request, 'frontend/homepage.html', ctx)
        if not (name and email and mobile and password):
            ctx = {'register_error': 'Todos os campos são obrigatórios.'}
            ctx.update(_base_ctx(request))
            return render(request, 'frontend/homepage.html', ctx)
        Customer.objects.create(
            name=name, email=email, mobile=mobile, cpf_cnpj=cpf_cnpj or None,
            password=Customer.make_password(password), currency=currency,
        )
        messages.success(request, 'Cadastro realizado com sucesso. Faça login.')
        return redirect('homepage')
    return redirect('homepage')


def logout_view(request):
    request.session.pop('customer_id', None)
    return redirect('homepage')


# --------------------------------------------------------------------------- #
# Forgot Password
# --------------------------------------------------------------------------- #

def _generate_code():
    return ''.join(random.choices(string.digits, k=6))


FORGOT_SESSION_KEY = 'forgot_password'


def forgot_password(request):
    step = request.session.get(FORGOT_SESSION_KEY + '_step', 'request')
    code = request.session.get(FORGOT_SESSION_KEY + '_code', '')
    email = request.session.get(FORGOT_SESSION_KEY + '_email', '')
    masked_email = ''
    if email:
        parts = email.split('@')
        if len(parts) == 2 and len(parts[0]) > 2:
            masked_email = parts[0][:2] + '***' + '@' + parts[1]
        else:
            masked_email = email

    if request.method == 'POST':
        action = request.POST.get('action', 'request')

        if action == 'request' or step == 'request':
            email_input = request.POST.get('email', '').strip()
            customer = Customer.objects.filter(email__iexact=email_input).first()
            if not customer or customer.status != 'Active':
                messages.error(request, 'E-mail não encontrado ou conta inativa.')
                ctx = {'step': 'request'}
                ctx.update(_base_ctx(request))
                return render(request, 'customer/forgot_password.html', ctx)
            new_code = _generate_code()
            request.session[FORGOT_SESSION_KEY + '_code'] = new_code
            request.session[FORGOT_SESSION_KEY + '_email'] = email_input
            request.session[FORGOT_SESSION_KEY + '_step'] = 'verify'
            messages.success(request, f'Código enviado para {email_input}. (Código de teste: {new_code})')
            return redirect('forgot_password')

        elif action == 'verify' or step == 'verify':
            code_input = request.POST.get('code', '').strip()
            if code_input != code:
                messages.error(request, 'Código inválido.')
                masked_email = email
                ctx = {'step': 'verify', 'masked_email': masked_email}
                ctx.update(_base_ctx(request))
                return render(request, 'customer/forgot_password.html', ctx)
            request.session[FORGOT_SESSION_KEY + '_step'] = 'reset'
            return redirect('forgot_password')

        elif action == 'reset' or step == 'reset':
            new_password = request.POST.get('new_password', '')
            confirm_password = request.POST.get('confirm_password', '')
            if len(new_password) < 8:
                messages.error(request, 'A senha deve ter pelo menos 8 caracteres.')
                ctx = {'step': 'reset', 'code': code}
                ctx.update(_base_ctx(request))
                return render(request, 'customer/forgot_password.html', ctx)
            if new_password != confirm_password:
                messages.error(request, 'As senhas não coincidem.')
                ctx = {'step': 'reset', 'code': code}
                ctx.update(_base_ctx(request))
                return render(request, 'customer/forgot_password.html', ctx)
            customer = Customer.objects.filter(email__iexact=email).first()
            if customer:
                customer.password = Customer.make_password(new_password)
                customer.save(update_fields=['password'])
            for key in list(request.session.keys()):
                if key.startswith(FORGOT_SESSION_KEY):
                    del request.session[key]
            ctx = {'step': 'done'}
            ctx.update(_base_ctx(request))
            return render(request, 'customer/forgot_password.html', ctx)

    for key in list(request.session.keys()):
        if key.startswith(FORGOT_SESSION_KEY) and step not in ('verify', 'reset'):
            del request.session[key]
            step = 'request'

    if step == 'verify':
        ctx = {'step': 'verify', 'masked_email': masked_email}
    elif step == 'reset':
        ctx = {'step': 'reset', 'code': code}
    else:
        ctx = {'step': 'request'}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/forgot_password.html', ctx)


# --------------------------------------------------------------------------- #
# Customer
# --------------------------------------------------------------------------- #

def _require_customer(view):
    def wrapper(request, *args, **kwargs):
        customer = _get_customer(request)
        if not customer:
            return redirect('homepage')
        return view(request, customer, *args, **kwargs)
    return wrapper


@_require_customer
def customer_dashboard(request, customer):
    orders = CustomerOrder.objects.filter(customer=customer)
    waiting_action = orders.filter(service_status='Waiting Action').count()
    in_process = orders.filter(service_status='In Process').count()
    completed_orders = orders.filter(service_status='Success').count()
    cancelled_orders = orders.filter(service_status='Rejected').count()
    status_chart = []
    status_colors = {
        'Success': 'green',
        'In Process': 'blue',
        'Waiting Action': 'amber',
        'Rejected': 'red',
    }
    status_labels = ('Success', 'In Process', 'Waiting Action', 'Rejected')
    status_counts = [orders.filter(service_status=status).count() for status in status_labels]
    status_max = max(status_counts or [0]) or 1
    for status, count in zip(status_labels, status_counts):
        status_chart.append({
            'label': status,
            'count': count,
            'percent': round(count * 100 / status_max),
            'color': status_colors[status],
        })
    ctx = {
        'waitingAction': waiting_action,
        'in_process': in_process,
        'completed_orders': completed_orders,
        'cancelled_orders': cancelled_orders,
        'total_orders': orders.count(),
        'total_spent': orders.aggregate(s=Sum('service_price'))['s'] or Decimal('0.00'),
        'balance': customer.balance,
        'latest_orders': orders[:5],
        'status_chart': status_chart,
    }
    ctx.update(_base_ctx(request))
    return render(request, 'customer/dashboard.html', ctx)


@_require_customer
def customer_profile(request, customer):
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'update_profile':
            name = request.POST.get('name', '').strip()
            mobile = request.POST.get('mobile', '').strip()
            cpf_cnpj = request.POST.get('cpf_cnpj', '').strip()
            if not name:
                messages.error(request, 'Nome é obrigatório.')
                return redirect('customer_profile')
            customer.name = name
            customer.mobile = mobile
            customer.cpf_cnpj = cpf_cnpj or None
            customer.save(update_fields=['name', 'mobile', 'cpf_cnpj'])
            messages.success(request, 'Perfil atualizado com sucesso.')
        elif action == 'change_password':
            current = request.POST.get('current_password', '')
            new_pass = request.POST.get('new_password', '')
            confirm = request.POST.get('confirm_password', '')
            if not customer.check_password(current):
                messages.error(request, 'Senha atual incorreta.')
                return redirect('customer_profile')
            if len(new_pass) < 8:
                messages.error(request, 'A nova senha deve ter pelo menos 8 caracteres.')
                return redirect('customer_profile')
            if new_pass != confirm:
                messages.error(request, 'As senhas não coincidem.')
                return redirect('customer_profile')
            customer.password = Customer.make_password(new_pass)
            customer.save(update_fields=['password'])
            messages.success(request, 'Senha alterada com sucesso.')
        elif action == 'generate_api_key':
            customer.api_key = public_api.generate_api_key()
            customer.api_allow = 'on'
            customer.api_ip = request.POST.get('api_ip', '').strip() or None
            customer.save(update_fields=['api_key', 'api_allow', 'api_ip'])
            messages.success(request, 'Chave de API gerada com sucesso.')
        elif action == 'disable_api':
            customer.api_allow = ''
            customer.save(update_fields=['api_allow'])
            messages.success(request, 'Acesso à API desabilitado.')
        elif action == 'enable_api':
            if not customer.api_key:
                customer.api_key = public_api.generate_api_key()
            customer.api_allow = 'on'
            customer.api_ip = request.POST.get('api_ip', '').strip() or None
            customer.save(update_fields=['api_key', 'api_allow', 'api_ip'])
            messages.success(request, 'Acesso à API habilitado.')
        return redirect('customer_profile')
    ctx = {'api_url': request.build_absolute_uri(reverse('public_api'))}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/profile.html', ctx)


@_require_customer
def customer_order_history(request, customer):
    orders = CustomerOrder.objects.filter(customer=customer)
    ctx = {'orders': orders}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/order_history.html', ctx)


@_require_customer
def customer_statement(request, customer):
    statements = Statement.objects.filter(customer=customer)
    ctx = {'statements': statements}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/statement.html', ctx)


@_require_customer
def customer_invoice_list(request, customer):
    invoices = Invoice.objects.filter(customer=customer)
    ctx = {'invoices': invoices}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/invoice_list.html', ctx)


@_require_customer
def customer_invoice_detail(request, customer, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, customer=customer)
    ctx = {'invoice': invoice}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/invoice.html', ctx)


@_require_customer
def customer_add_balance(request, customer):
    currency = Currency.objects.filter(code=customer.currency).first() or Currency.objects.first()
    ctx = {
        'add_amount': SystemSetting.get('addAmount', '100'),
        'currency': currency,
        'Customer': customer,
    }
    ctx.update(_base_ctx(request))
    return render(request, 'customer/add_balance.html', ctx)


@_require_customer
def customer_deposit(request, customer):
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('Amount', '0') or '0')
        except Exception:
            amount = Decimal('0')
        gateway_name = request.POST.get('payment_methode', '')
        if amount <= 0:
            messages.error(request, 'Enter a valid amount.')
            return redirect('customer_add_balance')
        currency = Currency.objects.filter(code=customer.currency).first() or Currency.objects.first()
        invoice = Invoice.objects.create(
            customer=customer,
            customer_name=customer.name,
            invoice_for='Deposit',
            invoice_amount=amount,
            customer_currency=customer.currency,
            payment_gateway=gateway_name,
            invoice_title='Adicionar Saldo',
            invoice_status='Unpaid',
            customer_mobile=customer.mobile,
            customer_email=customer.email,
        )
        return redirect('checkout', invoice_id=invoice.id)
    return redirect('customer_add_balance')


def _debit_and_forward_order(order, customer):
    """Debita o saldo do cliente e envia o pedido ao provedor.

    Se o envio falhar, estorna o valor e marca o pedido como Rejected."""
    price = order.service_price
    if customer.balance < price:
        return
    customer.balance = customer.balance - price
    customer.save(update_fields=['balance'])
    order.service_status = 'In Process'
    order.save(update_fields=['service_status'])
    Statement.objects.create(
        customer=customer, description=f"Order #{order.id} - {order.service_title}",
        type='Debit', amount=price, balance=customer.balance, order=order,
    )
    forwarded, msg = provider_api.submit_local_order(order)
    if forwarded is False:
        provider_api.refund_order(order, msg or 'Falha ao enviar para o provedor.')


@_require_customer
def submit_order(request, customer):
    if request.method != 'POST':
        return redirect('homepage')
    service_id = request.POST.get('serviceID')
    service = get_object_or_404(ServiceList, id=service_id)
    if service.status != 'Active':
        messages.error(request, 'Service unavailable.')
        return redirect('homepage')

    order = CustomerOrder.objects.create(
        customer=customer,
        service=service,
        service_status='Waiting Action',
        service_type=_invoice_type_key(service.service_type),
        service_price=service.original_price,
        service_title=service.title,
        seen='false',
    )
    for field_name in _service_input_fields(service):
        if 'quantidade' in field_name.lower() or field_name.lower().startswith(('qtd', 'qty', 'qnt')):
            try:
                order.service_qnt = int(str(request.POST.get(field_name, '1') or '1').strip())
            except (TypeError, ValueError):
                order.service_qnt = 1
            order.save(update_fields=['service_qnt'])
            continue
        value = request.POST.get(field_name, '').strip()
        OrderInput.objects.create(order=order, field_name=field_name, field_value=value)
        if not order.service_input1:
            order.service_input1 = value
            order.save(update_fields=['service_input1'])

    price = service.original_price
    if customer.balance >= price:
        _debit_and_forward_order(order, customer)
        messages.success(request, 'Pedido realizado com sucesso.')
        return redirect('customer_order_history')
    else:
        invoice = Invoice.objects.create(
            customer=customer,
            customer_name=customer.name,
            invoice_for='Order',
            invoice_amount=price,
            customer_currency=customer.currency,
            invoice_title=service.title,
            invoice_status='Unpaid',
            customer_mobile=customer.mobile,
            customer_email=customer.email,
            order=order,
        )
        return redirect('checkout', invoice_id=invoice.id)


def _invoice_type_key(service_type):
    if service_type == 'Credit Service':
        return 'credit_service'
    if service_type == 'IMEI Service':
        return 'imei_service'
    return 'server_service'


@_require_customer
def checkout(request, customer, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, customer=customer)
    currency = Currency.objects.filter(code=customer.currency).first() or Currency.objects.first()
    ctx = {
        'invoice': invoice,
        'currency': currency,
        'activeGateway': PaymentGateway.objects.filter(name__iexact='Asaas', status='Active'),
    }
    ctx.update(_base_ctx(request))
    return render(request, 'customer/checkout.html', ctx)


@_require_customer
def gateway_pay(request, customer, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, customer=customer)
    if request.method == 'POST':
        gateway_name = request.POST.get('payment_methode', '').strip()
        if gateway_name.lower() == 'asaas':
            return _pay_with_asaas(request, customer, invoice)
        messages.error(request, 'Selecione o pagamento via PIX com Asaas.')
        return redirect('checkout', invoice_id=invoice.id)
    return redirect('checkout', invoice_id=invoice.id)


def _pay_with_asaas(request, customer, invoice):
    gateway = PaymentGateway.objects.filter(name__iexact='Asaas', status='Active').first()
    cpf_cnpj = request.POST.get('cpf_cnpj', '').strip() if request.method == 'POST' else ''
    if cpf_cnpj:
        only_digits = ''.join(ch for ch in cpf_cnpj if ch.isdigit())
        if only_digits and not customer.cpf_cnpj:
            customer.cpf_cnpj = only_digits
            customer.save(update_fields=['cpf_cnpj'])
    if not gateway or not (gateway.asaas_api_key or '').strip():
        messages.error(request, 'Gateway Asaas nao configurado. Adicione a chave de API no painel.')
        return redirect('checkout', invoice_id=invoice.id)
    if not (''.join(ch for ch in (customer.cpf_cnpj or '') if ch.isdigit())):
        messages.error(request, 'Informe seu CPF ou CNPJ para gerar o PIX.')
        return redirect('checkout', invoice_id=invoice.id)
    try:
        payment = asaas.create_pix_payment(invoice, gateway)
    except Exception as exc:
        GatewayLog.objects.create(
            payment_gateway='Asaas', payment_for=f'Invoice #{invoice.id}',
            payment_amount=invoice.invoice_amount, customer=customer,
            customer_name=customer.name, invoice=invoice, invoice_status='Unpaid',
            create_payment=str(exc)[:1000],
        )
        messages.error(request, 'Falha ao gerar pagamento no Asaas. Tente novamente.')
        return redirect('checkout', invoice_id=invoice.id)
    encoded_image = payment.get('encodedImage') or payment.get('pixQrCode') or ''
    if encoded_image and not encoded_image.startswith('http') and not encoded_image.startswith('data:'):
        encoded_image = 'data:image/png;base64,' + encoded_image
    pix_payload = payment.get('payload') or payment.get('pixCopyPaste') or ''
    PaymentDeposit.objects.create(
        name='Asaas - PIX',
        gateway_amount=invoice.invoice_amount,
        gateway_payment_id=payment.get('id') or '',
        qrcode_url=encoded_image,
        pix_code=pix_payload,
        checkout_url=payment.get('invoiceUrl') or '',
        gateway_note=pix_payload[:100],
        gateway_data=json.dumps(payment, ensure_ascii=False)[:4000],
        status='Pending',
        invoice=invoice,
    )
    invoice.payment_gateway = gateway.name
    invoice.save(update_fields=['payment_gateway'])
    return redirect('payment_page', invoice_id=invoice.id)


def _pay_with_binance(request, customer, invoice):
    gateway = PaymentGateway.objects.filter(name__iexact='Binance', status='Active').first()
    if not gateway or not (gateway.binance_private_key or '').strip():
        messages.error(request, 'Gateway Binance nao configurado. Adicione a chave da API no painel.')
        return redirect('checkout', invoice_id=invoice.id)
    try:
        data = binance.create_order(invoice, gateway)
    except Exception as exc:
        GatewayLog.objects.create(
            payment_gateway='Binance', payment_for=f'Invoice #{invoice.id}',
            payment_amount=invoice.invoice_amount, customer=customer,
            customer_name=customer.name, invoice=invoice, invoice_status='Unpaid',
            create_payment=str(exc)[:1000],
        )
        messages.error(request, 'Falha ao gerar pagamento na Binance. Tente novamente.')
        return redirect('checkout', invoice_id=invoice.id)
    PaymentDeposit.objects.create(
        name='Binance - USDT',
        gateway_amount=invoice.invoice_amount,
        gateway_payment_id=data.get('merchantTradeNo') or '',
        checkout_url=data.get('checkoutUrl') or '',
        gateway_note=data.get('prepayId') or '',
        gateway_data=json.dumps(data, ensure_ascii=False)[:4000],
        status='Pending',
        invoice=invoice,
    )
    invoice.payment_gateway = gateway.name
    invoice.save(update_fields=['payment_gateway'])
    return redirect('payment_page', invoice_id=invoice.id)


def _mark_paid(deposit, payload=None):
    if deposit.status == 'Paid':
        return
    invoice = deposit.invoice
    if not invoice:
        deposit.status = 'Paid'
        deposit.save(update_fields=['status'])
        return
    deposit.status = 'Paid'
    deposit.save(update_fields=['status'])
    if invoice.invoice_status != 'Paid':
        invoice.invoice_status = 'Paid'
        invoice.total_paid = invoice.invoice_amount
        invoice.payment_currency = invoice.customer.currency if invoice.customer else invoice.customer_currency
        invoice.save(update_fields=['invoice_status', 'total_paid', 'payment_currency'])
    customer = invoice.customer
    if customer:
        customer.balance = customer.balance + invoice.invoice_amount
        customer.save(update_fields=['balance'])
        Statement.objects.create(
            customer=customer,
            description=f"Invoice #{invoice.id} - pago via {invoice.payment_gateway or deposit.name}",
            type='Credit', amount=invoice.invoice_amount, balance=customer.balance,
        )
    GatewayLog.objects.create(
        payment_gateway=invoice.payment_gateway or deposit.name,
        payment_for=f'Invoice #{invoice.id}',
        payment_amount=invoice.invoice_amount,
        customer=customer,
        customer_name=customer.name if customer else '',
        invoice=invoice,
        invoice_status='Paid',
        create_payment=json.dumps(payload, ensure_ascii=False)[:4000] if payload else '',
    )
    if invoice.invoice_for == 'Order' and invoice.order_id and customer:
        _debit_and_forward_order(invoice.order, customer)


@_require_customer
def payment_page(request, customer, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, customer=customer)
    deposit = PaymentDeposit.objects.filter(invoice=invoice).order_by('-id').first()
    ctx = {'invoice': invoice, 'deposit': deposit}
    ctx.update(_base_ctx(request))
    return render(request, 'customer/payment.html', ctx)


@_require_customer
def payment_status(request, customer, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, customer=customer)
    deposit = PaymentDeposit.objects.filter(invoice=invoice).order_by('-id').first()
    if deposit and deposit.status != 'Paid':
        gateway_name = (invoice.payment_gateway or '').lower()
        if gateway_name == 'asaas':
            gateway = PaymentGateway.objects.filter(name__iexact='Asaas', status='Active').first()
            if gateway and (gateway.asaas_api_key or '').strip() and deposit.gateway_payment_id:
                try:
                    payment = asaas.get_payment(gateway, deposit.gateway_payment_id)
                    if payment.get('status') in ('CONFIRMED', 'RECEIVED'):
                        _mark_paid(deposit, payment)
                except Exception:
                    pass
        elif gateway_name == 'binance':
            gateway = PaymentGateway.objects.filter(name__iexact='Binance', status='Active').first()
            if gateway and (gateway.binance_private_key or '').strip() and deposit.gateway_payment_id:
                try:
                    row = binance.query_order(gateway, deposit.gateway_payment_id)
                    if row and row.get('tradeStatus') == 'SUCCESS':
                        _mark_paid(deposit, row)
                except Exception:
                    pass
        deposit.refresh_from_db()
    status = deposit.status if deposit else 'Pending'
    return JsonResponse({'status': status, 'paid': status == 'Paid'})


@csrf_exempt
@require_POST
def asaas_webhook(request):
    gateway = PaymentGateway.objects.filter(name__iexact='Asaas', status='Active').first()
    if not gateway:
        return HttpResponse('ok')
    token = request.headers.get('asaas_access_token', '')
    expected = (gateway.asaas_api_key or '').strip()
    if expected and token.strip() != expected:
        return HttpResponse('invalid token', status=401)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        payload = {}
    payment = payload.get('payment') if isinstance(payload.get('payment'), dict) else {}
    payment_id = payment.get('id') or ''
    if payment_id and payment.get('status') in ('CONFIRMED', 'RECEIVED'):
        deposit = PaymentDeposit.objects.filter(gateway_payment_id=payment_id).order_by('-id').first()
        if deposit:
            _mark_paid(deposit, payload)
    return HttpResponse('ok')


@csrf_exempt
@require_POST
def binance_webhook(request):
    gateway = PaymentGateway.objects.filter(name__iexact='Binance', status='Active').first()
    if not gateway:
        return HttpResponse('ok')
    signature = request.headers.get('BinancePay-Signature', '')
    payload_str = request.body.decode('utf-8')
    if not binance.verify_notification(gateway, signature, payload_str):
        return HttpResponse('verify failed', status=403)
    try:
        payload = json.loads(payload_str)
    except Exception:
        payload = {}
    biz = payload.get('data')
    if isinstance(biz, str):
        try:
            biz = json.loads(biz)
        except Exception:
            biz = {}
    if not isinstance(biz, dict):
        return HttpResponse('ok')
    merchant_trade_no = biz.get('merchantTradeNo') or ''
    biz_status = biz.get('bizStatus') or biz.get('tradeStatus') or ''
    if merchant_trade_no and biz_status == 'PAY_SUCCESS':
        deposit = PaymentDeposit.objects.filter(gateway_payment_id=merchant_trade_no).order_by('-id').first()
        if deposit:
            _mark_paid(deposit, payload)
    return HttpResponse('ok')

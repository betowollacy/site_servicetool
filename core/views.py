import json
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import asaas, binance
from .models import (
    Api, ApiLog, Currency, Customer, CustomerOrder, GatewayLog, Invoice,
    OrderInput, Page, PaymentDeposit, PaymentGateway, ServiceGroup, ServiceInput,
    ServiceList, Slider, Statement, SystemSetting,
)

CATEGORY_SLUGS = {
    'server-service': {
        'type': 'Server Service',
        'display': 'Server',
        'title': 'Ativações e Créditos',
    },
    'credit-service': {
        'type': 'Credit Service',
        'display': 'Credit',
        'title': 'Aluguel',
    },
    'imei-service': {
        'type': 'IMEI Service',
        'display': 'IMEI',
        'title': 'IMEI/SN Service',
    },
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
    return {
        'currency_icon': 'R$',
        'sliders': Slider.objects.filter(status='Active').order_by('id'),
        'groups': ServiceGroup.objects.filter(status='Active'),
        'activeGateway': PaymentGateway.objects.filter(status='Active'),
    }


def homepage(request):
    hot_services = _active_services().filter(recommended=1).order_by('-sells')[:10]
    trending_services = _active_services().order_by('-sells')[:10]
    recent_services = _active_services().order_by('-created_at')[:10]
    ctx = {
        'hot_services': [_service_dict(s) for s in hot_services],
        'trending_services': [_service_dict(s) for s in trending_services],
        'recent_services': [_service_dict(s) for s in recent_services],
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


def server_view(request, slug):
    service = get_object_or_404(ServiceList, slug=slug)
    if service.status != 'Active':
        return redirect('homepage')
    service_fields = list(service.service_fields.all())
    tags = _service_tags(service)
    ctx = {
        'serviceData': service,
        'serviceInputs': service_fields,
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
            next_url = request.POST.get('next') or reverse('customer_dashboard')
            return redirect(next_url)
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
            name=name, email=email, mobile=mobile,
            password=Customer.make_password(password), currency=currency,
        )
        messages.success(request, 'Cadastro realizado com sucesso. Faça login.')
        return redirect('homepage')
    return redirect('homepage')


def logout_view(request):
    request.session.pop('customer_id', None)
    return redirect('homepage')


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
    ctx = {
        'waitingAction': waiting_action,
        'total_orders': orders.count(),
        'total_spent': orders.aggregate(s=Sum('service_price'))['s'] or Decimal('0.00'),
        'balance': customer.balance,
        'latest_orders': orders[:5],
    }
    ctx.update(_base_ctx(request))
    return render(request, 'customer/dashboard.html', ctx)


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
        service_status='Waiting Action',
        service_type=_invoice_type_key(service.service_type),
        service_price=service.original_price,
        service_title=service.title,
        seen='false',
    )
    for field in service.service_fields.all():
        value = request.POST.get(field.name, '').strip()
        OrderInput.objects.create(order=order, field_name=field.name, field_value=value)
        if not order.service_input1:
            order.service_input1 = value
            order.save(update_fields=['service_input1'])

    price = service.original_price
    if customer.balance >= price:
        customer.balance = customer.balance - price
        customer.save(update_fields=['balance'])
        order.service_status = 'In Process'
        order.save(update_fields=['service_status'])
        Statement.objects.create(
            customer=customer, description=f"Order #{order.id} - {service.title}",
            type='Debit', amount=price, balance=customer.balance, order=order,
        )
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
        'activeGateway': PaymentGateway.objects.filter(status='Active'),
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
        if gateway_name.lower() == 'binance':
            return _pay_with_binance(request, customer, invoice)
        invoice.payment_gateway = gateway_name
        invoice.invoice_status = 'Paid'
        invoice.total_paid = invoice.invoice_amount
        invoice.payment_currency = customer.currency
        invoice.save()
        customer.balance = customer.balance + invoice.invoice_amount
        customer.save(update_fields=['balance'])
        Statement.objects.create(
            customer=customer,
            description=f"Invoice #{invoice.id} deposit - {gateway_name}",
            type='Credit', amount=invoice.invoice_amount, balance=customer.balance,
        )
        messages.success(request, 'Pagamento realizado com sucesso. Saldo adicionado.')
        return redirect('customer_invoice_detail', invoice_id=invoice.id)
    return redirect('checkout', invoice_id=invoice.id)


def _pay_with_asaas(request, customer, invoice):
    gateway = PaymentGateway.objects.filter(name__iexact='Asaas', status='Active').first()
    if not gateway or not (gateway.asaas_api_key or '').strip():
        messages.error(request, 'Gateway Asaas nao configurado. Adicione a chave de API no painel.')
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
    PaymentDeposit.objects.create(
        name='Asaas - PIX',
        gateway_amount=invoice.invoice_amount,
        gateway_payment_id=payment.get('id') or '',
        qrcode_url=payment.get('pixQrCode') or '',
        pix_code=payment.get('pixCopyPaste') or '',
        checkout_url=payment.get('invoiceUrl') or '',
        gateway_note=(payment.get('pixCopyPaste') or '')[:100],
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

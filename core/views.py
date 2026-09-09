from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import (
    Api, ApiLog, Currency, Customer, CustomerOrder, GatewayLog, Invoice,
    OrderInput, Page, PaymentGateway, ServiceGroup, ServiceInput, ServiceList,
    Slider, Statement, SystemSetting,
)

CATEGORY_SLUGS = {
    'server-service': {
        'type': 'Server Service',
        'display': 'Server',
        'title': 'Activation/Server Service',
    },
    'credit-service': {
        'type': 'Credit Service',
        'display': 'Credit',
        'title': "Tool's Credit Refill",
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
        'currency_icon': SystemSetting.get('currencyIcon', '$'),
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
        currency = request.POST.get('currency', 'USD')
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
        gateway_name = request.POST.get('payment_methode', '')
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

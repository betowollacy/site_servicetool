from decimal import Decimal

import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import logout
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify

from .models import (
    Currency, Customer, CustomerOrder, Invoice, Page, PaymentGateway, ServiceGroup,
    ServiceInput, ServiceList, Slider, SystemSetting,
)

STATUS_MAP = {
    'waiting': ('Waiting Action', 'Aguardando Ação'),
    'in_process': ('In Process', 'Em Processo'),
    'success': ('Success', 'Sucesso'),
    'rejected': ('Rejected', 'Rejeitados'),
}
SERVICE_STATUS_CHOICES = ['Waiting Action', 'In Process', 'Success', 'Rejected']

TYPE_MAP = {
    'server': ('Server Service', 'Aluguel'),
    'credit': ('Credit Service', 'Créditos'),
    'imei': ('IMEI Service', 'IMEI'),
}


def _staff(fn):
    return staff_member_required(fn, login_url='/admin/login/')


@_staff
def admin_dashboard(request):
    ctx = {
        'total_customers': Customer.objects.count(),
        'total_services': ServiceList.objects.count(),
        'waiting_orders': CustomerOrder.objects.filter(service_status='Waiting Action').count(),
        'total_invoices': Invoice.objects.count(),
        'recent_orders': CustomerOrder.objects.all()[:8],
    }
    return render(request, 'admin/dashboard.html', ctx)


@_staff
def admin_orders(request, status):
    db_status, label = STATUS_MAP.get(status, ('Waiting Action', 'Aguardando Ação'))
    orders = CustomerOrder.objects.filter(service_status=db_status)
    ctx = {
        'status': status,
        'status_label': label,
        'orders': orders,
        'status_choices': SERVICE_STATUS_CHOICES,
    }
    return render(request, 'admin/orders.html', ctx)


@_staff
def admin_order_update(request, order_id):
    order = CustomerOrder.objects.filter(id=order_id).first()
    if order and request.method == 'POST':
        new_status = request.POST.get('service_status')
        if new_status in SERVICE_STATUS_CHOICES:
            order.service_status = new_status
        order.replied_in = request.POST.get('replied_in', '') or order.replied_in
        order.save()
        messages.success(request, 'Pedido atualizado com sucesso.')
    return redirect('admin_orders', status='waiting')


@_staff
def admin_invoice_list(request):
    return render(request, 'admin/invoice_list.html', {'invoices': Invoice.objects.all()})


@_staff
def admin_customer_list(request):
    return render(request, 'admin/customer_list.html', {'customers': Customer.objects.all()})


@_staff
def admin_service_list(request, svtype):
    db_type, label = TYPE_MAP.get(svtype, ('Server Service', 'Server'))
    ctx = {
        'service_type': db_type,
        'type_label': label,
        'svtype': svtype,
        'services': ServiceList.objects.filter(service_type=db_type),
    }
    return render(request, 'admin/service_list.html', ctx)


@_staff
def admin_setting(request):
    keys = [
        'siteTitle', 'siteMetaTitle', 'siteMetaDes', 'siteKeyword', 'siteLogo', 'siteFav',
        'siteEmailAddress', 'sitePhoneNumber', 'siteAddress',
        'siteWhatsappUrl', 'siteTelegramUrl', 'siteFacebookUrl', 'siteTwitterUrl',
    ]
    settings = {k: SystemSetting.get(k, '') for k in keys}
    if request.method == 'POST':
        for k in keys:
            obj, _ = SystemSetting.objects.get_or_create(key=k, defaults={'value': ''})
            obj.value = request.POST.get(k, '')
            obj.save()
        messages.success(request, 'Configurações salvas com sucesso.')
        return redirect('admin_setting')
    return render(request, 'admin/setting.html', {'settings': settings})


def _service_type_from(svtype):
    return TYPE_MAP.get(svtype, ('Server Service', 'Server'))


def _group_for(svtype, label):
    try:
        return ServiceGroup.objects.get(slug=svtype)
    except ServiceGroup.DoesNotExist:
        group = ServiceGroup.objects.filter(name__icontains=label.split(' ')[0]).first()
        if group:
            return group
        return ServiceGroup.objects.create(name=label, slug=svtype)


def _apply_service_post(service, post):
    service.title = post.get('title', service.title) or service.title
    service.slug = slugify(post.get('slug') or service.title)
    for f in ['subtitle', 'duration', 'delivery_time', 'min_qnt', 'max_qnt',
              'tool_download', 'login_url', 'register_url', 'thumbnail', 'screenshot',
              'service_tags', 'meta_description',
              'kw1', 'kw2', 'kw3', 'kw4', 'kw5', 'article']:
        val = post.get(f)
        if val is not None:
            setattr(service, f, val)
    if post.get('status'):
        service.status = post['status']
    if post.get('process_type'):
        service.process_type = post['process_type']
    if post.get('price_type'):
        service.price_type = post['price_type']
    for f in ['original_price', 'customer_profit_amount', 'reseller_profit_amount',
              'distributor_profit_amount', 'webowner_profit_amount']:
        val = post.get(f)
        if val is not None and val != '':
            try:
                setattr(service, f, Decimal(val))
            except Exception:
                pass
    service.save()


def _save_fields(service, fields_text):
    ServiceInput.objects.filter(service=service).delete()
    for line in (fields_text or '').splitlines():
        line = line.strip()
        if line:
            ServiceInput.objects.create(service=service, name=line)


@_staff
def admin_service_new(request, svtype):
    db_type, label = _service_type_from(svtype)
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        if not title:
            messages.error(request, 'O título é obrigatório.')
            return redirect('admin_service_new', svtype)
        service = ServiceList.objects.create(
            service_type=db_type,
            title=title,
            slug=slugify(request.POST.get('slug') or title),
            service_group=_group_for(svtype, label),
            status='Active',
            process_type='Manual',
        )
        _apply_service_post(service, request.POST)
        _save_fields(service, request.POST.get('fields', ''))
        messages.success(request, 'Serviço criado com sucesso.')
        return redirect('admin_service_list', svtype)
    return render(request, 'admin/service_form.html', {
        'service': None,
        'svtype': svtype,
        'type_label': label,
        'service_fields': [],
    })


@_staff
def admin_service_edit(request, svtype, service_id):
    db_type, label = _service_type_from(svtype)
    service = ServiceList.objects.filter(id=service_id, service_type=db_type).first()
    if not service:
        messages.error(request, 'Serviço não encontrado.')
        return redirect('admin_service_list', svtype)
    if request.method == 'POST':
        _apply_service_post(service, request.POST)
        _save_fields(service, request.POST.get('fields', ''))
        messages.success(request, 'Serviço atualizado com sucesso.')
        return redirect('admin_service_list', svtype)
    return render(request, 'admin/service_form.html', {
        'service': service,
        'svtype': svtype,
        'type_label': label,
        'service_fields': service.service_fields.all(),
    })


@_staff
def admin_service_delete(request, svtype, service_id):
    db_type, label = _service_type_from(svtype)
    service = ServiceList.objects.filter(id=service_id, service_type=db_type).first()
    if not service:
        messages.error(request, 'Serviço não encontrado.')
        return redirect('admin_service_list', svtype)
    if request.method == 'POST':
        title = service.title
        service.delete()
        messages.success(request, f'Serviço "{title}" excluído com sucesso.')
    return redirect('admin_service_list', svtype)


@_staff
def admin_currency_list(request):
    return render(request, 'admin/currency_list.html', {'currencies': Currency.objects.filter(code='BRL')})


@_staff
def admin_currency_update(request, currency_id):
    cur = Currency.objects.filter(id=currency_id).first()
    if cur and cur.code == 'BRL' and request.method == 'POST':
        cur.status = 'Active'
        cur.rate = Decimal('1')
        cur.save()
        messages.success(request, 'BRL é a moeda única do site (fixa em R$).')
    return redirect('admin_currency_list')


@_staff
def admin_gateway_list(request):
    return render(request, 'admin/gateway_list.html', {
        'gateways': PaymentGateway.objects.filter(name__iexact='Asaas'),
        'currencies': Currency.objects.filter(status='Active'),
        'webhook_asaas': request.build_absolute_uri(reverse('asaas_webhook')),
        'webhook_binance': request.build_absolute_uri(reverse('binance_webhook')),
    })


@_staff
def admin_gateway_update(request, gateway_id):
    g = PaymentGateway.objects.filter(id=gateway_id).first()
    if g and request.method == 'POST':
        if request.POST.get('currency_code'):
            g.currency_code = request.POST['currency_code']
        charge = request.POST.get('charge')
        if charge is not None and charge != '':
            try:
                g.charge = Decimal(charge)
            except Exception:
                pass
        if request.POST.get('status') in ('Active', 'Inactive'):
            g.status = request.POST['status']
        for f in ['bkash_app_key', 'bkash_app_secret', 'bkash_username', 'bkash_password',
                  'binance_api_key', 'binance_secret_key', 'asaas_api_key']:
            if request.POST.get(f) is not None:
                setattr(g, f, request.POST[f])
        if request.POST.get('binance_private_key') is not None:
            g.binance_private_key = request.POST['binance_private_key']
        g.asaas_sandbox = request.POST.get('asaas_sandbox') == 'on'
        g.save()
        messages.success(request, 'Gateway atualizado com sucesso.')
    return redirect('admin_gateway_list')


def admin_logout(request):
    logout(request)
    return redirect('homepage')


# --------------------------------------------------------------------------- #
# Sliders
# --------------------------------------------------------------------------- #

@_staff
def admin_slider_list(request):
    ctx = {'sliders': Slider.objects.all().order_by('-id')}
    return render(request, 'admin/slider_list.html', ctx)


@_staff
def admin_slider_new(request):
    if request.method == 'POST':
        slider = Slider.objects.create(
            img=request.POST.get('img', ''),
            url=request.POST.get('url', '') or None,
            width=request.POST.get('width', '') or None,
            height=request.POST.get('height', '') or None,
            status=request.POST.get('status', 'Inactive'),
        )
        messages.success(request, 'Slider criado com sucesso.')
        return redirect('admin_slider_list')
    return render(request, 'admin/slider_form.html', {'slider': None})


@_staff
def admin_slider_edit(request, slider_id):
    slider = Slider.objects.filter(id=slider_id).first()
    if not slider:
        messages.error(request, 'Slider não encontrado.')
        return redirect('admin_slider_list')
    if request.method == 'POST':
        slider.img = request.POST.get('img', slider.img)
        slider.url = request.POST.get('url', '') or None
        slider.width = request.POST.get('width', '') or None
        slider.height = request.POST.get('height', '') or None
        slider.status = request.POST.get('status', 'Inactive')
        slider.save()
        messages.success(request, 'Slider atualizado com sucesso.')
        return redirect('admin_slider_list')
    return render(request, 'admin/slider_form.html', {'slider': slider})


@_staff
def admin_slider_delete(request, slider_id):
    Slider.objects.filter(id=slider_id).delete()
    messages.success(request, 'Slider removido.')
    return redirect('admin_slider_list')


@_staff
def admin_slider_upload_image(request):
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'error': 'Envie um arquivo de imagem.'}, status=400)
    f = request.FILES['image']
    name = (f.name or '').lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext not in ('png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'):
        return JsonResponse({'error': 'Formato não permitido (use PNG, JPG, WEBP, GIF ou BMP).'}, status=400)
    subdir = settings.MEDIA_ROOT / 'sliders'
    subdir.mkdir(parents=True, exist_ok=True)
    fname = f"slider_{uuid.uuid4().hex[:10]}.{ext}"
    dest = subdir / fname
    with open(dest, 'wb+') as out:
        for chunk in f.chunks():
            out.write(chunk)
    return JsonResponse({'url': f"{settings.MEDIA_URL}sliders/{fname}"})


# --------------------------------------------------------------------------- #
# Pages (CMS)
# --------------------------------------------------------------------------- #

@_staff
def admin_page_list(request):
    ctx = {'pages': Page.objects.all().order_by('-id')}
    return render(request, 'admin/page_list.html', ctx)


@_staff
def admin_page_new(request):
    if request.method == 'POST':
        title = (request.POST.get('page_title') or '').strip()
        if not title:
            messages.error(request, 'O título é obrigatório.')
            return redirect('admin_page_new')
        page = Page.objects.create(
            page_title=title,
            page_slug=slugify(request.POST.get('page_slug') or title),
            page_article=request.POST.get('page_article', ''),
            page_visibility=request.POST.get('page_visibility', 'Active'),
            page_thumbnail=request.POST.get('page_thumbnail', '') or None,
            page_meta_description=request.POST.get('page_meta_description', '') or None,
            page_kw1=request.POST.get('page_kw1', '') or None,
            page_kw2=request.POST.get('page_kw2', '') or None,
            page_kw3=request.POST.get('page_kw3', '') or None,
            page_kw4=request.POST.get('page_kw4', '') or None,
            page_kw5=request.POST.get('page_kw5', '') or None,
            page_author=request.POST.get('page_author', '') or None,
        )
        messages.success(request, 'Página criada com sucesso.')
        return redirect('admin_page_list')
    return render(request, 'admin/page_form.html', {'page': None})


@_staff
def admin_page_edit(request, page_id):
    page = Page.objects.filter(id=page_id).first()
    if not page:
        messages.error(request, 'Página não encontrada.')
        return redirect('admin_page_list')
    if request.method == 'POST':
        title = (request.POST.get('page_title') or '').strip()
        if not title:
            messages.error(request, 'O título é obrigatório.')
            return redirect('admin_page_edit', page_id)
        page.page_title = title
        page.page_slug = slugify(request.POST.get('page_slug') or title)
        page.page_article = request.POST.get('page_article', '')
        page.page_visibility = request.POST.get('page_visibility', 'Active')
        page.page_thumbnail = request.POST.get('page_thumbnail', '') or None
        page.page_meta_description = request.POST.get('page_meta_description', '') or None
        page.page_kw1 = request.POST.get('page_kw1', '') or None
        page.page_kw2 = request.POST.get('page_kw2', '') or None
        page.page_kw3 = request.POST.get('page_kw3', '') or None
        page.page_kw4 = request.POST.get('page_kw4', '') or None
        page.page_kw5 = request.POST.get('page_kw5', '') or None
        page.page_author = request.POST.get('page_author', '') or None
        page.save()
        messages.success(request, 'Página atualizada com sucesso.')
        return redirect('admin_page_list')
    return render(request, 'admin/page_form.html', {'page': page})


@_staff
def admin_page_delete(request, page_id):
    Page.objects.filter(id=page_id).delete()
    messages.success(request, 'Página removida.')
    return redirect('admin_page_list')

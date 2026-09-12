from decimal import Decimal
from pathlib import Path
import os
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import logout
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify

from .models import (
    Api, ACTIVATION_SERVICE_EXTRA_FIELDS, CREDIT_SERVICE_EXTRA_FIELDS,
    Currency, Customer, CustomerOrder, Inventory,
    InventoryData, Invoice, OrderInput, Page, PaymentGateway, RemoteServiceInput, RemoteServiceList,
    ServiceGroup, ServiceInput, ServiceList, Slider, Statement, SystemSetting,
)
from . import provider_api, public_api

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
    'activation': ('Activation Service', 'Ativação'),
    'imei': ('IMEI Service', 'IMEI'),
    'method': ('Method Service', 'Métodos'),
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
    orders = list(CustomerOrder.objects.filter(service_status=db_status)
                  .select_related('customer', 'service__inventory'))
    inv_ids = {o.service.inventory_id for o in orders if o.service and o.service.inventory_id}
    avail = dict(
        InventoryData.objects.filter(inventory_id__in=inv_ids, status='Available')
        .values('inventory_id').annotate(c=Count('id')).values_list('inventory_id', 'c')
    )
    descriptions = dict(
        OrderInput.objects.filter(field_name='Descreva o serviço')
        .values_list('order_id', 'field_value')
    )
    for order in orders:
        inv = order.service.inventory if order.service else None
        order.inventory_id_for_delivery = inv.id if inv else None
        order.available_count = avail.get(inv.id, 0) if inv else 0
        order.service_description = descriptions.get(order.id, '')
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
def admin_order_refund(request, order_id):
    order = CustomerOrder.objects.filter(id=order_id).first()
    if order and request.method == 'POST':
        if order.service_status == 'Rejected':
            messages.error(request, 'O pedido #{} já está rejeitado/estornado.'.format(order.id))
        else:
            amount = order.service_price
            provider_api.refund_order(order, 'Estorno manual pelo administrador (pedido equivocado).')
            messages.success(request, 'Pedido #{} estornado: R$ {} devolvidos ao saldo do cliente.'.format(order.id, amount))
    referer = request.META.get('HTTP_REFERER') or reverse('admin_orders', args=['waiting'])
    return redirect(referer)


@_staff
def admin_order_delete(request, order_id):
    order = CustomerOrder.objects.filter(id=order_id).first()
    if order and request.method == 'POST':
        oid = order.id
        order.delete()
        messages.success(request, 'Pedido #{} excluído com sucesso.'.format(oid))
    referer = request.META.get('HTTP_REFERER') or reverse('admin_orders', args=['waiting'])
    return redirect(referer)


@_staff
def admin_customer_refund(request, customer_id):
    customer = Customer.objects.filter(id=customer_id).first()
    if customer and request.method == 'POST':
        try:
            amount = Decimal((request.POST.get('amount') or '0').replace(',', '.'))
        except Exception:
            amount = Decimal('0')
        reason = (request.POST.get('reason') or '').strip()[:500]
        if amount <= 0:
            messages.error(request, 'Informe um valor maior que zero para estornar.')
        else:
            customer.balance = customer.balance + amount
            customer.save(update_fields=['balance'])
            Statement.objects.create(
                customer=customer,
                description=reason or 'Estorno manual pelo administrador',
                type='Credit', amount=amount, balance=customer.balance,
            )
            messages.success(request, 'Estorno de R$ {} creditado para {}.'.format(amount, customer.name))
    return redirect('admin_customer_list')


@_staff
def admin_invoice_list(request):
    return render(request, 'admin/invoice_list.html', {'invoices': Invoice.objects.all()})


@_staff
def admin_invoice_delete(request, invoice_id):
    inv = Invoice.objects.filter(id=invoice_id).first()
    if inv and request.method == 'POST':
        iid = inv.id
        inv.delete()
        messages.success(request, 'Fatura #{} excluída com sucesso.'.format(iid))
    return redirect('admin_invoice_list')


@_staff
def admin_invoice_toggle_paid(request, invoice_id):
    inv = Invoice.objects.filter(id=invoice_id).first()
    if inv and request.method == 'POST':
        inv.invoice_status = 'Unpaid' if inv.invoice_status == 'Paid' else 'Paid'
        inv.total_paid = inv.invoice_amount if inv.invoice_status == 'Paid' else Decimal('0')
        inv.save(update_fields=['invoice_status', 'total_paid'])
        messages.success(request, 'Fatura #{} marcada como {}.'.format(inv.id, inv.invoice_status))
    return redirect('admin_invoice_list')


@_staff
def admin_customer_list(request):
    return render(request, 'admin/customer_list.html', {
        'customers': Customer.objects.all(),
        'roles': Customer.ROLES,
        'statuses': Customer.STATUS,
    })


@_staff
def admin_customer_edit(request, customer_id):
    customer = Customer.objects.filter(id=customer_id).first()
    if customer and request.method == 'POST':
        post = request.POST
        name = (post.get('name') or '').strip()
        email = (post.get('email') or '').strip().lower()
        if name:
            customer.name = name
        if email and email != customer.email:
            if Customer.objects.filter(email=email).exclude(id=customer.id).exists():
                messages.error(request, 'O e-mail {} já está em uso por outro cliente.'.format(email))
                return redirect('admin_customer_list')
            customer.email = email
        customer.mobile = (post.get('mobile') or '').strip() or None
        customer.cpf_cnpj = (post.get('cpf_cnpj') or '').strip() or None
        if post.get('role') in dict(Customer.ROLES):
            customer.role = post['role']
        if post.get('status') in dict(Customer.STATUS):
            customer.status = post['status']
        customer.save()
        messages.success(request, 'Cadastro de "{}" atualizado com sucesso.'.format(customer.name))
    return redirect('admin_customer_list')


@_staff
def admin_customer_password(request, customer_id):
    customer = Customer.objects.filter(id=customer_id).first()
    if customer and request.method == 'POST':
        new_password = (request.POST.get('new_password') or '').strip()
        if len(new_password) < 6:
            messages.error(request, 'A nova senha deve ter pelo menos 6 caracteres.')
        else:
            customer.password = Customer.make_password(new_password)
            customer.save(update_fields=['password'])
            messages.success(request, 'Senha de {} alterada com sucesso.'.format(customer.email))
    return redirect('admin_customer_list')


@_staff
def admin_customer_delete(request, customer_id):
    customer = Customer.objects.filter(id=customer_id).first()
    if customer and request.method == 'POST':
        name = customer.name
        customer.delete()
        messages.success(request, 'Cliente "{}" excluído com sucesso.'.format(name))
    return redirect('admin_customer_list')


@_staff
def admin_customer_api_toggle(request, customer_id):
    customer = Customer.objects.filter(id=customer_id).first()
    if customer and request.method == 'POST':
        if str(customer.api_allow or '').strip().lower() in ('on', '1', 'true', 'yes'):
            customer.api_allow = ''
            status = 'desabilitado'
        else:
            if not customer.api_key:
                customer.api_key = public_api.generate_api_key()
            customer.api_allow = 'on'
            status = 'habilitado'
        customer.save(update_fields=['api_allow', 'api_key'])
        messages.success(request, f'Acesso à API de {customer.email} {status} com sucesso.')
    return redirect('admin_customer_list')


@_staff
def admin_service_list(request, svtype):
    db_type, label = TYPE_MAP.get(svtype, ('Server Service', 'Server'))
    ctx = {
        'service_type': db_type,
        'type_label': label,
        'svtype': svtype,
        'services': ServiceList.objects.filter(service_type=db_type),
        'type_options': [(k, TYPE_MAP[k][1]) for k in TYPE_MAP],
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
    service.api_enabled = bool(post.get('api_enabled'))
    if post.get('carousel') in ('promocoes', 'desbloqueios'):
        service.carousel = post['carousel']
    else:
        service.carousel = ''
    if post.get('process_type'):
        service.process_type = post['process_type']
    if post.get('price_type'):
        service.price_type = post['price_type']
    inv_value = post.get('inventory_id')
    if inv_value is not None:
        if inv_value == '0' or inv_value == '':
            service.inventory = None
        else:
            service.inventory = Inventory.objects.filter(id=inv_value).first()
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


def _save_uploaded_thumbnail(service, files, request=None):
    """Salva a imagem enviada em MEDIA_ROOT/thumbnails e atualiza o campo thumbnail."""
    img = files.get('thumbnail_image')
    if not img:
        return
    ext = os.path.splitext(img.name)[1].lower() or '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        if request:
            messages.warning(request, 'Formato de imagem não suportado (use JPG, PNG, WEBP ou GIF).')
        return
    folder = Path(settings.MEDIA_ROOT) / 'thumbnails'
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{service.slug or 'service'}-{uuid.uuid4().hex[:8]}{ext}"
    with open(folder / name, 'wb+') as dest:
        for chunk in img.chunks():
            dest.write(chunk)
    service.thumbnail = f"{settings.MEDIA_URL}thumbnails/{name}"
    service.save(update_fields=['thumbnail'])


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
        _save_uploaded_thumbnail(service, request.FILES, request)
        messages.success(request, 'Serviço criado com sucesso.')
        return redirect('admin_service_list', svtype)
    return render(request, 'admin/service_form.html', {
        'service': None,
        'svtype': svtype,
        'type_label': label,
        'service_fields': [],
        'inventories': Inventory.objects.all().order_by('name'),
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
        _save_uploaded_thumbnail(service, request.FILES, request)
        messages.success(request, 'Serviço atualizado com sucesso.')
        return redirect('admin_service_list', svtype)
    return render(request, 'admin/service_form.html', {
        'service': service,
        'svtype': svtype,
        'type_label': label,
        'service_fields': service.service_fields.all(),
        'inventories': Inventory.objects.all().order_by('name'),
    })


@_staff
def admin_service_toggle_api(request, svtype, service_id):
    db_type, label = _service_type_from(svtype)
    service = ServiceList.objects.filter(id=service_id, service_type=db_type).first()
    if not service:
        messages.error(request, 'Serviço não encontrado.')
        return redirect('admin_service_list', svtype)
    if request.method == 'POST':
        service.api_enabled = not service.api_enabled
        service.save(update_fields=['api_enabled'])
        if service.api_enabled:
            messages.success(request, 'API reativada para "{}" - pedidos voltam a ser enviados ao provedor.'.format(service.title))
        else:
            messages.warning(request, 'API desativada para "{}" - os pedidos agora exigem entrega manual.'.format(service.title))
    return redirect('admin_service_list', svtype)


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
def admin_service_move(request, svtype, service_id):
    db_type, label = _service_type_from(svtype)
    service = ServiceList.objects.filter(id=service_id, service_type=db_type).first()
    if not service:
        messages.error(request, 'Serviço não encontrado.')
        return redirect('admin_service_list', svtype)
    target = request.POST.get('target_svtype', '')
    new_type, new_label = TYPE_MAP.get(target, (None, None))
    if request.method == 'POST' and new_type:
        service.service_type = new_type
        service.service_group = _group_for(target, new_label)
        service.save(update_fields=['service_type', 'service_group'])
        messages.success(request, f'Serviço "{service.title}" movido para {new_label}.')
        return redirect('admin_service_list', target)
    messages.error(request, 'Categoria de destino inválida.')
    return redirect('admin_service_list', svtype)


# --------------------------------------------------------------------------- #
# Inventário de logins/senhas (entrega manual quando a API está desligada)
# --------------------------------------------------------------------------- #

def _inline_credential(line):
    """Tenta interpretar a linha como 'usuario;senha', 'usuario|senha',
    'usuario:senha' ou 'usuario<TAB>senha'. Retorna (user, passwd) ou None
    se a linha não tem separador (é apenas um valor solto)."""
    user = passwd = None
    for sep in (';', '|', '\t'):
        if sep in line:
            user, passwd = line.split(sep, 1)
            break
    if user is None and line.count(':') == 1 and '://' not in line:
        user, passwd = line.split(':', 1)
    if user is None:
        return None
    user, passwd = user.strip(), passwd.strip()
    if not user and not passwd:
        return None
    return user, passwd


def _parse_credentials(text):
    """Converte a lista de credenciais em linhas prontas para o estoque.

    Aceita uma credencial por linha ("usuario;senha", "usuario|senha",
    "usuario:senha") OU o login numa linha e a senha na linha seguinte:
      tool@amg.com
      senha123
    """
    creds = []
    pending = None
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        pair = _inline_credential(line)
        if pair is not None:
            if pending is not None:
                creds.append(pending)
                pending = None
            user, passwd = pair
            if user and passwd:
                creds.append('Usuario: {} | Senha: {}'.format(user, passwd))
            else:
                creds.append(user or passwd)
            continue
        if pending is None:
            pending = line
        else:
            creds.append('Usuario: {} | Senha: {}'.format(pending, line))
            pending = None
    if pending is not None:
        creds.append(pending)
    return creds


def _refresh_inventory_counts(inventory):
    available = InventoryData.objects.filter(inventory=inventory, status='Available').count()
    sold = InventoryData.objects.filter(inventory=inventory, status='Sold out').count()
    inventory.available_code = available
    inventory.availableCount = available
    inventory.soldOutCount = sold
    inventory.save(update_fields=['available_code', 'availableCount', 'soldOutCount'])


@_staff
def admin_inventory_quick_add(request):
    """Formulario único: escolher o serviço e colar os logins/senhas.

    Cria o estoque do serviço automaticamente se ainda não existir e adiciona
    as credenciais. Elimina a necessidade de criar estoque e vincular depois."""
    if request.method == 'POST':
        service_id = request.POST.get('service_id')
        service = ServiceList.objects.filter(id=service_id).first() if service_id else None
        if not service:
            messages.error(request, 'Selecione um serviço para receber os logins.')
            return redirect('admin_inventory_list')
        inv = service.inventory
        if inv is None:
            inv = Inventory.objects.create(name=service.title)
            service.inventory = inv
            service.save(update_fields=['inventory'])
        creds = _parse_credentials(request.POST.get('codes', ''))
        existing = {c.lower() for c in InventoryData.objects.filter(inventory=inv).values_list('code', flat=True)}
        added = 0
        skipped = 0
        for cred in creds:
            if cred.lower() in existing:
                skipped += 1
                continue
            InventoryData.objects.create(inventory=inv, code=cred, status='Available')
            existing.add(cred.lower())
            added += 1
        _refresh_inventory_counts(inv)
        msg = '{} credencial(is) adicionada(s) ao estoque de "{}".'.format(added, service.title)
        if skipped:
            msg += ' {} já existia(m) e foi(ram) ignorada(s).'.format(skipped)
        if added:
            messages.success(request, msg)
        else:
            messages.error(request, msg if skipped else 'Nenhuma credencial válida informada.')
        return redirect('admin_inventory_detail', inv.id)
    return redirect('admin_inventory_list')


@_staff
def admin_inventory_list(request):
    _inventories = []
    for inv in Inventory.objects.all().order_by('name'):
        _inventories.append({
            'inventory': inv,
            'service': ServiceList.objects.filter(inventory=inv).first(),
            'available': InventoryData.objects.filter(inventory=inv, status='Available').count(),
            'total': InventoryData.objects.filter(inventory=inv).count(),
        })
    services = ServiceList.objects.filter(status='Active').order_by('service_type', 'title')
    return render(request, 'admin/inventory_list.html', {
        'inventories': _inventories,
        'services': services,
    })


@_staff
def admin_inventory_new(request):
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'O nome do estoque é obrigatório.')
            return redirect('admin_inventory_list')
        inv = Inventory.objects.create(name=name)
        _refresh_inventory_counts(inv)
        service_id = request.POST.get('service_id') or None
        if service_id and service_id != '0':
            service = ServiceList.objects.filter(id=service_id).first()
            if service:
                service.inventory = inv
                service.save(update_fields=['inventory'])
        messages.success(request, 'Estoque "{}" criado. Agora adicione os logins e senhas.'.format(name))
        return redirect('admin_inventory_detail', inv.id)
    return render(request, 'admin/inventory_form.html', {
        'inventory': None,
        'linked_service': None,
        'services': ServiceList.objects.filter(status='Active').order_by('service_type', 'title'),
    })


@_staff
def admin_inventory_detail(request, inventory_id):
    inv = Inventory.objects.filter(id=inventory_id).first()
    if inv is None:
        return redirect('admin_inventory_list')
    data_items = InventoryData.objects.filter(inventory=inv).select_related('order').order_by('-status', 'id')
    return render(request, 'admin/inventory_detail.html', {
        'inventory': inv,
        'linked_service': ServiceList.objects.filter(inventory=inv).first(),
        'services': ServiceList.objects.filter(status='Active').order_by('service_type', 'title'),
        'data_items': data_items,
        'available': InventoryData.objects.filter(inventory=inv, status='Available').count(),
        'in_use': InventoryData.objects.filter(inventory=inv, status='Sold out').count(),
    })


@_staff
def admin_inventory_update(request, inventory_id):
    inv = Inventory.objects.filter(id=inventory_id).first()
    if inv and request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if name:
            inv.name = name
            inv.save(update_fields=['name'])
        service_id = request.POST.get('service_id') or None
        ServiceList.objects.filter(inventory=inv).update(inventory=None)
        if service_id and service_id != '0':
            service = ServiceList.objects.filter(id=service_id).first()
            if service:
                service.inventory = inv
                service.save(update_fields=['inventory'])
        messages.success(request, 'Estoque atualizado com sucesso.')
    return redirect('admin_inventory_detail', inventory_id)


@_staff
def admin_inventory_delete(request, inventory_id):
    inv = Inventory.objects.filter(id=inventory_id).first()
    if inv and request.method == 'POST':
        ServiceList.objects.filter(inventory=inv).update(inventory=None)
        name = inv.name
        inv.delete()
        messages.success(request, 'Estoque "{}" excluído com sucesso.'.format(name))
    return redirect('admin_inventory_list')


@_staff
def admin_inventory_add(request, inventory_id):
    inv = Inventory.objects.filter(id=inventory_id).first()
    if inv and request.method == 'POST':
        creds = _parse_credentials(request.POST.get('codes', ''))
        existing = {c.lower() for c in InventoryData.objects.filter(inventory=inv).values_list('code', flat=True)}
        added = 0
        skipped = 0
        for cred in creds:
            if cred.lower() in existing:
                skipped += 1
                continue
            InventoryData.objects.create(inventory=inv, code=cred, status='Available')
            existing.add(cred.lower())
            added += 1
        _refresh_inventory_counts(inv)
        linked = ServiceList.objects.filter(inventory=inv).first()
        service_name = linked.title if linked else 'nenhum serviço vinculado'
        msg = '{} credencial(is) adicionada(s) ao estoque "{}" (serviço vinculado: {}).'.format(added, inv.name, service_name)
        if skipped:
            msg += ' {} já existia(m) e foi(ram) ignorada(s).'.format(skipped)
        if added:
            messages.success(request, msg)
        else:
            messages.error(request, msg if skipped else 'Nenhuma credencial válida informada.')
    return redirect('admin_inventory_detail', inventory_id)


@_staff
def admin_inventory_edit(request, data_id):
    item = InventoryData.objects.filter(id=data_id).first()
    if item and request.method == 'POST':
        new_code = (request.POST.get('code') or '').strip()
        if new_code:
            item.code = new_code
            item.save(update_fields=['code'])
        messages.success(request, 'Credencial salva. Caso tenha trocado a senha na ferramenta, disponibilize-a novamente.')
    if item:
        return redirect('admin_inventory_detail', item.inventory_id)
    return redirect('admin_inventory_list')


@_staff
def admin_inventory_toggle(request, data_id):
    item = InventoryData.objects.filter(id=data_id).first()
    if item and request.method == 'POST':
        if item.status == 'Available':
            item.status = 'Sold out'
            messages.success(request, 'Credencial marcada como em uso (indisponível).')
        else:
            item.status = 'Available'
            item.order = None
            messages.success(request, 'Credencial disponibilizada novamente para o próximo pedido.')
        item.save()
        _refresh_inventory_counts(item.inventory)
        return redirect('admin_inventory_detail', item.inventory_id)
    return redirect('admin_inventory_list')


@_staff
def admin_inventory_data_delete(request, data_id):
    item = InventoryData.objects.filter(id=data_id).first()
    if item and request.method == 'POST':
        inv = item.inventory
        item.delete()
        _refresh_inventory_counts(inv)
        messages.success(request, 'Credencial removida do estoque.')
        return redirect('admin_inventory_detail', inv.id)
    return redirect('admin_inventory_list')


@_staff
def admin_order_deliver_credential(request, order_id):
    """Entrega ao pedido o próximo login/senha disponível no estoque do serviço.

    Preenche a resposta do pedido (visível ao cliente) sem precisar editar o
    status manualmente e marca a credencial como em uso.
    """
    order = CustomerOrder.objects.filter(id=order_id).select_related('service', 'service__inventory').first()
    referer = request.META.get('HTTP_REFERER') or reverse('admin_orders', args=['in_process'])
    if not order or request.method != 'POST':
        return redirect(referer)
    delivered, code = provider_api.deliver_from_inventory(order)
    if delivered:
        messages.success(request, 'Login/senha entregue ao pedido #{} e resposta preenchida automaticamente.'.format(order.id))
    else:
        messages.error(request, code)
    return redirect(referer)


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
# APIs do provedor
# --------------------------------------------------------------------------- #

REMOTE_TYPE_TO_LOCAL = {
    'IMEI': 'IMEI Service',
    'REMOTE': 'Credit Service',
    'SERVER': 'Server Service',
}


@_staff
def admin_api_list(request):
    apis = Api.objects.all().order_by('-id')
    local_services = list(ServiceList.objects.filter(status='Active').order_by('service_type', 'title'))
    q = (request.GET.get('q') or '').strip()
    remote_qs = RemoteServiceList.objects.select_related('api').order_by('api_id', 'SERVICENAME')
    if q:
        remote_qs = remote_qs.filter(Q(SERVICENAME__icontains=q) | Q(referenceid__icontains=q))
    remote_services = list(remote_qs)
    linked_by_remote = {}
    for linked in ServiceList.objects.exclude(api__isnull=True).exclude(referenceid__isnull=True).exclude(referenceid=''):
        linked_by_remote.setdefault((linked.api_id, linked.referenceid), []).append(linked)
    return render(request, 'admin/api_list.html', {
        'apis': apis,
        'local_services': local_services,
        'remote_services': remote_services,
        'linked_by_remote': linked_by_remote,
        'remote_type_to_local': REMOTE_TYPE_TO_LOCAL,
        'search_q': q,
    })


@_staff
def admin_api_detail(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api is None:
        return redirect('admin_api_list')
    return render(request, 'admin/api_detail.html', {'api': api})


@_staff
def admin_api_new(request):
    if request.method == 'POST':
        name = (request.POST.get('api_name') or '').strip()
        if not name:
            messages.error(request, 'O nome da API é obrigatório.')
            return redirect('admin_api_list')
        Api.objects.create(
            api_name=name,
            api_type=(request.POST.get('api_type') or '').strip(),
            api_url=(request.POST.get('api_url') or '').strip(),
            api_username=(request.POST.get('api_username') or '').strip(),
            api_key=(request.POST.get('api_key') or '').strip(),
            status=request.POST.get('status', 'Active'),
        )
        messages.success(request, 'API criada com sucesso.')
        return redirect('admin_api_list')
    return render(request, 'admin/api_form.html', {'api': None})


@_staff
def admin_api_update(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api and request.method == 'POST':
        api.api_name = (request.POST.get('api_name') or api.api_name).strip()
        api.api_type = (request.POST.get('api_type') or '').strip()
        api.api_url = (request.POST.get('api_url') or '').strip()
        api.api_username = (request.POST.get('api_username') or '').strip()
        if request.POST.get('api_key') is not None:
            api.api_key = request.POST['api_key'].strip()
        if request.POST.get('status') in ('Active', 'Inactive'):
            api.status = request.POST['status']
        api.save()
        messages.success(request, 'API atualizada com sucesso.')
    return redirect('admin_api_list')


@_staff
def admin_api_test(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api:
        try:
            info = provider_api.account_info(api)
            messages.success(request, 'Conexão OK. Conta: {} | Saldo: {}'.format(info['mail'], info['credit']))
        except provider_api.ProviderError as exc:
            messages.error(request, 'Falha na conexão: {}'.format(exc))
    return redirect('admin_api_list')


@_staff
def admin_api_import(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api:
        try:
            catalog = provider_api.fetch_catalog(api)
        except provider_api.ProviderError as exc:
            messages.error(request, 'Falha ao importar: {}'.format(exc))
            return redirect('admin_api_list')
        existing = {r.referenceid: r for r in RemoteServiceList.objects.filter(api=api)}
        created = 0
        updated = 0
        for item in catalog:
            rid = item['referenceid']
            remote = existing.get(rid)
            credit = Decimal(str(item['credit']) or '0')
            fields = list(dict.fromkeys(item['fields']))
            if remote is None:
                remote = RemoteServiceList.objects.create(
                    api=api,
                    referenceid=rid,
                    SERVICENAME=item['name'],
                    SERVICETYPE=item['servicetype'],
                    CREDIT=credit,
                    added=True,
                )
                created += 1
                sync = True
            else:
                changed = (
                    remote.SERVICENAME != item['name']
                    or remote.SERVICETYPE != item['servicetype']
                    or remote.CREDIT != credit
                    or not remote.added
                )
                if changed:
                    remote.SERVICENAME = item['name']
                    remote.SERVICETYPE = item['servicetype']
                    remote.CREDIT = credit
                    remote.added = True
                    remote.save(update_fields=['SERVICENAME', 'SERVICETYPE', 'CREDIT', 'added'])
                    updated += 1
                sync = changed
            if sync:
                current = set(remote.service_fields.values_list('name', flat=True))
                to_delete = current - set(fields)
                if to_delete:
                    RemoteServiceInput.objects.filter(remote_service=remote, name__in=to_delete).delete()
                for fname in fields:
                    if fname not in current:
                        RemoteServiceInput.objects.create(remote_service=remote, name=fname)
        messages.success(request, 'Importados {} serviços do provedor ({} novos, {} atualizados).'.format(
            len(catalog), created, updated))
    return redirect('admin_api_list')


@_staff
def admin_api_link(request):
    if request.method == 'POST':
        remote_id = request.POST.get('remote_id')
        service_id = request.POST.get('service_id') or None
        remote = RemoteServiceList.objects.filter(id=remote_id).first()
        if remote:
            ServiceList.objects.filter(api=remote.api, referenceid=remote.referenceid)\
                                .update(api=None, referenceid='')
            if service_id:
                service = ServiceList.objects.filter(id=service_id).first()
                if service:
                    service.api = remote.api
                    service.referenceid = remote.referenceid
                    service.process_type = 'Auto'
                    service.save(update_fields=['api', 'referenceid', 'process_type'])
                    local_type = REMOTE_TYPE_TO_LOCAL.get(remote.SERVICETYPE.upper(), service.service_type)
                    if local_type != service.service_type:
                        service.service_type = local_type
                        service.save(update_fields=['service_type'])
                    remote_fields = list(remote.service_fields.all())
                    if remote_fields:
                        ServiceInput.objects.filter(service=service).delete()
                        remote_names = [rf.name for rf in remote_fields]
                        for rf in remote_fields:
                            ServiceInput.objects.create(service=service, name=rf.name)
                    else:
                        remote_names = []
                    if local_type == 'Credit Service':
                        extras = CREDIT_SERVICE_EXTRA_FIELDS
                    elif local_type == 'Activation Service':
                        extras = ACTIVATION_SERVICE_EXTRA_FIELDS
                    else:
                        extras = ()
                    for extra in extras:
                        if extra not in remote_names:
                            ServiceInput.objects.get_or_create(service=service, name=extra)
                    messages.success(request, 'Serviço "{}" vinculado ao provedor.'.format(service.title))
                else:
                    messages.error(request, 'Serviço local não encontrado.')
            else:
                messages.success(request, 'Serviço do provedor desvinculado.')
    return redirect('admin_api_list')


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

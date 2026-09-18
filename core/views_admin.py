from decimal import Decimal, InvalidOperation
from pathlib import Path
import io
import json
import os
import sqlite3
import tempfile
import time
import unicodedata
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import logout
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    Api, ACTIVATION_SERVICE_EXTRA_FIELDS, CREDIT_SERVICE_EXTRA_FIELDS,
    METHOD_SERVICE_EXTRA_FIELDS, SERVICE_COLLECT_DATA_CHOICES,
    Currency, Customer, CustomerOrder, Inventory,
    InventoryData, Invoice, OrderInput, Page, PaymentDeposit, PaymentGateway, RemoteServiceInput, RemoteServiceList,
    ServiceGroup, ServiceInput, ServiceList, Slider, Statement, SystemSetting, User,
    collect_data_codes, collect_field_code_by_name,
)
from . import asaas, catalog_images, notify, provider_api, public_api

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
    return staff_member_required(fn, login_url='/django-admin/login/')


STOCK_ACCESS_ADMIN_EMAIL = 'enterserver@hotmail.com'
STOCK_ACCESS_PASSWORD_KEY = 'stockAccessPassword'


def _is_stock_access_admin(user):
    return (getattr(user, 'email', '') or '').strip().lower() == STOCK_ACCESS_ADMIN_EMAIL


SETTINGS_ACCESS_PASSWORD_KEY = 'settingsAccessPassword'
SETTINGS_UNLOCK_SESSION = 'settings_access_unlocked'


def _settings_access_owner(user):
    return (getattr(user, 'email', '') or '').strip().lower() == STOCK_ACCESS_ADMIN_EMAIL


def _settings_locked(request):
    """True se a seção Configurações está protegida por senha e ainda não foi desbloqueada na sessão.

    Nunca bloqueia o dono enquanto a senha não estiver configurada (primeiro acesso)."""
    if not _settings_access_owner(request.user):
        return True
    if request.session.get(SETTINGS_UNLOCK_SESSION):
        return False
    expected = SystemSetting.get(SETTINGS_ACCESS_PASSWORD_KEY, '').strip()
    return bool(expected)


def _deny_settings_access(request):
    messages.error(request, 'Acesso restrito à seção Configurações. Apenas o administrador dono pode acessá-la.')
    return redirect('admin_dashboard')


def _guard_settings_section(request,
                            owner_msg='Acesso restrito à seção Configurações. Apenas o administrador dono pode acessá-la.',
                            lock_msg='Seção Configurações bloqueada. Desbloqueie com a senha de acesso primeiro.'):
    """Bloqueia qualquer página da seção Configurações para quem não é o dono,
    e pede senha antes de abrir quando a senha de acesso estiver configurada.

    Retorna uma HttpResponse de redirecionamento quando bloqueado, ou None se liberado."""
    if not _settings_access_owner(request.user):
        messages.error(request, owner_msg)
        return redirect('admin_dashboard')
    if _settings_locked(request):
        messages.error(request, lock_msg)
        return redirect('admin_setting')
    return None


def _check_stock_order_access(request, service):
    """Bloqueia a compra no painel de um produto entregue do estoque de logins
    (service.inventory) caso a senha de acesso não esteja configurada ou não bata.

    Clientes nunca passam por aqui: a senha é exigida apenas quando um
    administrador compra no painel. Retorna '' (liberado) ou mensagem de erro."""
    if not service or not service.inventory_id:
        return ''
    expected = SystemSetting.get(STOCK_ACCESS_PASSWORD_KEY, '').strip()
    given = (request.POST.get('stockAccessPassword') or '').strip()
    if not expected:
        return 'Acesso ao estoque de logins negado: configure a senha de acesso nas Configurações do Sistema.'
    if given != expected:
        return 'Senha de acesso ao estoque de logins incorreta. Você não pode comprar este produto.'
    return ''


AVAILABLE_PERIODS = {
    'all': 'Tudo',
    '30': 'Últimos 30 dias',
    '7': 'Últimos 7 dias',
    'today': 'Hoje',
}


def _dashboard_period(request):
    period = request.GET.get('period', '').strip().lower()
    if period not in AVAILABLE_PERIODS:
        period = 'all'
    since = None
    if period == 'today':
        since = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif period in ('7', '30'):
        since = timezone.now() - timezone.timedelta(days=int(period))
    return period, since


def _order_qty(order):
    try:
        q = str(order.service_qnt or '').strip()
        return int(q or '1')
    except (TypeError, ValueError):
        return 1


def _order_credits(order):
    """Custo do pedido em créditos do provedor (CREDIT do catálogo x quantidade).

    Pedidos entregues do estoque local custam zero na API."""
    service = order.service
    if not service or not service.api_id or not (service.referenceid or '').strip():
        return Decimal('0.00')
    if service.inventory_id:
        return Decimal('0.00')
    qty = _order_qty(order)
    remote = (RemoteServiceList.objects
              .filter(api_id=service.api_id, referenceid=(service.referenceid or '').strip())
              .first())
    credit = Decimal('0.00')
    if remote and remote.CREDIT:
        credit = remote.CREDIT
    elif service.api and service.api.reseller_price:
        credit = service.api.reseller_price
    return (credit * qty).quantize(Decimal('0.00'))


def _order_cost(order, is_direct):
    """Custo estimado em BRL que o site paga à API pelo pedido (CREDIT x rate)."""
    credits = _order_credits(order)
    if credits and order.service and order.service.api and order.service.api.price_rate:
        return (credits * order.service.api.price_rate).quantize(Decimal('0.00'))
    if is_direct:
        # Pedido direto do admin registra o custo do provedor no próprio valor.
        return (order.service_price or Decimal('0.00')).quantize(Decimal('0.00'))
    return Decimal('0.00')


def _is_admin_direct(order):
    """Pedidos diretos (Pedido Direto na API) não movimentam saldo/statement."""
    return not order.statements.all()


def _order_creator_display(request, order):
    """Retorna nome do administrador que fez o pedido.

    Para pedidos antigos sem created_by, infere cruzando o e-mail do
    customer do pedido com um User is_staff (regra usada pelo
    get_or_create do admin_customer).
    """
    user = getattr(order, 'created_by', None)
    if user is None:
        customer = getattr(order, 'customer', None)
        email = (getattr(customer, 'email', '') or '').strip().lower()
        if email:
            user = User.objects.filter(is_staff=True).filter(
                Q(email__iexact=email) | Q(username__iexact=email),
            ).first()
    if user is None:
        return None
    name = user.get_full_name() or user.get_short_name() or user.username
    return name or None


def _api_credit_decimal(info):
    if not info:
        return Decimal('0.00')
    try:
        return Decimal(str(info.get('creditraw') or 0)).quantize(Decimal('0.00'))
    except (TypeError, ValueError, InvalidOperation):
        pass
    digits = ''.join(ch for ch in str(info.get('credit') or '') if ch.isdigit() or ch in '.,-')
    if not digits:
        return Decimal('0.00')
    try:
        return Decimal(digits.replace(',', '.')).quantize(Decimal('0.00'))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal('0.00')


def _is_charged(order):
    """Pedido efetivamente cobrado pelo provedor (foi enviado / está em andamento)."""
    return bool(order.trx_id) or order.service_status in ('Success', 'In Process')


def _parse_int(value):
    try:
        return int(str(value or '').strip())
    except (TypeError, ValueError):
        return None


def _asaas_decimal(value):
    if value in (None, ''):
        return Decimal('0.00')
    normalized = str(value).replace(',', '.')
    digits = ''.join(ch for ch in normalized if ch.isdigit() or ch == '.')
    try:
        return Decimal(digits).quantize(Decimal('0.01'))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal('0.00')


def _ip_auth_hint(exc):
    """Quando o provedor rejeita pelo IP do servidor, devolve uma orientação clara.

    Provedores Dhr (gmsreseller etc.) bloqueiam chamadas fora do IP registrado
    no campo 'API IP' da conta — que captura o primeiro IP válido automaticamente.
    """
    text = str(exc or '')
    lowered = text.lower()
    if any(k in lowered for k in ('not authorized', 'is not authorized',
                                  'server\'s ip', 'server ip', 'ip address',
                                  'add or reset your server')):
        return ('IP do servidor não autorizado no provedor. Entre na aba "API Access" '
                'da sua conta no site do provedor e deixe o campo "API IP" capturar o IP '
                'do servidor (ou adicione/resete o IP para o IP da VPS); salve e clique '
                'em "Testar conexão" novamente.')
    return None


@_staff
def admin_dashboard(request):
    period, since = _dashboard_period(request)
    orders_qs = (CustomerOrder.objects
                 .select_related('customer', 'service', 'service__api')
                 .prefetch_related('statements'))
    if since is not None:
        orders_qs = orders_qs.filter(created_at__gte=since)
    orders = list(orders_qs)

    revenue = Decimal('0.00')      # vendas pagas pelos clientes (não-estornadas)
    api_cost = Decimal('0.00')     # gasto com as APIs (crédito x câmbio)
    credits_spent = Decimal('0.00')  # créditos consumidos nas APIs
    refunded = Decimal('0.00')     # reembolsos/estornos devolvidos a clientes
    direct_count = 0
    status_counts = dict.fromkeys(SERVICE_STATUS_CHOICES, 0)
    api_rows = {}
    tools = {}

    for order in orders:
        status_counts[order.service_status] = status_counts.get(order.service_status, 0) + 1
        is_direct = _is_admin_direct(order)
        if is_direct:
            direct_count += 1
        if _is_charged(order):
            cost = _order_cost(order, is_direct)
            api_cost += cost
            credits_spent += _order_credits(order)
            service = order.service
            if service and service.api_id and (service.referenceid or '').strip():
                row = api_rows.setdefault(service.api_id, {
                    'api': service.api, 'orders': 0, 'cost': Decimal('0.00'),
                    'credits': Decimal('0.00'), 'revenue': Decimal('0.00'),
                })
                row['orders'] += 1
                row['cost'] += cost
                row['credits'] += _order_credits(order)
                if not is_direct and order.service_status != 'Rejected':
                    row['revenue'] += order.service_price or Decimal('0.00')
        if not is_direct:
            if order.service_status != 'Rejected':
                revenue += order.service_price or Decimal('0.00')
            else:
                refunded += order.service_price or Decimal('0.00')
        if order.service_status == 'Success' and order.service_id:
            tool = tools.setdefault(order.service_id, {
                'service': order.service,
                'title': order.service_title or (order.service.title if order.service else ''),
                'qty': 0, 'revenue': Decimal('0.00'), 'cost': Decimal('0.00'),
            })
            tool['qty'] += 1
            tool['revenue'] += order.service_price or Decimal('0.00')
            if _is_charged(order):
                tool['cost'] += _order_cost(order, is_direct)

    top_tools = sorted(tools.values(), key=lambda t: t['qty'], reverse=True)[:8]
    max_tool_qty = max((t['qty'] for t in top_tools), default=0) or 1
    for t in top_tools:
        t['profit'] = t['revenue'] - t['cost']
        t['width'] = round((t['qty'] / max_tool_qty) * 100)

    invoices_qs = Invoice.objects.filter(invoice_for='Deposit', invoice_status='Paid')
    if since is not None:
        invoices_qs = invoices_qs.filter(created_at__gte=since)
    deposits = invoices_qs.aggregate(total=Sum('invoice_amount'))['total'] or Decimal('0.00')

    dep_qs = (PaymentDeposit.objects
              .filter(status='Paid')
              .select_related('invoice', 'invoice__customer'))
    if since is not None:
        dep_qs = dep_qs.filter(created_at__gte=since)
    deposit_flow = []
    deposit_credited = Decimal('0.00')   # valor creditado ao saldo do cliente
    deposit_net = Decimal('0.00')        # valor líquido que consta na conta do gateway
    deposit_fee = Decimal('0.00')        # taxa cobrada pelo gateway
    for dep in dep_qs.order_by('-id'):
        credited = (dep.invoice.invoice_amount if dep.invoice else dep.gateway_amount) or Decimal('0.00')
        if dep.net_amount is not None:
            net = dep.net_amount
            fee = dep.gateway_fee or Decimal('0.00')
        else:
            # Depósitos antigos sem registro de taxa: considera recebido = creditado.
            net = dep.gateway_amount or credited
            fee = Decimal('0.00')
        deposit_credited += credited
        deposit_net += net
        deposit_fee += fee
        deposit_flow.append({
            'deposit': dep,
            'customer': (dep.invoice.customer if dep.invoice else (dep.order.customer if dep.order else None)),
            'credited': credited,
            'net': net,
            'fee': fee,
            'profit': (net - credited).quantize(Decimal('0.00')),
        })
    deposit_profit = (deposit_net - deposit_credited).quantize(Decimal('0.00'))

    asaas_balance = None
    asaas_balance_error = None
    asaas_gw = PaymentGateway.objects.filter(name__iexact='Asaas', status='Active').first()
    if asaas_gw and (asaas_gw.asaas_api_key or '').strip():
        try:
            raw = asaas.get_balance(asaas_gw)
            if isinstance(raw, dict):
                fallback = raw.get('balance')
                asaas_balance = {
                    'total': _asaas_decimal(raw.get('balance')),
                    'available': _asaas_decimal(raw.get('availableBalance', fallback)),
                }
        except Exception as exc:  # noqa: BLE001 - saldo é opcional no painel
            asaas_balance_error = str(exc)

    profit = revenue - api_cost - refunded

    api_balances = []
    for api in Api.objects.filter(status='Active').order_by('api_name'):
        info = None
        error = None
        try:
            info = provider_api.cached_api_balance(api)
        except provider_api.ProviderError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - saldo é opcional no painel
            error = str(exc)
        api_balances.append({
            'api': api,
            'info': info,
            'balance': _api_credit_decimal(info),
            'error': error,
        })

    api_summary = {}
    for live in api_balances:
        entry = {
            'api': live['api'],
            'orders': 0, 'cost': Decimal('0.00'), 'credits': Decimal('0.00'),
            'revenue': Decimal('0.00'), 'profit': Decimal('0.00'),
            'balance': live['balance'], 'currency': (live['info'] or {}).get('currency', ''),
            'mail': (live['info'] or {}).get('mail', ''),
            'balance_error': live['error'],
        }
        api_summary[live['api'].id] = entry
    for aid, row in api_rows.items():
        entry = api_summary.setdefault(aid, {
            'api': row['api'], 'orders': 0, 'cost': Decimal('0.00'), 'credits': Decimal('0.00'),
            'revenue': Decimal('0.00'), 'profit': Decimal('0.00'),
            'balance': Decimal('0.00'), 'currency': '', 'mail': '', 'balance_error': 'API inativa',
        })
        entry['orders'] = row['orders']
        entry['cost'] = row['cost']
        entry['credits'] = row['credits']
        entry['revenue'] = row['revenue']
        entry['profit'] = row['revenue'] - row['cost']

    admin_flow = []
    admin_spent = Decimal('0.00')
    admin_services = {}
    for order in orders:
        if not _is_admin_direct(order):
            continue
        spent = order.service_price or Decimal('0.00')
        admin_spent += spent
        admin_services.setdefault(order.service_id, {
            'id': order.service_id,
            'title': order.service_title or 'Serviço removido',
        })
        admin_flow.append({
            'order': order,
            'who': _order_creator_display(request, order),
            'spent': spent,
            'result': order.service_comments or order.replied_in or '-',
        })
    admin_services = sorted(admin_services.values(), key=lambda s: s['title'].lower())
    admin_svc_filter = _parse_int(request.GET.get('svc'))
    admin_flow_all = admin_flow
    if admin_svc_filter:
        admin_flow = [a for a in admin_flow if a['order'].service_id == admin_svc_filter]
    admin_spent_filtered = sum((a['spent'] for a in admin_flow), Decimal('0.00'))
    admin_count_filtered = len(admin_flow)

    flow = []
    for order in orders[:15]:
        is_direct = _is_admin_direct(order)
        cost = _order_cost(order, is_direct) if _is_charged(order) else Decimal('0.00')
        sale = order.service_price if (order.service_status != 'Rejected' and not is_direct) else Decimal('0.00')
        flow.append({
            'order': order,
            'direct': is_direct,
            'cost': cost,
            'sale': sale,
            'profit': sale - cost,
        })

    ctx = {
        'period': period,
        'periods': AVAILABLE_PERIODS,
        'total_customers': Customer.objects.count(),
        'total_services': ServiceList.objects.count(),
        'total_invoices': Invoice.objects.count(),
        'status_counts': status_counts,
        'count_waiting': status_counts['Waiting Action'],
        'count_in_process': status_counts['In Process'],
        'count_success': status_counts['Success'],
        'count_rejected': status_counts['Rejected'],
        'revenue': revenue,
        'api_cost': api_cost,
        'credits_spent': credits_spent,
        'refunded': refunded,
        'deposits': deposits,
        'deposit_credited': deposit_credited.quantize(Decimal('0.00')),
        'deposit_net': deposit_net.quantize(Decimal('0.00')),
        'deposit_fee': deposit_fee.quantize(Decimal('0.00')),
        'deposit_profit': deposit_profit,
        'deposit_flow': deposit_flow[:15],
        'asaas_balance': asaas_balance,
        'asaas_balance_error': asaas_balance_error,
        'profit': profit,
        'direct_count': direct_count,
        'admin_flow': admin_flow,
        'admin_spent': admin_spent.quantize(Decimal('0.00')),
        'admin_services': admin_services,
        'admin_svc_filter': admin_svc_filter or '',
        'admin_spent_filtered': admin_spent_filtered.quantize(Decimal('0.00')),
        'admin_count_filtered': admin_count_filtered,
        'admin_count_total': len(admin_flow_all),
        'api_summary': [api_summary[a] for a in api_summary],
        'api_balances': api_balances,
        'top_tools': top_tools,
        'flow': flow,
    }
    return render(request, 'admin/dashboard.html', ctx)


@_staff
def admin_orders(request, status):
    db_status, label = STATUS_MAP.get(status, ('Waiting Action', 'Aguardando Ação'))
    orders = list(CustomerOrder.objects.filter(service_status=db_status)
                  .select_related('customer', 'service__inventory')
                  .prefetch_related('order_inputs'))
    CustomerOrder.objects.filter(service_status=db_status, seen='false').update(seen='true')
    inv_ids = {o.service.inventory_id for o in orders if o.service and o.service.inventory_id}
    avail = dict(
        InventoryData.objects.filter(inventory_id__in=inv_ids, status='Available')
        .values('inventory_id').annotate(c=Count('id')).values_list('inventory_id', 'c')
    )
    for order in orders:
        inv = order.service.inventory if order.service else None
        order.inventory_id_for_delivery = inv.id if inv else None
        order.available_count = avail.get(inv.id, 0) if inv else 0
    ctx = {
        'status': status,
        'status_label': label,
        'orders': orders,
        'status_choices': SERVICE_STATUS_CHOICES,
    }
    return render(request, 'admin/orders.html', ctx)


@_staff
def admin_orders_unseen(request):
    qs = CustomerOrder.objects.filter(seen='false').select_related('customer').order_by('-id')
    return JsonResponse({
        'count': qs.count(),
        'orders': [{
            'id': o.id,
            'customer': o.customer.name,
            'service': o.service_title,
            'price': '{}'.format(o.service_price),
            'status': o.service_status,
        } for o in qs[:5]],
    })


@_staff
def admin_order_update(request, order_id):
    order = CustomerOrder.objects.filter(id=order_id).first()
    if order and request.method == 'POST':
        new_status = request.POST.get('service_status')
        was_success = order.service_status == 'Success'
        if new_status in SERVICE_STATUS_CHOICES:
            order.service_status = new_status
        order.replied_in = request.POST.get('replied_in', '') or order.replied_in
        order.save()
        if order.service_status == 'Success' and not was_success:
            from . import notify
            notify.send_order_email(order)
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
        'admin_usernames': set(User.objects.filter(is_staff=True).values_list('username', flat=True)),
    })


@_staff
def admin_customer_promote(request, customer_id):
    customer = Customer.objects.filter(id=customer_id).first()
    if not customer:
        messages.error(request, 'Cliente não encontrado.')
        return redirect('admin_customer_list')
    if request.method == 'POST':
        username = (customer.email or '').strip().lower() or 'admin_{}'.format(customer.id)
        user = User.objects.filter(username=username).first() or User.objects.filter(email=customer.email).first()
        if user and user.is_staff and user.is_superuser:
            messages.info(request, '"{}" já é administrador.'.format(customer.name))
        else:
            if not user:
                user = User(username=username, email=customer.email or username, password=customer.password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            messages.success(request, '"{}" agora é administrador — entra no painel com o mesmo e-mail/senha do site.'.format(customer.name))
    return redirect('admin_customer_list')


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
        changed_pass = False
        if post.get('new_password'):
            new_password = post['new_password']
            if len(new_password) < 6:
                messages.error(request, 'A nova senha deve ter pelo menos 6 caracteres.')
                return redirect('admin_customer_list')
            customer.password = Customer.make_password(new_password)
            changed_pass = True
        customer.save()
        if post.get('promote_to_admin') == '1':
            username = (customer.email or '').strip().lower() or 'admin_{}'.format(customer.id)
            user = User.objects.filter(username=username).first() or User.objects.filter(email=customer.email).first()
            if user and user.is_staff and user.is_superuser:
                messages.info(request, '"{}" já é administrador.'.format(customer.name))
            else:
                if not user:
                    user = User(username=username, email=customer.email or username, password=customer.password)
                user.is_staff = True
                user.is_superuser = True
                user.save()
                messages.info(request, '"{}" agora é administrador — entra no painel com o mesmo e-mail/senha do site.'.format(customer.name))
        if changed_pass:
            messages.info(request, 'Senha de {} alterada com sucesso.'.format(customer.email))
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
        'services': ServiceList.objects.filter(service_type=db_type).select_related('api'),
        'type_options': [(k, TYPE_MAP[k][1]) for k in TYPE_MAP],
    }
    return render(request, 'admin/service_list.html', ctx)


@_staff
def admin_administrator(request):
    """Pedido direto na API do provedor, pelo valor fornecido por ela.

    Refaz o pedido de um cliente sem debitar saldo e sem precisar
    entrar no site do provedor."""
    from .views import _invoice_type_key, _service_input_fields

    def _parse_int(value):
        try:
            return int(str(value or '').strip())
        except (TypeError, ValueError):
            return None

    service_id = request.GET.get('service') or request.POST.get('serviceID') or ''
    service = ServiceList.objects.select_related('api').filter(id=_parse_int(service_id)).first()

    order_input_id = request.GET.get('order') or ''
    pref_order = CustomerOrder.objects.select_related(
        'customer', 'service', 'service__api',
    ).filter(id=_parse_int(order_input_id)).first()
    pref_inputs = {}
    if pref_order:
        pref_inputs = {i.field_name: i.field_value for i in pref_order.order_inputs.all()}
        service = pref_order.service

    customer_q = (request.GET.get('customer_q') or '').strip()
    customers = Customer.objects.filter(status='Active')
    if customer_q:
        customers = customers.filter(Q(name__icontains=customer_q) | Q(email__icontains=customer_q))
    customers = customers.order_by('name')[:50]

    services = ServiceList.objects.filter(
        status='Active', api__isnull=False, api__status='Active',
    ).select_related('api').order_by('title')

    api = service.api if service else None
    api_price = None
    api_balance = None
    api_balance_error = ''
    if api:
        remote = RemoteServiceList.objects.filter(
            api=api, referenceid=(service.referenceid or '').strip(),
        ).first()
        api_price = remote.CREDIT if remote and remote.CREDIT else api.reseller_price
        try:
            info = provider_api.cached_api_balance(api)
            digits = ''.join(ch for ch in str(info.get('credit') or '') if ch.isdigit() or ch in '.,-')
            api_balance = None
            if digits:
                try:
                    api_balance = float(digits.replace(',', '.'))
                except (TypeError, ValueError):
                    api_balance = None
            if api_balance is None:
                try:
                    api_balance = float(info.get('creditraw') or 0)
                except (TypeError, ValueError):
                    api_balance = 0.0
        except Exception as exc:
            api_balance_error = str(exc)

    input_objects = []
    if service:
        for name in _service_input_fields(service):
            is_qnt = 'quantidade' in name.lower() or name.lower().startswith(('qtd', 'qty', 'qnt'))
            input_objects.append({'name': name, 'value': pref_inputs.get(name, ''), 'is_qnt': is_qnt})

    api_ready = bool(service) and provider_api.provider_for_order(CustomerOrder(service=service)) is not None
    last_order = None
    last_id = _parse_int(request.session.get('last_admin_order'))
    if last_id:
        last_order = CustomerOrder.objects.select_related('customer').filter(id=last_id).first()
        if last_order is None:
            request.session.pop('last_admin_order', None)

    if request.method == 'POST':
        if provider_api.maintenance_active():
            messages.error(request, 'Site em manutenção. Pedidos na API pausados — desative a manutenção para enviar.')
            return redirect('admin_administrator')
        errors = []
        customer = Customer.objects.filter(id=_parse_int(request.POST.get('customerID'))).first()
        if customer is None:
            errors.append('Selecione o cliente.')
        elif customer.status != 'Active':
            errors.append('Cliente inativo.')
        if service is None or service.api is None or not (service.referenceid or '').strip():
            errors.append('Selecione um servico vinculado a uma API com ID do produto.')
        elif not provider_api.provider_for_order(CustomerOrder(service=service)):
            errors.append('Servico nao esta habilitado para API automatica: marque API habilitada e preencha o ID do produto no cadastro do servico.')
        fields = {}
        qnt = 1
        if customer and service:
            for obj in input_objects:
                name = obj['name']
                if obj['is_qnt']:
                    qnt = _parse_int(request.POST.get(name)) or 1
                    if qnt < 1:
                        qnt = 1
                    continue
                value = request.POST.get(name, '').strip()
                if not value:
                    errors.append('Informe {}.'.format(name))
                else:
                    fields[name] = value
        stock_err = _check_stock_order_access(request, service)
        if stock_err:
            errors.append(stock_err)
        if errors:
            for msg in errors:
                messages.error(request, msg)
            if pref_order:
                return redirect(reverse('admin_administrator') + '?order={}'.format(pref_order.id))
            if service:
                return redirect(reverse('admin_administrator') + '?service={}'.format(service.id))
            return redirect('admin_administrator')

        cost = (api_price or service.original_price) * qnt
        order = CustomerOrder.objects.create(
            customer=customer,
            created_by=request.user if getattr(request.user, 'is_authenticated', False) else None,
            service=service,
            service_status='In Process',
            service_type=_invoice_type_key(service.service_type),
            service_price=cost,
            service_qnt=str(qnt),
            service_title=service.title,
            process_type='Auto',
        )
        for name, value in fields.items():
            OrderInput.objects.create(order=order, field_name=name, field_value=value)
            if not order.service_input1:
                order.service_input1 = value
                order.save(update_fields=['service_input1'])

        forwarded, msg = provider_api.submit_local_order(order)
        order.refresh_from_db()
        if forwarded is False:
            order.service_status = 'Rejected'
            order.service_comments = msg or 'Falha ao enviar para a API.'
            order.save(update_fields=['service_status', 'service_comments'])
            messages.error(request, 'Falha no pedido #{}: {}'.format(order.id, order.service_comments))
        elif forwarded is True:
            provider_api.sync_local_order(order, notify_complete=True)
            order.refresh_from_db()
            result = order.replied_in or order.service_comments or '-'
            messages.success(request, 'Pedido #{} enviado a API (ref: {}). Status: {}. Resultado: {}'.format(
                order.id, order.trx_id or '-', order.service_status, result))
        else:
            order.service_status = 'Waiting Action'
            order.process_type = 'Manual'
            order.save(update_fields=['service_status', 'process_type'])
            messages.warning(request, 'Pedido #{} criado, mas o servico nao e automatico. Edite manualmente.'.format(order.id))
        request.session['last_admin_order'] = order.id
        notify.send_telegram(notify.new_order_message(order, paid=True))
        notify.send_new_order_email(order, paid=True)
        if pref_order:
            return redirect('admin_administrator')
        return redirect(reverse('admin_administrator') + '?service={}'.format(service.id))

    ctx = {
        'service': service,
        'services': services,
        'api': api,
        'api_price': api_price,
        'api_balance': api_balance,
        'api_balance_error': api_balance_error,
        'customers': customers,
        'customer_q': customer_q,
        'input_objects': input_objects,
        'pref_order': pref_order,
        'order_input_id': order_input_id,
        'api_ready': api_ready,
        'last_order': last_order,
    }
    return render(request, 'admin/administrator.html', ctx)

@_staff
def admin_direct_order(request):
    """Pedido direto na API do provedor, exclusivo do painel administrativo.

    Area simplificada: o administrador logado envia o pedido direto na API do
    provedor (sem refazer pedido de cliente) e acompanha o status das
    solicitacoes. O pedido fica registrado no cadastro do proprio admin."""
    from .views import _invoice_type_key, _service_input_fields

    def _parse_int(value):
        try:
            return int(str(value or '').strip())
        except (TypeError, ValueError):
            return None

    admin_customer, _ = Customer.objects.get_or_create(
        email=request.user.email or '',
        defaults={
            'name': request.user.get_full_name() or request.user.username or 'Administrador',
            'password': Customer.make_password(uuid.uuid4().hex),
            'currency': 'BRL',
            'role': 'Web Owner',
            'api_allow': 'off',
        },
    )

    q = (request.GET.get('q') or '').strip()
    api_filter = _parse_int(request.GET.get('api'))
    services = ServiceList.objects.filter(
        status='Active', api__isnull=False, api__status='Active',
    ).select_related('api').order_by('title')
    if q:
        services = services.filter(title__icontains=q)
    if api_filter:
        services = services.filter(api_id=api_filter)
    services = services[:100]

    api_balances = []
    for api in Api.objects.filter(status='Active').order_by('api_name'):
        info = None
        error = None
        try:
            info = provider_api.cached_api_balance(api)
        except Exception as exc:  # noqa: BLE001 - saldo é opcional no painel
            error = str(exc)
        api_balances.append({
            'api': api,
            'balance': _api_credit_decimal(info),
            'error': error,
            'currency': (info or {}).get('currency', ''),
        })

    service_id = request.POST.get('serviceID') or request.GET.get('service') or ''
    service = ServiceList.objects.select_related('api').filter(id=_parse_int(service_id)).first()

    api_price = None
    if service:
        remote = RemoteServiceList.objects.filter(
            api=service.api, referenceid=(service.referenceid or '').strip(),
        ).first()
        api_price = remote.CREDIT if remote and remote.CREDIT else service.api.reseller_price

    input_objects = []
    if service:
        for name in _service_input_fields(service):
            is_qnt = 'quantidade' in name.lower() or name.lower().startswith(('qtd', 'qty', 'qnt'))
            input_objects.append({'name': name, 'value': '', 'is_qnt': is_qnt})

    if request.method == 'POST':
        if provider_api.maintenance_active():
            messages.error(request, 'Site em manutenção. Pedidos na API pausados — desative a manutenção para enviar.')
            return redirect('admin_direct_order')
        errors = []
        if service is None or service.api_id is None or not (service.referenceid or '').strip():
            errors.append('Selecione um serviço vinculado a uma API com ID do produto.')
        elif not provider_api.provider_for_order(CustomerOrder(service=service)):
            errors.append('Serviço não habilitado para API automática: marque API habilitada e preencha o ID do produto no cadastro do serviço.')
        fields = {}
        qnt = 1
        if service:
            for obj in input_objects:
                name = obj['name']
                if obj['is_qnt']:
                    qnt = _parse_int(request.POST.get(name)) or 1
                    if qnt < 1:
                        qnt = 1
                    continue
                value = request.POST.get(name, '').strip()
                if not value:
                    errors.append('Informe {}.'.format(name))
                else:
                    fields[name] = value
        stock_err = _check_stock_order_access(request, service)
        if stock_err:
            errors.append(stock_err)
        if errors:
            for msg in errors:
                messages.error(request, msg)
            return redirect(reverse('admin_direct_order') + '?service={}'.format(service.id if service else ''))
        if admin_customer.status != 'Active':
            admin_customer.status = 'Active'
            admin_customer.save(update_fields=['status'])

        cost = (api_price or service.original_price) * qnt
        order = CustomerOrder.objects.create(
            customer=admin_customer,
            created_by=request.user if getattr(request.user, 'is_authenticated', False) else None,
            service=service,
            service_status='In Process',
            service_type=_invoice_type_key(service.service_type),
            service_price=cost,
            service_qnt=str(qnt),
            service_title=service.title,
            process_type='Auto',
        )
        for name, value in fields.items():
            OrderInput.objects.create(order=order, field_name=name, field_value=value)
            if not order.service_input1:
                order.service_input1 = value
                order.save(update_fields=['service_input1'])

        forwarded, msg = provider_api.submit_local_order(order)
        order.refresh_from_db()
        if forwarded is False:
            order.service_status = 'Rejected'
            order.service_comments = msg or 'Falha ao enviar para a API.'
            order.save(update_fields=['service_status', 'service_comments'])
            messages.error(request, 'Falha no pedido #{}: {}'.format(order.id, order.service_comments))
        elif forwarded is True:
            provider_api.sync_local_order(order, notify_complete=True)
            order.refresh_from_db()
            result = order.replied_in or order.service_comments or '-'
            messages.success(request, 'Pedido #{} enviado a API (ref: {}). Status: {}. Resultado: {}'.format(
                order.id, order.trx_id or '-', order.service_status, result))
        else:
            order.service_status = 'Waiting Action'
            order.process_type = 'Manual'
            order.save(update_fields=['service_status', 'process_type'])
            messages.warning(request, 'Pedido #{} criado, mas o serviço não é automático. Edite manualmente.'.format(order.id))
        notify.send_telegram(notify.new_order_message(order, paid=True))
        notify.send_new_order_email(order, paid=True)
        return redirect(reverse('admin_direct_order') + '?service={}'.format(service.id if service else ''))

    api_ready = bool(service) and provider_api.provider_for_order(CustomerOrder(service=service)) is not None

    history = (CustomerOrder.objects
               .filter(customer=admin_customer)
               .select_related('service')
               .order_by('-id')[:15])

    ctx = {
        'admin_customer': admin_customer,
        'q': q,
        'api_filter': api_filter,
        'services': services,
        'api_balances': api_balances,
        'service': service,
        'api_price': api_price,
        'input_objects': input_objects,
        'api_ready': api_ready,
        'history': history,
    }
    return render(request, 'admin/direct_order.html', ctx)


@_staff
def admin_setting(request):
    if not _settings_access_owner(request.user):
        return _deny_settings_access(request)
    unlock_password = request.POST.get('unlock_settings_password')
    if request.method == 'POST' and unlock_password is not None:
        expected = SystemSetting.get(SETTINGS_ACCESS_PASSWORD_KEY, '').strip()
        if expected and (unlock_password or '').strip() == expected:
            request.session[SETTINGS_UNLOCK_SESSION] = True
            messages.success(request, 'Configurações desbloqueadas.')
        else:
            messages.error(request, 'Senha de acesso às Configurações incorreta.')
        return redirect('admin_setting')
    if _settings_locked(request):
        return render(request, 'admin/setting_unlock.html', {})
    keys = [
        'siteTitle', 'siteMetaTitle', 'siteMetaDes', 'siteKeyword', 'siteLogo', 'siteFav',
        'siteEmailAddress', 'sitePhoneNumber', 'siteAddress',
        'siteWhatsappUrl', 'siteTelegramUrl', 'siteFacebookUrl', 'siteTwitterUrl',
        'tgBotToken', 'tgChatId',
        'mailHost', 'mailPort', 'mailUser', 'mailPass', 'mailFrom', 'mailFromName', 'mailUseTls',
        'orderNotifyTo', 'orderReplyCopyTo',
    ]
    can_stock_password = _is_stock_access_admin(request.user)
    setting_keys = list(keys)
    if can_stock_password:
        setting_keys.append(STOCK_ACCESS_PASSWORD_KEY)
        setting_keys.append(SETTINGS_ACCESS_PASSWORD_KEY)
    settings = {k: SystemSetting.get(k, '') for k in setting_keys}
    if request.method == 'POST':
        for k in setting_keys:
            obj, _ = SystemSetting.objects.get_or_create(key=k, defaults={'value': ''})
            obj.value = request.POST.get(k, '')
            obj.save()
        request.session[SETTINGS_UNLOCK_SESSION] = True
        messages.success(request, 'Configurações salvas com sucesso.')
        return redirect('admin_setting')
    return render(request, 'admin/setting.html', {
        'settings': settings,
        'can_stock_password': can_stock_password,
    })


@_staff
def admin_setting_upload_image(request, kind):
    if not _settings_access_owner(request.user):
        return JsonResponse({'error': 'Acesso restrito às Configurações.'}, status=403)
    if _settings_locked(request):
        return JsonResponse({'error': 'Seção Configurações bloqueada. Desbloqueie com a senha primeiro.'}, status=403)
    if kind not in ('logo', 'favicon'):
        return JsonResponse({'error': 'Tipo inválido.'}, status=400)
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'error': 'Envie um arquivo de imagem.'}, status=400)
    f = request.FILES['image']
    name = (f.name or '').lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext not in ('png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'):
        return JsonResponse({'error': 'Formato não permitido (use PNG, JPG, WEBP, GIF ou BMP).'}, status=400)
    if kind == 'favicon' and ext not in ('png', 'ico', 'jpg', 'jpeg', 'webp'):
        return JsonResponse({'error': 'Favicon: use PNG, ICO, JPG ou WEBP.'}, status=400)
    subdir = settings.MEDIA_ROOT / 'settings' / kind
    subdir.mkdir(parents=True, exist_ok=True)
    fname = "{}_{}.{}".format(kind, uuid.uuid4().hex[:10], ext)
    dest = subdir / fname
    with open(dest, 'wb+') as out:
        for chunk in f.chunks():
            out.write(chunk)
    return JsonResponse({'url': '{0}settings/{1}/{2}'.format(settings.MEDIA_URL, kind, fname)})


@_staff
def admin_maintenance(request):
    on = str(SystemSetting.get('siteMaintenanceMode', 'off')).strip().lower() in ('1', 'true', 'yes', 'on')
    message = SystemSetting.get('siteMaintenanceMsg', '')
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action in ('on', 'off'):
            setting, _ = SystemSetting.objects.get_or_create(key='siteMaintenanceMode', defaults={'value': 'off'})
            setting.value = action
            setting.save(update_fields=['value'])
            if action == 'on':
                msg, _ = SystemSetting.objects.get_or_create(key='siteMaintenanceMsg', defaults={'value': ''})
                msg.value = request.POST.get('message', '').strip()
                msg.save(update_fields=['value'])
                messages.success(request, 'Site em manutenção ATIVADO. Página pública mostra a manutenção.')
            else:
                messages.success(request, 'Site em manutenção DESATIVADO. Site público normal.')
        return redirect('admin_maintenance')
    return render(request, 'admin/maintenance.html', {
        'maintenance_on': on,
        'maintenance_msg': message,
    })


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


def _wa_contacts_to_raw(contacts):
    return ','.join('{}|{}'.format(c['name'], c['number']) for c in contacts)


@_staff
def admin_support_whatsapp(request):
    """Numeros e nomes de atendentes exibidos como botao flutuante de WhatsApp na area do cliente."""
    if request.method == 'POST':
        def _digits(value):
            return ''.join(ch for ch in (value or '') if ch.isdigit())

        contacts = []
        for key in ('number', 'number2'):
            digits = _digits(request.POST.get(key))
            if not digits or digits in [c['number'] for c in contacts]:
                continue
            name = (request.POST.get(key.replace('number', 'name')) or '').strip()
            contacts.append({'name': name, 'number': digits})
        setting, _ = SystemSetting.objects.get_or_create(key='siteWhatsappNumber', defaults={'value': ''})
        if contacts:
            setting.value = _wa_contacts_to_raw(contacts)
            setting.save(update_fields=['value'])
            links = ' | '.join('{} <https://wa.me/{}>'.format(c['name'] or c['number'], c['number']) for c in contacts)
            messages.success(request, 'Contato(s) salvo(s). {}'.format(links))
        else:
            setting.value = ''
            setting.save(update_fields=['value'])
            messages.warning(request, 'Números vazios — botão de WhatsApp ocultado do site.')
        return redirect('admin_support_whatsapp')
    current = SystemSetting.get('siteWhatsappNumber', '')
    contacts = _parse_wa_contacts(current)
    return render(request, 'admin/support_whatsapp.html', {
        'wa_number': current,
        'wa_contacts': contacts,
        'wa_links': ['https://wa.me/{}'.format(c['number']) for c in contacts],
    })


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


def _remote_ref_options():
    """Opções (referência, nome do produto no provedor, API) para o autocomplete
    do campo 'ID do Produto' no cadastro do serviço."""
    return [
        (r.referenceid, r.SERVICENAME or '', r.api.api_name)
        for r in RemoteServiceList.objects.select_related('api')
        .filter(api__status='Active').order_by('api_id', 'SERVICENAME')
    ]


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
    api_value = post.get('api')
    if api_value is not None:
        if api_value in ('', '0'):
            service.api = None
            service.referenceid = ''
            service.api_enabled = False
        else:
            service.api = Api.objects.filter(id=api_value).first()
            service.referenceid = (post.get('referenceid') or '').strip()
            service.api_enabled = bool(post.get('api_enabled'))
    else:
        service.api_enabled = bool(post.get('api_enabled'))
    service.collect_data = ','.join(
        c for c in post.getlist('collect_data') if c in dict(SERVICE_COLLECT_DATA_CHOICES)
    )
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
    # Campos equivalentes aos dados do painel (ex.: 'E-mail', 'Senha') só ficam
    # salvos quando a checkbox correspondente está marcada; caso contrário são
    # removidos para a compra respeitar a regra configurada.
    checked = set(collect_data_codes(service.collect_data))
    for si in service.service_fields.all():
        code = collect_field_code_by_name(si.name)
        if code is not None and code not in checked:
            si.delete()


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


def _save_uploaded_screenshot(service, files, request=None):
    """Salva a imagem enviada em MEDIA_ROOT/screenshots, ajustada ao tamanho de
    exibição (máx. 460px de altura preservando a proporção), e atualiza o campo."""
    img = files.get('screenshot_image')
    if not img:
        return
    ext = os.path.splitext(img.name)[1].lower() or '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        if request:
            messages.warning(request, 'Formato de imagem não suportado (use JPG, PNG, WEBP ou GIF).')
        return
    folder = Path(settings.MEDIA_ROOT) / 'screenshots'
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{service.slug or 'service'}-{uuid.uuid4().hex[:8]}{ext}"
    dest = folder / name
    try:
        from PIL import Image
        probe = Image.open(img)
        probe.verify()
        img.seek(0)
    except Exception:
        if request:
            messages.warning(request, 'Arquivo não é uma imagem válida.')
        return
    if ext != '.gif':
        try:
            image = Image.open(img)
            image.thumbnail((1024, 460), Image.Resampling.LANCZOS)
            image.save(dest, optimize=True)
        except Exception:
            with open(dest, 'wb+') as raw:
                for chunk in img.chunks():
                    raw.write(chunk)
    else:
        with open(dest, 'wb+') as raw:
            for chunk in img.chunks():
                raw.write(chunk)
    service.screenshot = f"{settings.MEDIA_URL}screenshots/{name}"
    service.save(update_fields=['screenshot'])


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
        _save_uploaded_screenshot(service, request.FILES, request)
        auto_remote, auto_score = provider_api.auto_link_service(
            service, allow_assign=str(request.POST.get('api') or '') not in ('', '0'))
        if auto_remote:
            messages.success(request, 'Serviço criado e integrado automaticamente ao provedor: "{}" (referência {}).'.format(
                auto_remote.SERVICENAME, auto_remote.referenceid))
        else:
            messages.success(request, 'Serviço criado com sucesso.')
        return redirect('admin_service_list', svtype)
    return render(request, 'admin/service_form.html', {
        'service': None,
        'svtype': svtype,
        'type_label': label,
        'service_fields': [],
        'inventories': Inventory.objects.all().order_by('name'),
        'apis': Api.objects.filter(status='Active').order_by('api_name'),
        'api_ref_options': _remote_ref_options(),
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
        _save_uploaded_screenshot(service, request.FILES, request)
        auto_remote, auto_score = provider_api.auto_link_service(
            service, allow_assign=str(request.POST.get('api') or '') not in ('', '0'))
        if auto_remote:
            messages.success(request, 'Serviço atualizado e integrado automaticamente ao provedor: "{}" (referência {}).'.format(
                auto_remote.SERVICENAME, auto_remote.referenceid))
        else:
            messages.success(request, 'Serviço atualizado com sucesso.')
        return redirect('admin_service_list', svtype)
    return render(request, 'admin/service_form.html', {
        'service': service,
        'svtype': svtype,
        'type_label': label,
        'service_fields': service.service_fields.all(),
        'inventories': Inventory.objects.all().order_by('name'),
        'apis': Api.objects.filter(status='Active').order_by('api_name'),
        'api_ref_options': _remote_ref_options(),
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
def admin_service_toggle_login(request, svtype, service_id):
    db_type, _label = _service_type_from(svtype)
    service = ServiceList.objects.filter(id=service_id, service_type=db_type).first()
    if not service:
        messages.error(request, 'Serviço não encontrado.')
        return redirect('admin_service_list', svtype)
    if request.method == 'POST':
        codes = collect_data_codes(service.collect_data)
        has_login = 'user' in codes or 'email' in codes
        if has_login:
            service.collect_data = ','.join(c for c in codes if c not in ('user', 'email'))
        else:
            service.collect_data = ','.join(codes + ['user', 'email'])
        service.save(update_fields=['collect_data'])
        if has_login:
            messages.warning(request, f'"{service.title}" não pede mais usuário/e-mail do cliente na compra.')
        else:
            messages.success(request, f'"{service.title}" agora pede usuário/e-mail do cliente na compra.')
    return redirect('admin_service_list', svtype)


@_staff
def admin_service_set_fields(request, svtype, service_id):
    db_type, _label = _service_type_from(svtype)
    service = ServiceList.objects.filter(id=service_id, service_type=db_type).first()
    if not service:
        messages.error(request, 'Serviço não encontrado.')
        return redirect('admin_service_list', svtype)
    if request.method == 'POST':
        codes = [c for c in request.POST.getlist('collect_data') if c in dict(SERVICE_COLLECT_DATA_CHOICES)]
        service.collect_data = ','.join(codes)
        service.save(update_fields=['collect_data'])
        labels = dict(SERVICE_COLLECT_DATA_CHOICES)
        names = [labels[c] for c in codes]
        messages.success(request, f'"{service.title}" agora solicita: {", ".join(names) or "nada à parte"}.')
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
def admin_service_bulk_delete(request, svtype):
    db_type, label = _service_type_from(svtype)
    if request.method == 'POST':
        raw = request.POST.get('service_ids', '')
        ids = []
        for part in str(raw).split(','):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        qs = ServiceList.objects.filter(id__in=ids, service_type=db_type)
        count = qs.count()
        if count:
            qs.delete()
            messages.success(request, f'{count} serviço(s) excluído(s) com sucesso.')
        else:
            messages.warning(request, 'Nenhum serviço selecionado para exclusão.')
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
    blocked = _guard_settings_section(request)
    if blocked:
        return blocked
    return render(request, 'admin/currency_list.html', {'currencies': Currency.objects.filter(code='BRL')})


@_staff
def admin_currency_update(request, currency_id):
    blocked = _guard_settings_section(request)
    if blocked:
        return blocked
    cur = Currency.objects.filter(id=currency_id).first()
    if cur and cur.code == 'BRL' and request.method == 'POST':
        cur.status = 'Active'
        cur.rate = Decimal('1')
        cur.save()
        messages.success(request, 'BRL é a moeda única do site (fixa em R$).')
    return redirect('admin_currency_list')


@_staff
def admin_gateway_list(request):
    blocked = _guard_settings_section(request)
    if blocked:
        return blocked
    return render(request, 'admin/gateway_list.html', {
        'gateways': PaymentGateway.objects.filter(name__in=['Asaas', 'Binance', 'bKash']),
        'currencies': Currency.objects.filter(status='Active'),
        'webhook_asaas': request.build_absolute_uri(reverse('asaas_webhook')),
        'webhook_binance': request.build_absolute_uri(reverse('binance_webhook')),
    })


@_staff
def admin_gateway_update(request, gateway_id):
    blocked = _guard_settings_section(request)
    if blocked:
        return blocked
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

#: Listas locais (ServiceGroup.slug) usadas na distribuição automática por palavra-chave.
AUTO_KEYWORD_SVTYPES = ('remote', 'imei', 'server', 'file', 'method')

#: Tipo de serviço local de cada lista (ServiceGroup.slug).
AUTO_GROUP_TYPE = {
    'remote': 'Server Service',
    'imei': 'IMEI Service',
    'server': 'Activation Service',
    'file': 'Server Service',
    'method': 'Method Service',
}

#: Nome amigável de cada lista local.
AUTO_GROUP_LABELS = {
    'remote': 'Aluguel',
    'imei': 'IMEI',
    'server': 'Ativação',
    'file': 'Arquivos',
    'method': 'Métodos',
}

#: Palavras-chave padrão por lista local (sem acento, minúsculas).
DEFAULT_AUTO_KEYWORDS = {
    'remote': ['rent', '6 hours', 'hours'],
    'imei': [],
    'server': [],
    'file': [],
    'method': [],
}

#: Lista local de destino quando o serviço remoto não bate com nenhuma palavra-chave.
AUTO_FALLBACK_BY_TYPE = {
    'IMEI': 'imei',
    'SERVER': 'server',
    'REMOTE': 'server',
    'CREDIT': 'server',
}


def _auto_norm(text):
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()


def _auto_keywords_for(api):
    """Retorna {group_slug: [palavras-chave normalizadas]} da API, mesclado com o padrão."""
    merged = {k: list(v) for k, v in DEFAULT_AUTO_KEYWORDS.items()}
    raw = SystemSetting.get('apiAutoMap', '')
    if raw:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = {}
        entry = (data.get(str(api.id)) or {}) if api else {}
        for svtype, kws in entry.items():
            if svtype in merged:
                merged[svtype] = [_auto_norm(k) for k in (kws or []) if str(k).strip()]
    return merged


def _upsert_remote_catalog(api):
    """Baixa o catálogo do provedor e sincroniza RemoteServiceList.

    Retorna (catalog, created, updated) onde 'created'/'updated' dizem respeito
    apenas aos serviços remotos persistidos.
    """
    catalog = provider_api.fetch_catalog(api)
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
    return catalog, created, updated


def _price_field(request, name, default=Decimal('0')):
    """Lê um campo decimal de formulário, retornando o valor padrão se inválido."""
    raw = (request.POST.get(name) or '').strip().replace(',', '.').replace('R$', '')
    if not raw:
        return default
    try:
        return Decimal(raw)
    except (TypeError, ValueError, InvalidOperation):
        return default


@_staff
def admin_api_list(request):
    apis = Api.objects.all().order_by('-id')
    q = (request.GET.get('q') or '').strip()
    show_products = request.GET.get('produtos') == '1' or bool(q)
    raw_map = SystemSetting.get('apiAutoMap', '')
    auto_map_data = {}
    if raw_map:
        try:
            auto_map_data = json.loads(raw_map)
        except (TypeError, ValueError):
            auto_map_data = {}
    api_kw_strings = {}
    for api in apis:
        api_kw_strings[api.id] = {k: ', '.join(v) for k, v in _auto_keywords_for(api).items()}
    ctx = {
        'apis': apis,
        'show_products': show_products,
        'search_q': q,
        'auto_map_data': auto_map_data,
        'api_kw_strings': api_kw_strings,
        'auto_svtypes': AUTO_KEYWORD_SVTYPES,
        'auto_svtype_labels': AUTO_GROUP_LABELS,
    }
    if show_products:
        local_services = list(ServiceList.objects.filter(status='Active').order_by('service_type', 'title'))
        local_by_type = {}
        for svc in local_services:
            local_by_type.setdefault(svc.service_type, []).append(svc)
        remote_qs = RemoteServiceList.objects.select_related('api').order_by('api_id', 'SERVICENAME')
        if q:
            remote_qs = remote_qs.filter(Q(SERVICENAME__icontains=q) | Q(referenceid__icontains=q))
        remote_services = list(remote_qs)
        for remote in remote_services:
            remote.price_brl = provider_api.suggested_price(remote.api, remote.CREDIT) if remote.api else None
            remote.rem_local_type = REMOTE_TYPE_TO_LOCAL.get((remote.SERVICETYPE or '').upper())
        linked_by_remote = {}
        for linked in ServiceList.objects.exclude(api__isnull=True).exclude(referenceid__isnull=True).exclude(referenceid=''):
            linked_by_remote.setdefault((linked.api_id, linked.referenceid), []).append(linked)
        ctx.update({
            'local_services': local_services,
            'local_by_type': local_by_type,
            'remote_services': remote_services,
            'linked_by_remote': linked_by_remote,
            'remote_type_to_local': REMOTE_TYPE_TO_LOCAL,
        })
    return render(request, 'admin/api_list.html', ctx)


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
            api_pin=(request.POST.get('api_pin') or '').strip(),
            panel_url=(request.POST.get('panel_url') or '').strip(),
            panel_user=(request.POST.get('panel_user') or '').strip(),
            panel_pass=(request.POST.get('panel_pass') or '').strip(),
            status=request.POST.get('status', 'Active'),
            price_rate=_price_field(request, 'price_rate'),
            price_markup=_price_field(request, 'price_markup'),
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
        if request.POST.get('api_pin') is not None:
            api.api_pin = request.POST['api_pin'].strip()
        if request.POST.get('panel_url') is not None:
            api.panel_url = request.POST['panel_url'].strip()
        if request.POST.get('panel_user') is not None:
            api.panel_user = request.POST['panel_user'].strip()
        if request.POST.get('panel_pass') is not None:
            api.panel_pass = request.POST['panel_pass'].strip()
        if request.POST.get('status') in ('Active', 'Inactive'):
            api.status = request.POST['status']
        if request.POST.get('price_rate') is not None:
            api.price_rate = _price_field(request, 'price_rate')
        if request.POST.get('price_markup') is not None:
            api.price_markup = _price_field(request, 'price_markup')
        api.save()
        messages.success(request, 'API atualizada com sucesso.')
    return redirect('admin_api_list')


@_staff
def admin_api_delete(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api and request.method == 'POST':
        name = api.api_name
        ServiceList.objects.filter(api=api).update(api=None, referenceid='', process_type='Manual')
        api.delete()
        messages.success(request, 'API "{}" excluída com sucesso. Os serviços vinculados ficam sem API e podem ser revinculados.'.format(name))
    return redirect('admin_api_list')
@_staff
def admin_api_test(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api:
        try:
            info = provider_api.account_info(api)
            messages.success(request, 'Conexão OK. Conta: {} | Saldo: {}'.format(info['mail'], info['credit']))
        except provider_api.ProviderError as exc:
            hint = _ip_auth_hint(exc)
            if hint:
                messages.error(request, hint)
            else:
                messages.error(request, 'Falha na conexão: {}'.format(exc))
    return redirect('admin_api_list')


@_staff
def admin_api_import(request, api_id):
    api = Api.objects.filter(id=api_id).first()
    if api:
        try:
            catalog, created, updated = _upsert_remote_catalog(api)
        except provider_api.ProviderError as exc:
            hint = _ip_auth_hint(exc)
            if hint:
                messages.error(request, hint)
            else:
                messages.error(request, 'Falha ao importar: {}'.format(exc))
            return redirect('admin_api_list')
        messages.success(request, 'Importados {} serviços do provedor ({} novos, {} atualizados).'.format(
            len(catalog), created, updated))
    return redirect('{}?produtos=1'.format(reverse('admin_api_list')))


@_staff
def admin_api_sync(request, api_id):
    """Importa o catálogo do provedor e vincula na lista local automaticamente,
    distribuindo cada serviço para a lista correta pelas palavras-chave
    configuradas (ex.: 'Rent', '6 Hours', 'Hours' -> lista Aluguel)."""
    api = Api.objects.filter(id=api_id).first()
    if api is None:
        return redirect('admin_api_list')
    try:
        catalog, remote_created, remote_updated = _upsert_remote_catalog(api)
    except provider_api.ProviderError as exc:
        hint = _ip_auth_hint(exc)
        if hint:
            messages.error(request, hint)
        else:
            messages.error(request, 'Falha ao importar: {}'.format(exc))
        return redirect('admin_api_list')
    keywords = _auto_keywords_for(api)
    remotes = {r.referenceid: r for r in RemoteServiceList.objects.filter(api=api)}
    created = 0
    already = 0
    skipped = 0
    for item in catalog:
        name = (item['name'] or '').strip()
        rid = item['referenceid']
        if not name:
            skipped += 1
            continue
        target = None
        rname_n = _auto_norm(name)
        for group_slug, kws in keywords.items():
            if any(kw and kw in rname_n for kw in kws):
                target = group_slug
                break
        if target is None:
            target = AUTO_FALLBACK_BY_TYPE.get(str(item['servicetype']).upper())
        if target is None:
            skipped += 1
            continue
        local = ServiceList.objects.filter(api=api, referenceid=rid).first()
        if local is not None:
            already += 1
            continue
        remote = remotes.get(rid)
        group = ServiceGroup.objects.filter(slug=target).first()
        if group is None:
            group = ServiceGroup.objects.create(name=target, slug=target, status='Active')
        local_type = AUTO_GROUP_TYPE.get(target, 'Server Service')
        suggested = provider_api.suggested_price(api, item['credit']) if api else None
        base_slug = slugify(name)[:50] or 'servico'
        slug = base_slug
        n = 2
        while ServiceList.objects.filter(slug=slug).exists():
            slug = '{}-{}'.format(base_slug, n)
            n += 1
        time_text = item['time'] or ''
        local = ServiceList.objects.create(
            service_type=local_type,
            service_group=group,
            title=name,
            slug=slug,
            status='Active',
            duration=time_text,
            delivery_time=time_text,
            price_type='fixed_price',
            original_price=suggested if suggested is not None else Decimal('0'),
            min_qnt='1',
            max_qnt='',
            process_type='Auto',
            api=api,
            api_enabled=True,
            referenceid=rid,
            collect_data='user,email',
        )
        if remote is not None:
            names = list(remote.service_fields.values_list('name', flat=True))
        else:
            names = list(item['fields'])
        for fname in names:
            ServiceInput.objects.create(service=local, name=fname)
        if local_type == 'Credit Service':
            extras = CREDIT_SERVICE_EXTRA_FIELDS
        elif local_type == 'Activation Service':
            extras = ACTIVATION_SERVICE_EXTRA_FIELDS
        elif local_type == 'Method Service':
            extras = METHOD_SERVICE_EXTRA_FIELDS
        else:
            extras = ()
        for extra in extras:
            if extra not in names:
                ServiceInput.objects.get_or_create(service=local, name=extra)
        created += 1
    messages.success(request,
        'Vinculação automática de "{}" concluída: {} serviços novos criados, {} já vinculados, {} sem lista. '
        'Catálogo remoto: {} novos, {} atualizados.'.format(
            api.api_name, created, already, skipped, remote_created, remote_updated))
    return redirect('{}?produtos=1'.format(reverse('admin_api_list')))


@_staff
def admin_fetch_missing_thumbnails(request):
    """Varre sites GSM Theme, casa pelo nome e preenche a imagem dos serviços
    locais que estão sem thumbnail (mesma lógica de correspondência da vinculação)."""
    if request.method != 'POST':
        return redirect('admin_api_list')
    stats = catalog_images.scan_and_fill()
    if stats['downloaded']:
        messages.success(request,
            'Imagens do catálogo GSM Theme: {} produto(s) preenchido(s) com imagem '
            '({} correspondidos, {} falhas ao baixar). Sites lidos: {}.'.format(
                stats['downloaded'], stats['matched'], stats['failed'],
                ', '.join(stats['sites_ok']) or 'nenhum'))
    else:
        messages.info(request,
            'Nenhum produto sem imagem foi preenchido ({0} itens no catálogo dos sites, '
            '{1} correspondidos, {2} sem correspondência).{3}'.format(
                stats['catalog_items'], stats['matched'],
                stats['total'] - stats['matched'],
                ' Sites inacessíveis: {}.'.format(', '.join(stats['sites_fail']))
                if stats['sites_fail'] else ''))
    return redirect('{}?produtos=1'.format(reverse('admin_api_list')))


@_staff
def admin_api_map(request, api_id):
    """Grava as palavras-chave de distribuição de uma API (por lista local)."""
    api = Api.objects.filter(id=api_id).first()
    if api and request.method == 'POST':
        raw = SystemSetting.get('apiAutoMap', '')
        data = {}
        if raw:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                data = {}
        entry = {}
        for svtype in AUTO_KEYWORD_SVTYPES:
            text = (request.POST.get('kw_{}'.format(svtype)) or '')
            kws = [k.strip() for k in text.replace(';', ',').split(',') if k.strip()]
            entry[svtype] = kws
        data[str(api.id)] = entry
        obj, _ = SystemSetting.objects.get_or_create(key='apiAutoMap', defaults={'value': ''})
        obj.value = json.dumps(data)
        obj.save()
        messages.success(request, 'Palavras-chave de distribuição de "{}" salvas.'.format(api.api_name))
    return redirect('admin_api_list')


def _db_path():
    name = settings.DATABASES['default']['NAME']
    return name


def _save_pre_restore_backup():
    """Copia o banco atual para a pasta de backups antes de um restore."""
    db_path = _db_path()
    if not os.path.exists(db_path):
        return ''
    backup_dir = Path(settings.BASE_DIR) / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(db_path))
    dest_name = backup_dir / 'pre_restore_{}.sqlite'.format(time.strftime('%Y%m%d_%H%M%S'))
    dest = sqlite3.connect(str(dest_name))
    try:
        with dest:
            src.backup(dest)
    finally:
        src.close()
        dest.close()
    return dest_name


@_staff
def admin_db_export(request):
    """Baixa um snapshot consistente do banco (SQLite) para o computador."""
    db_path = _db_path()
    if not os.path.exists(db_path):
        messages.error(request, 'Banco de dados não encontrado em "{}".'.format(db_path))
        return redirect('admin_api_list')
    src = sqlite3.connect(str(db_path))
    tmp = tempfile.NamedTemporaryFile(prefix='servicetool_backup_', suffix='.sqlite', delete=False)
    tmp.close()
    dest = sqlite3.connect(tmp.name)
    try:
        with dest:
            src.backup(dest)
    except Exception:
        messages.error(request, 'Falha ao gerar o backup.')
        if os.path.exists(tmp.name):
            try:
                os.remove(tmp.name)
            except OSError:
                pass
        return redirect('admin_api_list')
    finally:
        src.close()
        dest.close()
    with open(tmp.name, 'rb') as fh:
        data = fh.read()
    os.remove(tmp.name)
    response = HttpResponse(data, content_type='application/octet-stream')
    stamp = time.strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = 'attachment; filename="servicetool_backup_{}.sqlite"'.format(stamp)
    return response


@_staff
def admin_db_import(request):
    """Restaura o banco a partir de um arquivo enviado.

    Aceita um snapshot binario .sqlite (gerado pelo botao de exportar) ou um
    dump de texto .sql (CREATE TABLE/INSERT). Antes de restaurar, o banco
    atual e salvo automaticamente na pasta backups/ do servidor.
    """
    if request.method != 'POST':
        return redirect('admin_api_list')
    f = request.FILES.get('backup_file')
    if not f:
        messages.error(request, 'Selecione um arquivo de backup.')
        return redirect('admin_api_list')
    data = f.read()
    if not data:
        messages.error(request, 'O arquivo está vazio.')
        return redirect('admin_api_list')
    db_path = _db_path()
    pre_restore = _save_pre_restore_backup()
    tmp_name = None
    try:
        is_binary = data[:16] == b'SQLite format 3\x00'
        text = data.decode('utf-8', errors='replace')
        is_sql = ('CREATE TABLE' in text.upper()) or text.lstrip().lower().startswith('pragma')
        if not is_binary and not is_sql:
            messages.error(request, 'Arquivo inválido: não parece um backup SQLite (.sqlite) nem um dump .sql.')
            return redirect('admin_api_list')

        # monta o banco a importar num arquivo temporario novo (schema limpo)
        tmp = tempfile.NamedTemporaryFile(prefix='servicetool_restore_', suffix='.sqlite', delete=False)
        tmp.close()
        tmp_name = tmp.name
        if is_binary:
            with open(tmp_name, 'wb') as fh:
                fh.write(data)
        # dump .sql roda sobre arquivo vazio: sqlite cria um banco novo ao conectar
        src = sqlite3.connect(tmp_name)
        if is_binary:
            src.execute('SELECT count(*) FROM sqlite_master').fetchone()
        else:
            src.executescript(text)
            src.commit()
        # copia o banco temporario para o banco vivo (online backup)
        dst = sqlite3.connect(str(db_path))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        detail = ''
        if pre_restore:
            detail = ' O banco anterior foi salvo em "backups/{}".'.format(os.path.basename(str(pre_restore)))
        messages.success(request, 'Backup importado com sucesso.{}'.format(detail))
    except sqlite3.Error as exc:
        messages.error(request, 'Falha ao importar o backup: {}'.format(exc))
    except Exception as exc:
        messages.error(request, 'Falha ao importar o backup: {}'.format(exc))
    finally:
        if tmp_name:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
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
                    suggested = provider_api.suggested_price(remote.api, remote.CREDIT) if remote.api else None
                    auto_priced = False
                    if request.POST.get('auto_price') and suggested is not None:
                        service.original_price = suggested
                        auto_priced = True
                    service.save(update_fields=['api', 'referenceid', 'process_type', 'original_price'])
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
                    if auto_priced:
                        messages.success(request, 'Serviço "{}" vinculado ao provedor. Preço definido automaticamente: R$ {}.'.format(
                            service.title, suggested))
                    else:
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


@_staff
def admin_daily_report(request):
    """Relatório diário de pedidos.

    Agrupa as compras por dia (data de criação local). O dia de hoje é
    mostrado isolado (começa do zero a cada dia) e os dias anteriores
    ficam no histórico agrupados pela data, sem misturar um dia com o outro.
    """
    service_q = (request.GET.get('svc') or '').strip()
    kind = (request.GET.get('kind') or '').strip()
    only_direct = kind == 'admin'
    only_customers = kind == 'clients'

    orders = (CustomerOrder.objects
              .select_related('customer', 'service')
              .prefetch_related('order_inputs', 'statements'))
    if service_q:
        orders = orders.filter(service_id=service_q)

    services = sorted(
        {o.service_id: (o.service.title if o.service else 'Serviço removido')
         for o in orders.order_by('service_id')}.items(),
        key=lambda kv: kv[1].lower(),
    )

    days = {}
    for order in orders.order_by('created_at'):
        is_direct = _is_admin_direct(order)
        if only_direct and not is_direct:
            continue
        if only_customers and is_direct:
            continue
        local = timezone.localtime(order.created_at)
        day_key = local.date().isoformat()
        day = days.setdefault(day_key, {
            'date': local.date(),
            'label': '',
            'orders': [],
            'total': Decimal('0.00'),
            'count': 0,
        })
        day['orders'].append({
            'order': order,
            'direct': is_direct,
            'time': local.strftime('%H:%M'),
        })
        day['total'] += (order.service_price or Decimal('0.00'))
        day['count'] += 1

    today = timezone.localdate()
    yesterday = today - timezone.timedelta(days=1)
    # Ordena por data descendente (hoje primeiro, depois o histórico).
    ordered_days = []
    for key in sorted(days.keys(), reverse=True):
        day = days[key]
        if key == today.isoformat():
            day['label'] = 'Hoje'
        elif key == yesterday.isoformat():
            day['label'] = 'Ontem'
        ordered_days.append(day)

    total_all = sum((d['total'] for d in ordered_days), Decimal('0.00'))

    ctx = {
        'days': ordered_days,
        'today': today,
        'services': services,
        'svc': service_q,
        'kind': kind,
        'total_all': total_all.quantize(Decimal('0.00')),
        'day_count': len(ordered_days),
    }
    return render(request, 'admin/daily_report.html', ctx)

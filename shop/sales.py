from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, ROUND_DOWN
import logging
import time as clock

import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Order, OrderItem, Product, StripeSyncRun, StripeTransaction
from .services import mark_order_paid


logger = logging.getLogger(__name__)
PAID_STATES = ['paid', 'partially_refunded', 'refunded']
ZERO = Decimal('0.00')


def append_refund_notes(record, notes):
    with transaction.atomic():
        current = type(record).objects.select_for_update().get(pk=record.pk)
        combined = '\n\n'.join(value for value in [current.refund_notes, notes] if value)
        if len(combined) > 2000:
            raise ValueError('Refund notes cannot exceed 2,000 characters in total.')
        current.refund_notes = combined
        current.save(update_fields=['refund_notes'])


def sales_window(params):
    period = params.get('period', 'month')
    if period not in {'month', 'quarter', 'year'}:
        period = 'month'
    current_year = timezone.localdate().year
    try:
        year = int(params.get('year', current_year))
    except (TypeError, ValueError):
        year = current_year
    if not 2000 <= year <= current_year:
        year = current_year
    starts = [(year, month) for month in range(1, 13)] if period == 'month' else [(year, month) for month in [1, 4, 7, 10]] if period == 'quarter' else [(value, 1) for value in range(year - 5, year + 1)]
    buckets = []
    for bucket_year, month in starts:
        next_month = month + (1 if period == 'month' else 3 if period == 'quarter' else 12)
        end_year = bucket_year + (next_month - 1) // 12
        end_month = (next_month - 1) % 12 + 1
        start = timezone.make_aware(datetime(bucket_year, month, 1))
        end = timezone.make_aware(datetime(end_year, end_month, 1))
        key = f'{bucket_year}-{month:02}' if period == 'month' else f'{bucket_year}-Q{(month - 1) // 3 + 1}' if period == 'quarter' else str(bucket_year)
        label = start.strftime('%b %Y') if period == 'month' else f'Q{(month - 1) // 3 + 1} {bucket_year}' if period == 'quarter' else str(bucket_year)
        buckets.append({'key': key, 'label': label, 'start': start, 'end': end})
    return {'period': period, 'year': year, 'current_year': current_year, 'currency': settings.STRIPE_CURRENCY.upper(), 'start': buckets[0]['start'], 'end': buckets[-1]['end'], 'buckets': buckets}

def record_offline_sale(data, actor):
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=data['product'].pk)
        existing = Order.objects.filter(manual_entry_id=data['entry_id']).first()
        if existing:
            return existing, False
        if not product.active or product.stock_quantity < data['quantity']:
            raise ValueError('Insufficient available stock for this entry.')
        reference = data.get('reference') or None
        if reference and Order.objects.filter(manual_payment_reference=reference).exists():
            raise ValueError('That payment or redemption reference has already been recorded.')
        channel = data['payment_channel']
        amount = data['amount'] if channel != 'gift' else ZERO
        now = timezone.now()
        order = Order.objects.create(
            email='', shipping_name=data['customer_name'], shipping_address1='Recorded in person',
            shipping_city='', shipping_postcode='', status='completed', payment_status='pending',
            payment_channel=channel, manual_entry_id=data['entry_id'], manual_payment_reference=reference,
            assigned_to=actor, subtotal=amount, total=amount, currency=settings.STRIPE_CURRENCY.upper(),
            payment_method=dict(Order.PAYMENT_CHANNEL_CHOICES)[channel], payment_date=now,
            transaction_id=f'offline:{data["entry_id"]}',
        )
        unit_price = (amount / data['quantity']).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        extra_pennies = int((amount - unit_price * data['quantity']) * 100)
        for quantity, price in [(data['quantity'] - extra_pennies, unit_price), (extra_pennies, unit_price + Decimal('0.01'))]:
            if quantity:
                OrderItem.objects.create(order=order, product=product, product_name=product.name, sku=product.sku, unit_price=price, unit_cost=product.cost_price, quantity=quantity)
        mark_order_paid(order, stock_actor=actor)
        return order, True


def local_orders_scope(window):
    orders = Order.objects.filter(currency=window['currency'])
    if settings.STRIPE_SECRET_KEY.startswith(('sk_live_', 'rk_live_')):
        orders = orders.exclude(stripe_session_id__startswith='cs_test_')
    return orders


def local_paid_orders(window):
    return local_orders_scope(window).filter(payment_status__in=PAID_STATES)


def replenishment_budget():
    items = []
    for product in Product.objects.filter(active=True, stock_quantity__lte=F('reorder_level')).select_related('collection'):
        quantity = product.replenishment_units
        if quantity:
            items.append({'product': product, 'qty_needed': quantity, 'line_cost': quantity * product.cost_price})
    items.sort(key=lambda entry: (-entry['line_cost'], entry['product'].sku))
    return {'items': items, 'total': sum((entry['line_cost'] for entry in items), ZERO), 'zero_cost_count': sum(entry['product'].cost_price == 0 for entry in items)}


def completed_sales_report(window):
    orders = local_paid_orders(window).filter(completed_at__gte=window['start'], completed_at__lt=window['end']).order_by('completed_at', 'pk')
    cost_expression = ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=DecimalField(max_digits=14, decimal_places=2))
    costs = dict(OrderItem.objects.filter(order__in=orders).values('order_id').annotate(cost=Sum(cost_expression)).values_list('order_id', 'cost'))
    periods = [{**bucket, **{name: ZERO for name in ['revenue', 'product_revenue', 'cogs', 'gift_cost', 'refunds', 'stripe', 'cash', 'gift_card']}, 'order_count': 0, 'gift_count': 0} for bucket in window['buckets']]
    for order in orders:
        bucket = next(row for row in periods if row['start'] <= order.completed_at < row['end'])
        cost = costs.get(order.pk, ZERO) or ZERO
        if order.payment_channel == 'gift':
            bucket['gift_cost'] += cost
            bucket['gift_count'] += 1
            continue
        net = max(ZERO, order.total - order.refunded_amount)
        product_net = max(ZERO, order.subtotal - order.discount_amount)
        product_net = (product_net * net / order.total).quantize(Decimal('0.01')) if order.total else ZERO
        bucket['revenue'] += net
        bucket['product_revenue'] += product_net
        bucket['cogs'] += cost
        bucket['refunds'] += order.refunded_amount
        bucket[order.payment_channel] += net
        bucket['order_count'] += 1
    for row in periods:
        row['gross_profit'] = row['product_revenue'] - row['cogs']
        row['profit_after_gifts'] = row['gross_profit'] - row['gift_cost']
        row['margin'] = row['gross_profit'] / row['product_revenue'] * 100 if row['product_revenue'] else ZERO
    totals = {name: sum((row[name] for row in periods), ZERO) for name in ['revenue', 'product_revenue', 'cogs', 'gift_cost', 'refunds', 'stripe', 'cash', 'gift_card', 'gross_profit', 'profit_after_gifts']}
    totals['order_count'] = sum(row['order_count'] for row in periods)
    totals['gift_count'] = sum(row['gift_count'] for row in periods)
    totals['missing_completion_dates'] = local_paid_orders(window).filter(status='completed', completed_at__isnull=True).count()
    totals['zero_cost_item_count'] = OrderItem.objects.filter(order__in=orders, unit_cost=0).count()
    return {'periods': periods, 'totals': totals, 'orders': orders}


def sync_stripe_transactions(window, actor):
    livemode = settings.STRIPE_SECRET_KEY.startswith(('sk_live_', 'rk_live_'))
    run = StripeSyncRun.objects.create(start_at=window['start'], end_at=window['end'], currency=window['currency'], livemode=livemode, status='failed', actor=actor)
    if not settings.STRIPE_SECRET_KEY:
        run.error_code = 'StripeNotConfigured'
        run.save(update_fields=['error_code'])
        return run
    deadline = clock.monotonic() + 40
    scanned = 0
    try:
        client = stripe.StripeClient(settings.STRIPE_SECRET_KEY, max_network_retries=0, http_client=stripe.RequestsClient(timeout=10))
        charges = client.v1.charges.list({'limit': 100, 'created': {'gte': int(window['start'].timestamp()), 'lt': int(window['end'].timestamp())}, 'expand': ['data.balance_transaction']})
        run.status = 'complete'
        for charge in charges.auto_paging_iter():
            if scanned >= 5000 or clock.monotonic() >= deadline:
                run.status = 'partial'
                break
            scanned += 1
            charge = charge.to_dict() if hasattr(charge, 'to_dict') else dict(charge)
            if bool(charge.get('livemode')) != livemode:
                continue
            currency = str(charge.get('currency', '')).upper()
            if currency != window['currency']:
                continue
            references = [charge['id']] + ([charge['payment_intent']] if charge.get('payment_intent') else [])
            order = Order.objects.filter(payment_channel='stripe', transaction_id__in=references).first()
            metadata_order = (charge.get('metadata') or {}).get('order_id')
            if order is None and str(metadata_order).isdigit():
                order = Order.objects.filter(pk=int(metadata_order), payment_channel='stripe').first()
            balance = charge.get('balance_transaction')
            same_currency = isinstance(balance, dict) and str(balance.get('currency', '')).upper() == currency
            refunded = Decimal(charge.get('amount_refunded', 0)) / 100
            defaults = {
                'payment_intent_id': charge.get('payment_intent') or '', 'order': order,
                'currency': currency, 'amount': Decimal(charge.get('amount_captured') or charge['amount']) / 100,
                'refunded_amount': refunded,
                'fee': Decimal(balance['fee']) / 100 if same_currency else None,
                'net': Decimal(balance['net']) / 100 - refunded if same_currency else None,
                'status': 'paid' if charge.get('paid') and charge.get('captured') else 'failed' if charge.get('status') == 'failed' else 'pending',
                'occurred_at': datetime.fromtimestamp(charge['created'], tz=datetime_timezone.utc),
                'livemode': bool(charge.get('livemode')),
            }
            StripeTransaction.objects.update_or_create(
                stripe_id=charge['id'], defaults=defaults,
                create_defaults={**defaults, 'refund_notes': order.refund_notes if order else ''},
            )
            run.transaction_count += 1
    except Exception as error:
        run.status = 'partial' if run.transaction_count else 'failed'
        run.error_code = type(error).__name__
        logger.warning('Stripe reconciliation sync failed: %s', run.error_code)
    run.save(update_fields=['status', 'transaction_count', 'error_code'])
    return run


def reconciliation_report(window):
    livemode = settings.STRIPE_SECRET_KEY.startswith(('sk_live_', 'rk_live_'))
    remote = list(StripeTransaction.objects.filter(currency=window['currency'], livemode=livemode, occurred_at__gte=window['start'], occurred_at__lt=window['end']).select_related('order'))
    local = list(local_paid_orders(window).filter(payment_channel='stripe').annotate(received_at=Coalesce('payment_date', 'paid_at', 'created_at')).filter(received_at__gte=window['start'], received_at__lt=window['end']))
    failed_local = list(local_orders_scope(window).filter(payment_channel='stripe', payment_status='failed').annotate(received_at=Coalesce('payment_date', 'created_at')).filter(received_at__gte=window['start'], received_at__lt=window['end']))
    last_sync = StripeSyncRun.objects.filter(currency=window['currency'], livemode=livemode, start_at__lte=window['start'], end_at__gte=window['end']).first()
    duplicates = dict(StripeTransaction.objects.filter(livemode=livemode, status='paid', order__isnull=False).values('order_id').annotate(count=Count('pk')).filter(count__gt=1).values_list('order_id', 'count'))
    matched_ids = set()
    entries = []
    for receipt in remote:
        order = receipt.order
        if order and receipt.status == 'paid':
            matched_ids.add(order.pk)
        if receipt.status == 'failed':
            if order is None:
                status = 'Unmatched failed Stripe attempt'
            elif order.currency != receipt.currency or order.total != receipt.amount:
                status = 'Failed attempt amount mismatch'
            elif order.payment_status == 'failed':
                status = 'Failure matched'
            elif order.payment_status in PAID_STATES:
                status = 'Failed attempt; order now paid'
            else:
                status = 'Failure status mismatch'
        elif receipt.status != 'paid':
            status = receipt.get_status_display()
        elif order is None:
            status = 'Unmatched Stripe receipt'
        elif order.pk in duplicates:
            status = 'Multiple Stripe payments'
        elif order.currency != receipt.currency or order.total != receipt.amount:
            status = 'Amount mismatch'
        elif order.refunded_amount != receipt.refunded_amount:
            status = 'Refund mismatch'
        elif order.payment_status not in PAID_STATES:
            status = 'Payment status mismatch'
        else:
            status = 'Matched'
        entries.append({'receipt': receipt, 'order': order, 'status': status, 'stripe_net': receipt.amount - receipt.refunded_amount if receipt.status == 'paid' else ZERO, 'local_net': max(ZERO, order.total - order.refunded_amount) if receipt.status == 'paid' and order and order.payment_status in PAID_STATES else ZERO})
    for order in local:
        if order.pk not in matched_ids:
            occurred_at = order.payment_date or order.paid_at or order.created_at
            covered = last_sync and last_sync.status == 'complete' and occurred_at <= last_sync.created_at
            entries.append({'receipt': None, 'order': order, 'status': 'Missing Stripe receipt' if covered else 'Not synced', 'stripe_net': ZERO, 'local_net': max(ZERO, order.total - order.refunded_amount)})
    linked_ids = {receipt.order_id for receipt in remote}
    for order in failed_local:
        if order.pk not in linked_ids:
            covered = last_sync and last_sync.status == 'complete' and order.received_at <= last_sync.created_at
            entries.append({'receipt': None, 'order': order, 'status': 'Missing Stripe failed attempt' if covered else 'Not synced', 'stripe_net': ZERO, 'local_net': ZERO})
    for entry in entries:
        receipt, order = entry['receipt'], entry['order']
        entry.update({
            'occurred_at': receipt.occurred_at if receipt else order.received_at,
            'stripe_refund': receipt.refunded_amount if receipt else ZERO,
            'local_refund': order.refunded_amount if order and (receipt is None or receipt.status == 'paid') else ZERO,
            'stripe_failed_amount': receipt.amount if receipt and receipt.status == 'failed' else ZERO,
            'local_failed_amount': order.total if order and order.payment_status == 'failed' else ZERO,
        })
    refund_entries = [entry for entry in entries if entry['stripe_refund'] or entry['local_refund']]
    failure_entries = [entry for entry in entries if (entry['receipt'] and entry['receipt'].status == 'failed') or (entry['order'] and entry['order'].payment_status == 'failed')]
    paid_remote = [receipt for receipt in remote if receipt.status == 'paid']
    failed_remote = [receipt for receipt in remote if receipt.status == 'failed']
    stripe_total = sum((receipt.amount - receipt.refunded_amount for receipt in paid_remote), ZERO)
    local_total = sum((max(ZERO, order.total - order.refunded_amount) for order in local), ZERO)
    stripe_refunds = sum((receipt.refunded_amount for receipt in remote), ZERO)
    local_refunds = sum((order.refunded_amount for order in local), ZERO)
    settled_statuses = {'Matched', 'Failure matched', 'Failed attempt; order now paid', 'Not synced', 'Pending'}
    return {
        'entries': entries, 'last_sync': last_sync, 'stripe_total': stripe_total, 'local_total': local_total,
        'difference': stripe_total - local_total, 'fees': sum((receipt.fee or ZERO for receipt in paid_remote), ZERO),
        'unknown_fees': sum(receipt.fee is None for receipt in paid_remote),
        'matched_count': sum(entry['status'] == 'Matched' for entry in entries),
        'review_count': sum(entry['status'] not in settled_statuses for entry in entries),
        'unsynced_count': sum(entry['status'] == 'Not synced' for entry in entries),
        'refunds': {
            'entries': refund_entries, 'stripe_total': stripe_refunds, 'local_total': local_refunds,
            'difference': stripe_refunds - local_refunds,
            'matched_count': sum(entry['status'] == 'Matched' for entry in refund_entries),
            'review_count': sum(entry['status'] not in settled_statuses for entry in refund_entries),
            'unsynced_count': sum(entry['status'] == 'Not synced' for entry in refund_entries),
        },
        'failures': {
            'entries': failure_entries, 'stripe_count': len(failed_remote), 'local_count': len(failed_local),
            'stripe_total': sum((receipt.amount for receipt in failed_remote), ZERO),
            'local_total': sum((order.total for order in failed_local), ZERO),
            'matched_count': sum(entry['status'] == 'Failure matched' for entry in failure_entries),
            'recovered_count': sum(entry['status'] == 'Failed attempt; order now paid' for entry in failure_entries),
            'review_count': sum(entry['status'] not in settled_statuses for entry in failure_entries),
            'unsynced_count': sum(entry['status'] == 'Not synced' for entry in failure_entries),
        },
    }
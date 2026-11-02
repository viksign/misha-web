import hashlib
import json
import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .cart import Cart
from .models import CheckoutAttempt, CustomerAddress, Order

logger = logging.getLogger(__name__)
ATTEMPT_SESSION_KEY = 'checkout_attempt_id'


def checkout_attempt(request, create=False):
    attempt_id = request.session.get(ATTEMPT_SESSION_KEY)
    attempt = CheckoutAttempt.objects.filter(pk=attempt_id).select_related('order').first() if attempt_id else None
    if attempt is None and create:
        attempt = CheckoutAttempt.objects.create()
        request.session[ATTEMPT_SESSION_KEY] = str(attempt.pk)
    return attempt


def checkout_fingerprint(form, items, shipping_cost):
    details = dict(form.cleaned_data)
    if details.get('ship_to_different_address') and details.get('shipping_address'):
        address = CustomerAddress.objects.get(pk=details['shipping_address'], user=form.user, address_type='shipping')
        details['saved_shipping_address'] = {
            name: getattr(address, name)
            for name in ('house_number', 'street_name', 'address2', 'city', 'postcode', 'country')
        }
    payload = {
        'details': details,
        'items': sorted([
            {
                'key': row['key'], 'quantity': row['quantity'],
                'price': str(row['unit_price']), 'name': row['product'].name,
                'sku': row['product'].sku,
                'options': [(option.kind, option.label) for option in row['options']],
            }
            for row in items
        ], key=lambda row: row['key']),
        'shipping': str(shipping_cost),
        'currency': settings.STRIPE_CURRENCY.lower(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def previous_checkout_session(attempt, fingerprint):
    order = attempt.order
    if order is None or attempt.cart_cleared_at or not order.stripe_session_id:
        return None
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.retrieve(order.stripe_session_id).to_dict()
    if session.get('payment_status') == 'paid' or session.get('status') == 'complete':
        return session
    if session.get('status') == 'open':
        if attempt.fingerprint == fingerprint and order.status != 'cancelled':
            return session
        try:
            session = stripe.checkout.Session.expire(order.stripe_session_id).to_dict()
        except stripe.InvalidRequestError:
            # Payment can complete between retrieval and expiration.
            session = stripe.checkout.Session.retrieve(order.stripe_session_id).to_dict()
            if session.get('status') != 'complete':
                raise
            return session
    if session.get('status') != 'expired':
        raise stripe.APIError('Unexpected checkout session status')
    if not order.paid_at:
        order.payment_status = 'cancelled'
        order.status = 'cancelled'
        order.save(update_fields=['payment_status', 'status', 'updated_at'])
        logger.info('Retired unpaid checkout for order %s before replacement', order.pk)
    return None


def clear_paid_checkout_cart(request, order):
    attempt = checkout_attempt(request)
    if attempt is None or attempt.order_id != order.pk or not order.paid_at:
        return
    with transaction.atomic():
        attempt = CheckoutAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.cart_cleared_at:
            return
        cart = Cart(request)
        for key, quantity in attempt.cart_snapshot.items():
            remaining = cart.data.get(key, 0) - quantity
            if remaining > 0:
                cart.data[key] = remaining
            else:
                cart.data.pop(key, None)
        cart.save()
        request.session.pop('delivery_method', None)
        request.session.save()
        attempt.cart_cleared_at = timezone.now()
        attempt.save(update_fields=['cart_cleared_at'])


def update_unpaid_checkout(order, payment_status, cancel_order=False):
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if not locked_order.paid_at and locked_order.payment_status not in {'paid', 'partially_refunded', 'refunded'}:
            locked_order.payment_status = payment_status
            fields = ['payment_status', 'updated_at']
            if cancel_order:
                locked_order.status = 'cancelled'
                fields.append('status')
            locked_order.save(update_fields=fields)
    order.refresh_from_db()

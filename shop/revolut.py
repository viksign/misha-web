import hashlib
import hmac
import json
from decimal import Decimal

import requests
from django.conf import settings
from django.urls import reverse


class RevolutError(RuntimeError):
    pass


def _amount_in_minor_units(value):
    amount = Decimal(str(value)).quantize(Decimal('0.01'))
    return int((amount * 100).to_integral_value())


def _base_headers():
    return {
        'Authorization': f'Bearer {settings.REVOLUT_API_KEY}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }


def build_revolut_checkout(order, request, return_url=None):
    if not settings.REVOLUT_API_KEY:
        raise RevolutError('REVOLUT_API_KEY is not configured.')

    payload = {
        'amount': _amount_in_minor_units(order.total),
        'currency': settings.REVOLUT_CURRENCY,
        'description': f'Order #{order.pk} from Misha Island Heritage',
        'metadata': {
            'order_id': str(order.pk),
            'email': order.email,
        },
        'customer': {
            'email': order.email,
            'name': order.shipping_name or '',
        },
        'redirect_url': return_url or request.build_absolute_uri(reverse('revolut_return')),
    }

    url = f"{settings.REVOLUT_API_BASE_URL}/api/1.0/orders"
    response = requests.post(url, headers=_base_headers(), data=json.dumps(payload), timeout=30)
    response.raise_for_status()
    data = response.json()

    checkout_url = data.get('checkout_url') or data.get('href')
    revolut_order_id = data.get('id') or data.get('order_id')
    if not checkout_url or not revolut_order_id:
        raise RevolutError('Revolut did not return a valid checkout URL.')

    return {
        'revolut_order_id': revolut_order_id,
        'checkout_url': checkout_url,
        'data': data,
    }


def verify_webhook_signature(raw_body, signature_header):
    if not settings.REVOLUT_WEBHOOK_SECRET:
        return False
    if not signature_header:
        return False

    try:
        signature = signature_header.replace('HMAC ', '', 1)
        expected = hmac.new(
            settings.REVOLUT_WEBHOOK_SECRET.encode('utf-8'),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


def fetch_revolut_order(revolut_order_id):
    if not settings.REVOLUT_API_KEY:
        raise RevolutError('REVOLUT_API_KEY is not configured.')
    url = f"{settings.REVOLUT_API_BASE_URL}/api/1.0/orders/{revolut_order_id}"
    response = requests.get(url, headers=_base_headers(), timeout=30)
    response.raise_for_status()
    return response.json()

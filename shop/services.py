import stripe
from django.conf import settings
from django.urls import reverse
from .models import Order


def create_stripe_checkout(order, request):
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError('STRIPE_SECRET_KEY is not configured.')
    stripe.api_key = settings.STRIPE_SECRET_KEY
    line_items = []
    for item in order.items.select_related('product'):
        line_items.append({
            'price_data': {
                'currency': settings.STRIPE_CURRENCY,
                'product_data': {'name': item.product_name, 'metadata': {'sku': item.sku}},
                'unit_amount': int(item.unit_price * 100),
            },
            'quantity': item.quantity,
        })
    base = settings.SITE_URL
    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=line_items,
        customer_email=order.email,
        metadata={'order_id': str(order.pk)},
        success_url=f'{base}{reverse("checkout_success")}?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{base}{reverse("checkout")}',
        shipping_address_collection={'allowed_countries': ['GB']},
    )
    order.stripe_session_id = session.id
    order.save(update_fields=['stripe_session_id'])
    return session

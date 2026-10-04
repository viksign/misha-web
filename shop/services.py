from decimal import Decimal
import logging
from datetime import datetime, timezone as datetime_timezone
from email.mime.image import MIMEImage
from io import BytesIO
from ipaddress import ip_address as parse_ip_address
from pathlib import Path
from smtplib import SMTPException
from urllib.parse import quote

import requests
import stripe
from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import IPGeolocation

logger = logging.getLogger(__name__)


def send_preorder_request_emails(signup):
    from .models import InterestSignup

    if not isinstance(signup, InterestSignup) or signup.interest_type != 'preorder' or not signup.product:
        return {'customer': False, 'admin': False}

    product_url = f'{settings.SITE_URL}{signup.product.get_absolute_url()}'
    customer_name = signup.user.get_full_name() if signup.user else ''
    greeting = f'Hello {customer_name},' if customer_name else 'Hello,'
    customer_message = (
        f'{greeting}\n\n'
        f'We have received your pre-order request for {signup.desired_quantity} '
        f'unit(s) of {signup.product.name}. This is a request, not a confirmed order. '
        f'We will be in touch with further updates.\n\n'
        f'View the piece: {product_url}\n\n'
        'MISHA Island Heritage'
    )
    admin_message = (
        'A new pre-order request has been submitted.\n\n'
        f'Customer: {customer_name or "Registered customer"}\n'
        f'Email: {signup.email}\n'
        f'Product: {signup.product.name}\n'
        f'Quantity requested: {signup.desired_quantity}\n'
        f'Requested: {timezone.localtime(signup.created_at).strftime("%d %b %Y, %H:%M %Z")}\n'
        f'Product page: {product_url}\n'
        f'Staff stock panel: {settings.SITE_URL}{reverse("control_stock")}'
    )
    email_jobs = [
        (
            'customer',
            f'Pre-order request received: {signup.product.name} | MISHA Island Heritage',
            customer_message,
            signup.email.strip(),
        ),
        (
            'admin',
            f'New pre-order request: {signup.product.name}',
            admin_message,
            settings.PREORDER_NOTIFICATION_EMAIL.strip(),
        ),
    ]
    delivery_status = {'customer': False, 'admin': False}

    for recipient_type, subject, message, recipient in email_jobs:
        if not recipient:
            logger.error('Cannot send pre-order %s email for signup %s: recipient is not configured', recipient_type, signup.pk)
            continue
        try:
            sent_count = send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        except (OSError, SMTPException):
            logger.exception('Failed to send pre-order %s email for signup %s', recipient_type, signup.pk)
        else:
            delivery_status[recipient_type] = sent_count == 1
            if sent_count != 1:
                logger.error('Pre-order %s email for signup %s was not sent', recipient_type, signup.pk)

    return delivery_status


def send_restock_notification(signup):
    from .models import InterestSignup

    if not isinstance(signup, InterestSignup) or signup.interest_type not in {'restock', 'preorder'} or signup.notified_at or not signup.product:
        return False
    token = signing.dumps({'product_id': signup.product_id}, salt='restock-add-to-bag')
    add_to_bag_url = f'{settings.SITE_URL}{reverse("restock_add_to_bag", args=[signup.product.slug])}?token={quote(token, safe="")}'
    context = {
        'signup': signup,
        'product': signup.product,
        'is_preorder': signup.interest_type == 'preorder',
        'add_to_bag_url': add_to_bag_url,
        'shop_url': settings.SITE_URL,
    }
    subject = (
        f'{signup.product.name} is ready to order | MISHA Island Heritage'
        if signup.interest_type == 'preorder'
        else f'{signup.product.name} is back in stock | MISHA Island Heritage'
    )
    message = EmailMultiAlternatives(
        subject,
        render_to_string('shop/restock_notification_email.txt', context),
        settings.DEFAULT_FROM_EMAIL,
        [signup.email],
    )
    message.extra_headers['X-SMTPAPI'] = '{"filters":{"clicktrack":{"settings":{"enable":0}}}}'
    message.attach_alternative(render_to_string('shop/restock_notification_email.html', context), 'text/html')
    message.send(fail_silently=False)
    return True


def send_order_confirmation(order_id):
    from .models import Order

    order = Order.objects.prefetch_related('items__product').get(pk=order_id)
    if not order.email or order.payment_status != 'paid':
        return False
    context = {
        'order': order,
        'logo_url': f'{settings.SITE_URL}/static/images/mih_logo.png',
    }
    subject = f'Order from Misha Island Heritage #{order.order_reference or order.pk}'
    message = EmailMultiAlternatives(
        subject,
        render_to_string('shop/order_confirmation_email.txt', context),
        settings.DEFAULT_FROM_EMAIL,
        [order.email],
        bcc=['mishaislandheritage@gmail.com'],
    )
    message.attach_alternative(render_to_string('shop/order_confirmation_email.html', context), 'text/html')

    logo_path = Path(settings.BASE_DIR) / 'static' / 'images' / 'mih_logo.png'
    if logo_path.exists():
        logo = MIMEImage(logo_path.read_bytes())
        logo.add_header('Content-ID', '<misha-logo>')
        logo.add_header('Content-Disposition', 'inline', filename='mih_logo.png')
        message.attach(logo)

    message.attach(
        f'order-{order.order_reference or order.pk}.pdf',
        build_order_pdf(order),
        'application/pdf',
    )
    message.send(fail_silently=False)
    return True


def send_tracking_email(order_id):
    from .models import Order

    order = Order.objects.get(pk=order_id)
    if not order.email or order.status != 'shipped' or not order.tracking_number:
        return False
    context = {
        'order': order,
        'logo_url': f'{settings.SITE_URL}/static/images/mih_logo.png',
    }
    subject = f'Your Misha Island Heritage order has shipped | {order.order_reference or order.pk}'
    message = EmailMultiAlternatives(
        subject,
        render_to_string('shop/tracking_email.txt', context),
        settings.DEFAULT_FROM_EMAIL,
        [order.email],
    )
    message.attach_alternative(render_to_string('shop/tracking_email.html', context), 'text/html')
    logo_path = Path(settings.BASE_DIR) / 'static' / 'images' / 'mih_logo.png'
    if logo_path.exists():
        logo = MIMEImage(logo_path.read_bytes())
        logo.add_header('Content-ID', '<misha-logo>')
        logo.add_header('Content-Disposition', 'inline', filename='mih_logo.png')
        message.attach(logo)
    message.send(fail_silently=False)
    return True


def refund_stripe_order(order, amount):
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError('Stripe is not configured.')
    if not order.stripe_session_id:
        raise RuntimeError('This order has no Stripe checkout session.')

    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.retrieve(order.stripe_session_id).to_dict()
    payment_intent = session.get('payment_intent')
    if not payment_intent:
        raise RuntimeError('Stripe has no payment intent for this order.')
    return stripe.Refund.create(
        payment_intent=payment_intent,
        amount=int((amount * 100).quantize(Decimal('1'))),
    ).to_dict()


def update_stripe_payment_details(order, payment_intent_id):
    if not settings.STRIPE_SECRET_KEY or not payment_intent_id:
        return False
    stripe.api_key = settings.STRIPE_SECRET_KEY
    payment_intent = stripe.PaymentIntent.retrieve(
        payment_intent_id,
        expand=['latest_charge.payment_method_details'],
    ).to_dict()
    charge = payment_intent.get('latest_charge')
    if not charge:
        return False
    payment_details = charge.get('payment_method_details') or {}
    card = payment_details.get('card') or {}
    brand = str(card.get('brand') or payment_details.get('type') or 'Card').title()
    last4 = card.get('last4')
    order.payment_method = f'{brand} (4*** **** **** {last4})' if last4 else brand
    order.transaction_id = charge.get('id') or payment_intent.get('id', '')
    created = charge.get('created')
    order.payment_date = datetime.fromtimestamp(created, tz=datetime_timezone.utc) if created else timezone.now()
    order.save(update_fields=['payment_method', 'transaction_id', 'payment_date', 'updated_at'])
    return True


def mark_order_paid(order, stock_actor=None):
    from .models import Order, Product

    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if not locked_order.stock_deducted_at:
            quantities = dict(locked_order.items.values('product_id').annotate(units=Sum('quantity')).values_list('product_id', 'units'))
            for product in Product.objects.select_for_update().filter(pk__in=quantities).order_by('pk'):
                if product.stock_quantity < quantities[product.pk]:
                    logger.error('Paid order %s exceeds available stock for product %s: ordered %s, available %s', locked_order.pk, product.pk, quantities[product.pk], product.stock_quantity)
                product.stock_quantity = max(0, product.stock_quantity - quantities[product.pk])
                product.save(update_fields=['stock_quantity', 'updated_at'], stock_actor=stock_actor, stock_reason='gift' if locked_order.payment_channel == 'gift' else 'sale', stock_order=locked_order)
            locked_order.stock_deducted_at = timezone.now()
        if locked_order.payment_status not in {'partially_refunded', 'refunded'}:
            locked_order.payment_status = 'paid'
        if locked_order.status in {'pending', 'paid'}:
            locked_order.status = 'pending'
        locked_order.paid_at = locked_order.paid_at or timezone.now()
        locked_order.save(update_fields=['payment_status', 'status', 'paid_at', 'stock_deducted_at', 'updated_at'])
    order.refresh_from_db()

def mark_stripe_order_paid(order):
    return mark_order_paid(order)


def build_order_pdf(order):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='OrderTitle', parent=styles['Title'], alignment=TA_CENTER, textColor=colors.HexColor('#183b3a'), spaceAfter=5 * mm))
    styles.add(ParagraphStyle(name='Small', parent=styles['BodyText'], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name='Right', parent=styles['BodyText'], alignment=2))
    story = []
    logo_path = Path(settings.BASE_DIR) / 'static' / 'images' / 'mih_logo.png'
    if logo_path.exists():
        logo = Image(str(logo_path), width=42 * mm, height=18 * mm, kind='proportional')
        logo.hAlign = 'CENTER'
        story.extend([logo, Spacer(1, 3 * mm)])
    story.append(Paragraph('Order confirmation', styles['OrderTitle']))
    payment_date = order.payment_date.strftime('%d %b %Y %I:%M %p UTC') if order.payment_date else 'Pending Stripe confirmation'
    payment_method = order.payment_method or 'Pending Stripe confirmation'
    transaction_id = order.transaction_id or 'Pending Stripe confirmation'
    story.append(Paragraph(f'<b>Order reference:</b> {order.order_reference or order.pk}<br/><b>Date:</b> {order.created_at:%d %B %Y}<br/><b>Status:</b> {order.get_status_display()}<br/><b>Payment method:</b> {payment_method}<br/><b>Transaction ID:</b> {transaction_id}<br/><b>Payment date:</b> {payment_date}', styles['BodyText']))
    story.append(Spacer(1, 7 * mm))

    item_rows = [[
        Paragraph('<b>Product</b>', styles['Small']),
        Paragraph('<b>Details</b>', styles['Small']),
        Paragraph('<b>Qty</b>', styles['Small']),
        Paragraph('<b>Unit</b>', styles['Small']),
        Paragraph('<b>Total</b>', styles['Small']),
    ]]
    for item in order.items.all():
        product = item.product
        details = f'SKU: {item.sku}'
        if product.description:
            details += f'<br/>{product.description[:180]}'
        if product.material:
            details += f'<br/>Material: {product.material}'
        if product.dimensions:
            details += f'<br/>Dimensions: {product.dimensions}'
        item_rows.append([
            Paragraph(item.product_name, styles['Small']),
            Paragraph(details, styles['Small']),
            Paragraph(str(item.quantity), styles['Small']),
            Paragraph(f'{order.currency} {item.unit_price:.2f}', styles['Small']),
            Paragraph(f'{order.currency} {item.line_total:.2f}', styles['Small']),
        ])
    items_table = Table(item_rows, colWidths=[31 * mm, 78 * mm, 13 * mm, 25 * mm, 25 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#183b3a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#c9d4d1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.extend([items_table, Spacer(1, 7 * mm)])
    totals = [
        ['Subtotal', f'{order.currency} {order.subtotal:.2f}'],
        ['Delivery', f'{order.currency} {order.delivery_cost:.2f}'],
        ['Total', f'{order.currency} {order.total:.2f}'],
    ]
    totals_table = Table(totals, colWidths=[45 * mm, 35 * mm], hAlign='RIGHT')
    totals_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, 2), (-1, 2), 1, colors.HexColor('#183b3a')),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 8 * mm))
    address = '<br/>'.join(filter(None, [order.shipping_name, order.shipping_address1, order.shipping_address2, order.shipping_city, order.shipping_postcode, order.shipping_country]))
    story.append(Paragraph(f'<b>Delivery address</b><br/>{address}', styles['BodyText']))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph('Thank you for shopping with MISHA Island Heritage.', styles['BodyText']))
    document.build(story)
    return buffer.getvalue()

GEOLOCATION_BATCH_URL = 'http://ip-api.com/batch?fields=status,country,city,query'
GEOLOCATION_BATCH_LIMIT = 100


def resolve_ip_locations(ip_addresses):
    """Return {ip: display label} for the given IPs, using a cached lookup table."""
    unique_ips = {ip for ip in ip_addresses if ip}
    if not unique_ips:
        return {}

    cached = {record.ip_address: record for record in IPGeolocation.objects.filter(ip_address__in=unique_ips)}
    missing_ips = [ip for ip in unique_ips if ip not in cached]
    to_query = []
    for ip in missing_ips:
        try:
            parsed = parse_ip_address(ip)
        except ValueError:
            continue
        record = IPGeolocation(ip_address=ip, is_private=not parsed.is_global)
        if record.is_private:
            record.save()
            cached[ip] = record
        else:
            to_query.append(ip)

    for batch_start in range(0, len(to_query), GEOLOCATION_BATCH_LIMIT):
        batch = to_query[batch_start:batch_start + GEOLOCATION_BATCH_LIMIT]
        try:
            response = requests.post(GEOLOCATION_BATCH_URL, json=batch, timeout=5)
            results = response.json() if response.ok else []
        except (requests.RequestException, ValueError):
            results = []
        for ip, result in zip(batch, results):
            record, _ = IPGeolocation.objects.update_or_create(
                ip_address=ip,
                defaults={
                    'country': result.get('country', '') if result.get('status') == 'success' else '',
                    'city': result.get('city', '') if result.get('status') == 'success' else '',
                    'is_private': False,
                },
            )
            cached[ip] = record

    return {ip: record.label for ip, record in cached.items()}


def create_stripe_checkout(order, request):
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured('STRIPE_SECRET_KEY is not configured.')

    stripe.api_key = settings.STRIPE_SECRET_KEY
    line_items = []
    for item in order.items.select_related('product'):
        line_items.append({
            'price_data': {
                'currency': settings.STRIPE_CURRENCY,
                'product_data': {
                    'name': item.product_name,
                    'metadata': {'sku': item.sku},
                },
                'unit_amount': int((item.unit_price * 100).quantize(Decimal('1'))),
            },
            'quantity': item.quantity,
        })

    if order.delivery_cost:
        line_items.append({
            'price_data': {
                'currency': settings.STRIPE_CURRENCY,
                'product_data': {'name': order.get_delivery_method_display() or 'Delivery'},
                'unit_amount': int((order.delivery_cost * 100).quantize(Decimal('1'))),
            },
            'quantity': 1,
        })

    base = settings.SITE_URL.rstrip('/')
    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=line_items,
        customer_email=order.email,
        metadata={'order_id': str(order.pk)},
        payment_intent_data={'metadata': {'order_id': str(order.pk)}},
        success_url=f'{base}{reverse("checkout_success")}?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{base}{reverse("checkout")}',
    )

    order.stripe_session_id = session.id
    order.save(update_fields=['stripe_session_id'])
    return session

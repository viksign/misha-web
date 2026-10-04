import json
import hashlib
import logging
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from smtplib import SMTPException
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.core import signing
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Avg, Case, Count, DecimalField, DurationField, ExpressionWrapper, F, IntegerField, Min, Q, Sum, Value, When
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.http import FileResponse, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import stripe

from .cart import Cart
from .assistant import contains_personal_data, gemini_chat_reply, gemini_rate_limited
from .shipping import checkout_shipping_configuration, destination_shipping_price, matching_shipping_rate, parcel_metrics, shipping_rate_payload
from .cookie_consent import has_cookie_consent
from .forms import CheckoutForm, CollectionManagementForm, CustomDesignRequestForm, CustomerProfileForm, CustomerReviewForm, DeliveryOptionManagementForm, DELIVERY_OPTIONS, EmailAuthenticationForm, EnquiryForm, InterestSignupForm, OrderManagementForm, ProductManagementForm, ProductShippingForm, ShippingPackagingManagementForm, ShippingRateManagementForm, SignUpForm, StockManagementForm, get_delivery_options

logger = logging.getLogger(__name__)
from .models import AnalyticsEvent, BlockedIP, Collection, CustomDesignRequest, CustomDesignRequestReply, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, Enquiry, EnquiryReply, InterestSignup, Order, OrderItem, PrivacyRequest, Product, ProductImage, SecurityEvent, ShippingPackaging, ShippingRate, WebhookEvent
from .services import build_order_pdf, create_stripe_checkout, mark_stripe_order_paid, refund_stripe_order, resolve_ip_locations, send_order_confirmation, send_preorder_request_emails, send_restock_notification, send_tracking_email, update_stripe_payment_details
from .models import IPGeolocation, StockMovement
from .forms import OfflineSaleForm, RefundNotesForm
from .user_dashboard import filtered_users, user_filters, user_summary
from .seo import collection_metadata, product_metadata, robots_response
from .sales import append_refund_notes, completed_sales_report, record_offline_sale, reconciliation_report, replenishment_budget, sales_window, sync_stripe_transactions


def home(request):
    featured_products = Product.objects.filter(active=True, featured=True).select_related('collection').prefetch_related('images')
    preorder_products = Product.objects.filter(active=True, preorder_enabled=True, stock_quantity=0).select_related('collection').prefetch_related('images')
    featured_collections = Collection.objects.filter(active=True, featured=True).order_by('name')

    return render(request, 'shop/home.html', {
        'featured_products': featured_products[:8],
        'featured_collections': featured_collections[:6],
        'preorder_products': preorder_products[:8],
        'featured_reviews': CustomerReview.objects.filter(status='approved', featured=True)[:3],
    })

def heritage(request):
    return render(request, 'shop/heritage.html')

@login_required(login_url='login')
def custom_design(request):
    form = CustomDesignRequestForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        custom_request = form.save(commit=False)
        custom_request.user = request.user
        custom_request.save()
        photo_note = 'Reference photo attached; view it in the staff control panel after signing in.' if custom_request.photo else 'No photo attached.'
        try:
            send_mail(
                f'Custom design request received: {custom_request.title}',
                f'Thank you for sharing your idea. We have received "{custom_request.title}" and will review it carefully.\n\nYou can follow the conversation here: {settings.SITE_URL}{reverse("custom_design")}',
                settings.DEFAULT_FROM_EMAIL,
                [request.user.email],
            )
            if settings.CUSTOM_REQUEST_NOTIFICATION_EMAIL:
                send_mail(
                    f'New custom design request: {custom_request.title}',
                    f'Customer: {request.user.email}\n\nProduct details:\n{custom_request.product_details}\n\nInspiration:\n{custom_request.inspiration or "Not provided."}\n\nMaterial: {custom_request.material or "Not provided."}\nBudget: {custom_request.budget or "Not provided."}\nPhoto: {photo_note}\n\nReview it in the control panel: {settings.SITE_URL}{reverse("control_custom_requests")}',
                    settings.DEFAULT_FROM_EMAIL,
                    [settings.CUSTOM_REQUEST_NOTIFICATION_EMAIL],
                )
        except (SMTPException, OSError):
            logger.exception('Failed to send custom design request emails for %s', custom_request.pk)
            messages.warning(request, 'Your request was saved, but an email could not be sent. We will still review it in the control panel.')
        else:
            messages.success(request, 'Your idea has been submitted. A confirmation email has been sent, and we will reply by email.')
        return redirect('custom_design')
    return render(request, 'shop/custom_design.html', {
        'form': form,
        'requests': request.user.custom_design_requests.prefetch_related('replies')[:10],
    })


@login_required(login_url='login')
def custom_design_photo(request, pk):
    custom_request = get_object_or_404(CustomDesignRequest, pk=pk)
    if not custom_request.photo or (not request.user.is_staff and custom_request.user_id != request.user.pk):
        return HttpResponse(status=404)

    response = FileResponse(
        custom_request.photo.open('rb'),
        as_attachment=True,
        filename=f'custom-design-reference-{custom_request.pk}',
        content_type='application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

@require_POST
def store_assistant(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'reply': 'Please try sending that again.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'reply': 'Please try sending that again.'}, status=400)

    if payload.get('action') == 'contact':
        contact_form = EnquiryForm(payload)
        if contact_form.is_valid():
            contact_form.save()
            return JsonResponse({'reply': 'Your message has been received. Thanks for getting in touch.', 'contact_submitted': True})
        return JsonResponse({'reply': 'Please check the required name, email, and message fields, then try again.'}, status=400)

    message = str(payload.get('message', '')).strip()[:500]
    query = message.casefold()
    if payload.get('action') == 'track_order':
        reference = str(payload.get('reference', '')).strip()
        email = str(payload.get('email', '')).strip()
        if len(reference) != 6 or not reference.isdigit():
            return JsonResponse({'reply': 'Enter the six-digit order reference from your confirmation email.'}, status=400)
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({'reply': 'Enter the email address used at checkout.'}, status=400)

        client_ip = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', 'unknown')
        ip_digest = hashlib.sha256(client_ip.encode()).hexdigest()[:32]
        rate_key = f'store-assistant-order:{ip_digest}'
        if not cache.add(rate_key, 1, timeout=3600):
            try:
                attempts = cache.incr(rate_key)
            except ValueError:
                cache.set(rate_key, 1, timeout=3600)
                attempts = 1
            if attempts > 12:
                return JsonResponse({
                    'reply': 'For your security, order lookups are temporarily limited. Please try again later or contact us for help.',
                    'contact_url': reverse('contact'),
                }, status=429)

        order = Order.objects.filter(order_reference=reference, email__iexact=email).first()
        if order is None:
            return JsonResponse({
                'reply': 'I could not find an order with those details. Check the reference and checkout email, or contact us for help.',
                'contact_url': reverse('contact'),
            })

        status_label = dict(Order.STATUS_CHOICES).get(order.status, order.status.title())
        delivery_label = dict(Order.DELIVERY_METHOD_CHOICES).get(order.delivery_method, order.delivery_method)
        reply = f'Order {order.order_reference}: {status_label}. Delivery: {delivery_label}.'
        if order.tracking_number:
            reply += f' Tracking number: {order.tracking_number}.'
        return JsonResponse({'reply': reply, 'tracking_url': order.tracking_url or ''})

    if not message:
        return JsonResponse({'reply': 'Type a question to get started.'}, status=400)

    if any(word in query for word in ('track', 'tracking', 'where is my order', 'delivery status', 'order status', 'shipped', 'shipment', 'dispatch')):
        return JsonResponse({
            'reply': 'I can look that up. Enter the six-digit order reference and checkout email below.',
            'needs_order_details': True,
        })

    if any(word in query for word in ('contact', 'human', 'talk to', 'speak to', 'someone', 'support', 'agent', 'person')):
        return JsonResponse({'reply': 'Send a message to the shop using this form.', 'needs_contact_form': True})

    if any(word in query for word in ('delivery', 'shipping', 'postage', 'deliver')):
        options = DeliveryOption.objects.filter(active=True)
        if options.exists():
            lines = []
            for option in options:
                price = 'free' if option.price == 0 else f'£{option.price:.2f}'
                line = f'{option.label}: {price}'
                if option.free_over is not None:
                    line += f' (free on orders over £{option.free_over:.2f})'
                lines.append(line)
            reply = 'Current delivery options:\n' + '\n'.join(lines)
        else:
            reply = 'Please check delivery options during checkout or contact us for current rates.'
        reply += '\nI can’t confirm delivery timing from the information available here.'
        return JsonResponse({'reply': reply, 'contact_url': reverse('contact')})

    if any(word in query for word in ('return', 'refund', 'exchange', 'cancel')):
        return JsonResponse({'reply': 'I do not have the store’s returns policy available here. Send the shop a message and we’ll help.', 'needs_contact_form': True})

    if any(word in query for word in ('payment', 'pay', 'checkout')):
        return JsonResponse({'reply': 'The available payment methods are listed at checkout.', 'checkout_url': reverse('checkout')})

    product_intent = any(word in query for word in ('product', 'jewellery', 'jewelry', 'necklace', 'earring', 'bracelet', 'ring', 'collection', 'material', 'size', 'price', 'stock'))
    if product_intent:
        products = list(Product.objects.filter(active=True).select_related('collection'))
        matches = [product for product in products if product.name.casefold() in query]
        if not matches:
            words = {word.strip('?!.,:;') for word in query.split()} - {
                'about', 'and', 'are', 'bracelet', 'bracelets', 'can', 'do', 'earring', 'earrings',
                'find', 'for', 'have', 'help', 'how', 'jewellery', 'jewelry', 'me', 'necklace',
                'necklaces', 'our', 'piece', 'pieces', 'product', 'products', 'show', 'tell', 'the',
                'what', 'which', 'with', 'you', 'your', 'ring', 'rings',
            }
            matches = [product for product in products if any(word in product.name.casefold().split() for word in words if len(word) > 2)]

        if matches:
            product = matches[0]
            details = [f'{product.name} is £{product.price:.2f} and is {"in stock" if product.in_stock else "currently out of stock"}.']
            specifications = []
            if product.material:
                specifications.append(f'material: {product.material}')
            if product.size:
                specifications.append(f'size: {product.size}')
            if product.length:
                specifications.append(f'length: {product.length}')
            if product.weight:
                specifications.append(f'weight: {product.weight}')
            if specifications:
                details.append('Listed details: ' + ', '.join(specifications) + '.')
            if product.short_description or product.description:
                details.append((product.short_description or product.description).strip()[:240])
            return JsonResponse({'reply': ' '.join(details), 'products': [{'name': product.name, 'url': product.get_absolute_url()}]})

        if any(phrase in query for phrase in ('show me', 'browse', 'what pieces', 'what products', 'what jewellery', 'what jewelry', 'collection')):
            suggestions = Product.objects.filter(active=True).order_by('-featured', 'name')[:4]
            return JsonResponse({
                'reply': 'Here are a few pieces from the collection. Which one would you like to know about?',
                'products': [{'name': product.name, 'url': product.get_absolute_url()} for product in suggestions],
            })
        return JsonResponse({'reply': 'Which piece are you asking about? Share its name and I’ll check the listed details.'})

    if any(word in query for word in ('hello', 'hi', 'hey', 'help')):
        return JsonResponse({'reply': 'Hello! I can help with products, delivery options, and order tracking. What would you like to know?'})

    if settings.GEMINI_API_KEY:
        if contains_personal_data(message):
            return JsonResponse({
                'reply': 'Please don’t send personal details here. Use order tracking for an order, or contact the shop for help.',
                'needs_contact_form': True,
            })
        if not payload.get('ai_consent'):
            return JsonResponse({
                'reply': 'To answer general questions, this message must be sent to Google Gemini. Tick the data-use notice below to continue.',
                'needs_ai_consent': True,
            })
        client_ip = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', 'unknown')
        if gemini_rate_limited(client_ip):
            return JsonResponse({
                'reply': 'The free AI chat limit has been reached for now. Please contact the shop for help.',
                'needs_contact_form': True,
            }, status=429)
        answer = gemini_chat_reply(message)
        if answer:
            return JsonResponse({'reply': answer, 'ai_generated': True})

    return JsonResponse({
        'reply': 'I don’t have that information here. Are you asking about a product, delivery, or an existing order? You can message the shop about anything else.',
        'needs_contact_form': True,
    })

def robots_txt(request):
    return robots_response(request)

def catalogue(request):
    qs = Product.objects.filter(active=True).select_related('collection').prefetch_related('images')
    collection = request.GET.get('collection', '')
    q = request.GET.get('q', '')
    sort = request.GET.get('sort', 'default')
    if collection:
        qs = qs.filter(collection__slug=collection)
    if q:
        qs = qs.filter(name__icontains=q)
    if sort == 'price_asc':
        qs = qs.order_by('price', 'name')
    elif sort == 'price_desc':
        qs = qs.order_by('-price', 'name')
    elif sort == 'preorder_first':
        qs = qs.annotate(
            preorder_rank=Case(
                When(preorder_enabled=True, stock_quantity=0, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        ).order_by('preorder_rank', '-featured', 'name')
    else:
        sort = 'default'
    return render(request, 'shop/catalogue.html', {
        'products': qs,
        'collections': Collection.objects.filter(active=True),
        'current_collection': collection,
        'current_sort': sort,
        'query': q,
    })

def collection_detail(request, slug):
    collection = get_object_or_404(Collection, slug=slug, active=True)
    return render(request, 'shop/collection.html', {'collection': collection, 'products': collection.products.filter(active=True).prefetch_related('images'), **collection_metadata(collection)})

def product_detail(request, slug):
    product = get_object_or_404(Product.objects.select_related('collection').prefetch_related('images'), slug=slug, active=True)
    return render(request, 'shop/product_detail.html', {'product': product, 'restock_form': InterestSignupForm(), **product_metadata(product)})

@login_required(login_url='login')
@require_POST
def preorder_interest_product(request, slug):
    product = get_object_or_404(Product.objects.select_related('collection'), slug=slug, active=True)
    if not product.preorder_enabled:
        messages.error(request, 'Pre-ordering is not currently available for this product.')
    elif product.in_stock:
        messages.info(request, f'{product.name} is available now. Add it to your bag to order.')
    else:
        try:
            desired_quantity = max(1, int(request.POST.get('quantity', 1)))
        except (TypeError, ValueError):
            desired_quantity = 1
        signup, _ = InterestSignup.objects.update_or_create(
            email=request.user.email.strip().lower(),
            user=request.user,
            product=product,
            interest_type='preorder',
            defaults={'desired_quantity': desired_quantity},
        )
        email_status = send_preorder_request_emails(signup)
        if all(email_status.values()):
            messages.success(request, f'You are on the pre-order list for {product.name} ({desired_quantity} unit(s)). Confirmation emails have been sent.')
        else:
            failed_deliveries = []
            if not email_status['customer']:
                failed_deliveries.append('your confirmation')
            if not email_status['admin']:
                failed_deliveries.append('the staff notification')
            messages.warning(request, f'Your pre-order request was saved, but {" and ".join(failed_deliveries)} could not be sent.')
    return redirect(f'{product.get_absolute_url()}#preorder')

@require_POST
def restock_notification(request, slug):
    product = get_object_or_404(Product, slug=slug, active=True)
    email = request.user.email if request.user.is_authenticated else request.POST.get('email', '')
    form = InterestSignupForm({'email': email})
    if form.is_valid():
        InterestSignup.objects.get_or_create(
            email=form.cleaned_data['email'],
            product=product,
            interest_type='restock',
        )
        messages.success(request, f'We will email you when {product.name} is back in stock.')
    else:
        messages.error(request, 'Enter a valid email address to get a stock notification.')
    return redirect(f'{product.get_absolute_url()}#stock-notification')

def restock_add_to_bag(request, slug):
    product = get_object_or_404(Product, slug=slug, active=True)
    if product.stock_quantity == 0:
        messages.error(request, f'{product.name} has just sold out. Please return to the product page for the next update.')
        return redirect(product.get_absolute_url())
    try:
        payload = signing.loads(request.GET.get('token', ''), salt='restock-add-to-bag', max_age=60 * 60 * 24 * 60)
    except signing.BadSignature:
        messages.error(request, 'This stock notification link has expired. Please visit the product page to shop.')
        return redirect(product.get_absolute_url())
    if payload.get('product_id') != product.pk:
        messages.error(request, 'This stock notification link is not valid for this product.')
        return redirect(product.get_absolute_url())
    Cart(request).add(product, 1)
    messages.success(request, f'{product.name} added to your bag.')
    return redirect('cart')

def send_restock_notifications(product):
    if product.stock_quantity == 0:
        return
    signups = InterestSignup.objects.filter(
        product=product,
        interest_type__in=['restock', 'preorder'],
        notified_at__isnull=True,
    ).order_by('created_at', 'id')
    for signup in signups:
        try:
            if send_restock_notification(signup):
                signup.notified_at = timezone.now()
                signup.save(update_fields=['notified_at'])
        except SMTPException:
            logger.exception('Failed to send restock email to %s', signup.email)

def add_to_cart(request, pk):
    product = get_object_or_404(Product, pk=pk, active=True)
    if request.method != 'POST': return redirect(product.get_absolute_url())
    quantity = max(1, int(request.POST.get('quantity', 1)))
    Cart(request).add(product, quantity)
    messages.success(request, f'{product.name} added to your bag.', extra_tags='bag-added')
    return redirect(request.POST.get('next') or 'checkout')

def delivery_cost(method, subtotal, country=None, items=None):
    option = DeliveryOption.objects.filter(code=method, active=True).first()
    if option is None:
        return Decimal('4.95')
    if country and items:
        rate, _ = matching_shipping_rate(items, country, method)
        if rate:
            return rate.price
    destination_price = destination_shipping_price(country, method)
    if destination_price is not None:
        return destination_price
    if option.free_over is not None and subtotal >= option.free_over:
        return Decimal('0.00')
    return option.price


def checkout_destination_country(form, user):
    data = form.data if form.is_bound else form.initial
    address_type = 'shipping' if data.get('ship_to_different_address') in (True, 'on', 'true', '1') else 'billing'
    address_field = 'shipping_address' if address_type == 'shipping' else 'billing_address'
    country_field = 'shipping_country' if address_type == 'shipping' else 'billing_country'
    saved_address_id = data.get(address_field)
    if user.is_authenticated and saved_address_id:
        address = CustomerAddress.objects.filter(
            pk=saved_address_id,
            user=user,
            address_type=address_type,
        ).first()
        if address:
            return address.country
    return data.get(country_field, '')


def generate_order_reference():
    while True:
        reference = f'{secrets.randbelow(900000) + 100000}'
        if not Order.objects.filter(order_reference=reference).exists():
            return reference


def cart_view(request):
    cart = Cart(request)
    items = list(cart.items())
    method = request.session.get('delivery_method', 'standard_uk')
    delivery_options = get_delivery_options()
    valid_methods = {value for value, label in delivery_options}
    if request.method == 'POST' and request.POST.get('shipping_method') in valid_methods:
        method = request.POST['shipping_method']
        request.session['delivery_method'] = method
        request.session.modified = True
    cost = delivery_cost(method, cart.subtotal)
    return render(request, 'shop/cart.html', {
        'cart': cart,
        'items': items,
        'suggested_products': Product.objects.filter(
            active=True,
            stock_quantity__gt=0,
        ).exclude(
            pk__in=[item['product'].pk for item in items],
        ).prefetch_related('images').order_by('-featured', 'name')[:4],
        'delivery_options': delivery_options,
        'selected_delivery': method,
        'delivery_cost': cost,
        'cart_total': cart.subtotal + cost,
    })

def update_cart(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST': Cart(request).set(product, max(0, int(request.POST.get('quantity', 0))))
    return redirect('cart')

def remove_from_cart(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST': Cart(request).remove(product)
    return redirect('cart')


def checkout(request):
    cart = Cart(request)
    items = list(cart.items())

    if not items:
        return redirect('cart')

    delivery_options = get_delivery_options()
    delivery_rates = []
    for code, label in delivery_options:
        option = DeliveryOption.objects.get(code=code)
        delivery_rates.append({
            'code': code,
            'label': label,
            'price': str(option.price),
            'free_over': str(option.free_over) if option.free_over is not None else None,
        })
    current_parcel = parcel_metrics(items)
    configured_shipping_rates = shipping_rate_payload()
    country_shipping_config = checkout_shipping_configuration()

    if request.user.is_authenticated:
        profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
        profile_address = {
            'house_number': profile.house_number,
            'street_name': profile.street_name,
            'city': profile.city,
            'postcode': profile.postcode,
            'country': profile.country,
        }
        if all(profile_address.values()):
            for address_type in ('billing', 'shipping'):
                matching_addresses = CustomerAddress.objects.filter(
                    user=request.user,
                    address_type=address_type,
                    house_number=profile.house_number,
                    street_name=profile.street_name,
                    city=profile.city,
                    postcode=profile.postcode,
                    country=profile.country,
                ).order_by('id')
                if not matching_addresses.exists():
                    CustomerAddress.objects.create(
                        user=request.user,
                        address_type=address_type,
                        label='Saved address',
                        **profile_address,
                    )
                else:
                    matching_addresses.exclude(pk=matching_addresses.first().pk).delete()

    if request.method == 'POST':
        form = CheckoutForm(request.POST, user=request.user)
        if form.is_valid():
            unavailable = [
                f"{row['product'].name} only has {row['product'].stock_quantity} available."
                for row in items
                if row['quantity'] > row['product'].stock_quantity
            ]
            if unavailable:
                for message in unavailable:
                    messages.error(request, message)
                return render(request, 'shop/checkout.html', {
                    'form': form,
                    'cart': cart,
                    'items': items,
                    'delivery_cost': delivery_cost(form.cleaned_data.get('shipping_method', 'standard_uk'), cart.subtotal, checkout_destination_country(form, request.user), items),
                    'delivery_rates': delivery_rates,
                    'shipping_rate_data': configured_shipping_rates,
                    'country_shipping_config': country_shipping_config,
                    'parcel_metrics': current_parcel,
                })

            with transaction.atomic():
                order = form.save(commit=False)
                order.user = request.user if request.user.is_authenticated else None
                order.order_reference = generate_order_reference()
                order.delivery_method = form.cleaned_data['shipping_method']
                billing = CustomerAddress.objects.filter(
                    pk=form.cleaned_data.get('billing_address'),
                    user=request.user,
                    address_type='billing',
                ).first() if request.user.is_authenticated and form.cleaned_data.get('billing_address') else None
                full_name = request.user.get_full_name() if request.user.is_authenticated else ''
                order.billing_name = form.cleaned_data.get('billing_name') or full_name or order.shipping_name
                if billing:
                    order.billing_address1 = f'{billing.house_number} {billing.street_name}'.strip()
                    order.billing_address2 = ''
                    order.billing_city = billing.city
                    order.billing_postcode = billing.postcode
                    order.billing_country = billing.country
                else:
                    order.billing_address1 = form.cleaned_data['billing_address1']
                    order.billing_address2 = form.cleaned_data['billing_address2']
                    order.billing_city = form.cleaned_data['billing_city']
                    order.billing_postcode = form.cleaned_data['billing_postcode']
                    order.billing_country = form.cleaned_data['billing_country']
                    if request.user.is_authenticated:
                        billing_values = {
                            'label': 'Billing address',
                            'house_number': order.billing_address1.split(' ', 1)[0],
                            'street_name': order.billing_address1.split(' ', 1)[1] if ' ' in order.billing_address1 else order.billing_address1,
                            'city': order.billing_city,
                            'postcode': order.billing_postcode,
                            'country': order.billing_country,
                        }
                        billing_address = CustomerAddress.objects.filter(user=request.user, address_type='billing').first()
                        if billing_address:
                            for key, value in billing_values.items():
                                setattr(billing_address, key, value)
                            billing_address.save()
                        else:
                            CustomerAddress.objects.create(user=request.user, address_type='billing', **billing_values)

                if form.cleaned_data.get('ship_to_different_address'):
                    shipping = CustomerAddress.objects.filter(
                        pk=form.cleaned_data.get('shipping_address'),
                        user=request.user,
                        address_type='shipping',
                    ).first() if request.user.is_authenticated and form.cleaned_data.get('shipping_address') else None
                    if shipping:
                        order.shipping_name = order.billing_name
                        order.shipping_address1 = f'{shipping.house_number} {shipping.street_name}'.strip()
                        order.shipping_address2 = ''
                        order.shipping_city = shipping.city
                        order.shipping_postcode = shipping.postcode
                        order.shipping_country = shipping.country
                    else:
                        order.shipping_name = form.cleaned_data['shipping_name'] or order.billing_name
                        order.shipping_address1 = form.cleaned_data['shipping_address1']
                        order.shipping_address2 = form.cleaned_data['shipping_address2']
                        order.shipping_city = form.cleaned_data['shipping_city']
                        order.shipping_postcode = form.cleaned_data['shipping_postcode']
                        order.shipping_country = form.cleaned_data['shipping_country']
                        if request.user.is_authenticated and form.cleaned_data['save_shipping_address']:
                            CustomerAddress.objects.create(
                                user=request.user,
                                address_type='shipping',
                                label=f'{order.shipping_address1}, {order.shipping_city}',
                                house_number=order.shipping_address1.split(' ', 1)[0],
                                street_name=order.shipping_address1.split(' ', 1)[1] if ' ' in order.shipping_address1 else order.shipping_address1,
                                city=order.shipping_city,
                                postcode=order.shipping_postcode,
                                country=order.shipping_country,
                            )
                else:
                    order.shipping_name = order.billing_name
                    order.shipping_address1 = order.billing_address1
                    order.shipping_address2 = order.billing_address2
                    order.shipping_city = order.billing_city
                    order.shipping_postcode = order.billing_postcode
                    order.shipping_country = order.billing_country
                order.delivery_cost = delivery_cost(order.delivery_method, cart.subtotal, order.shipping_country, items)
                order.subtotal = cart.subtotal
                order.shipping = order.delivery_cost
                order.total = order.subtotal + order.shipping
                order.currency = (settings.STRIPE_CURRENCY or 'gbp').upper()
                order.payment_status = 'pending'
                order.save()

                for row in items:
                    product = row['product']
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        product_name=product.name,
                        sku=product.sku,
                        unit_price=product.price,
                        unit_cost=product.cost_price,
                        quantity=row['quantity'],
                    )
                try:
                    checkout_session = create_stripe_checkout(order, request)
                except (stripe.StripeError, ImproperlyConfigured) as error:
                    transaction.set_rollback(True)
                    checkout_session = None
                    logger.error(
                        'Checkout session creation failed: type=%s code=%s',
                        type(error).__name__, getattr(error, 'code', None),
                    )
                    form.add_error(None, 'We could not start your payment. Your bag has been kept. Please try again shortly or contact customer care.')

            if checkout_session is not None:
                cart.clear()
                request.session.pop('delivery_method', None)
                request.session.modified = True
                return redirect(checkout_session.url)
    else:
        initial = {
            'email': request.user.email if request.user.is_authenticated else '',
            'shipping_method': request.session.get('delivery_method', 'standard_uk'),
            'ship_to_different_address': False,
        }
        if request.user.is_authenticated:
            profile = getattr(request.user, 'customer_profile', None)
            if profile:
                initial.update({
                    'shipping_name': ' '.join(
                        part for part in [request.user.first_name, request.user.last_name] if part
                    ),
                    'shipping_address1': ' '.join(
                        part for part in [profile.house_number, profile.street_name] if part
                    ),
                    'shipping_address2': profile.address,
                    'shipping_city': profile.city,
                    'shipping_postcode': profile.postcode,
                    'shipping_country': profile.country,
                    'billing_name': ' '.join(part for part in [request.user.first_name, request.user.last_name] if part),
                    'billing_address1': ' '.join(part for part in [profile.house_number, profile.street_name] if part),
                    'billing_address2': profile.address,
                    'billing_city': profile.city,
                    'billing_postcode': profile.postcode,
                    'billing_country': profile.country,
                })
                initial['billing_name'] = ' '.join(
                    part for part in [request.user.first_name, request.user.last_name] if part
                )
        form = CheckoutForm(initial=initial, user=request.user)

    selected_method = form.data.get('shipping_method', form.initial.get('shipping_method', request.session.get('delivery_method', 'standard_uk')))
    selected_country = checkout_destination_country(form, request.user)
    saved_addresses = {'billing': [], 'shipping': []}
    if request.user.is_authenticated:
        for address_type in saved_addresses:
            saved_addresses[address_type] = list(CustomerAddress.objects.filter(
                user=request.user,
                address_type=address_type,
            ).values('id', 'country'))
    selected_delivery_cost = delivery_cost(selected_method, cart.subtotal, selected_country, items)
    return render(request, 'shop/checkout.html', {
        'form': form,
        'cart': cart,
        'items': items,
        'delivery_cost': selected_delivery_cost,
        'checkout_total': cart.subtotal + selected_delivery_cost,
        'delivery_rates': delivery_rates,
        'shipping_rate_data': configured_shipping_rates,
        'country_shipping_config': country_shipping_config,
        'parcel_metrics': current_parcel,
        'saved_checkout_addresses': saved_addresses,
        'billing_profile': getattr(request.user, 'customer_profile', None) if request.user.is_authenticated else None,
    })


def checkout_success(request):
    session_id = request.GET.get('session_id')
    order = None

    if session_id:
        order = Order.objects.filter(stripe_session_id=session_id).first()
        if order and settings.STRIPE_SECRET_KEY:
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                session = stripe.checkout.Session.retrieve(session_id).to_dict()
                if session.get('payment_status') == 'paid':
                    mark_stripe_order_paid(order)
                update_stripe_payment_details(order, session.get('payment_intent'))
                if session.get('payment_status') == 'paid' and not order.payment_confirmation_sent_at and send_order_confirmation(order.pk):
                    order.payment_confirmation_sent_at = timezone.now()
                    order.save(update_fields=['payment_confirmation_sent_at', 'updated_at'])
                order.refresh_from_db()
            except stripe.error.StripeError:
                pass
    elif request.GET.get('order_id'):
        order = get_object_or_404(Order, pk=request.GET.get('order_id'))

    if order is None:
        return render(request, 'shop/success.html', {'order': None})

    return render(request, 'shop/success.html', {'order': order})


@csrf_exempt
def stripe_webhook(request):
    if not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse(status=400)

    try:
        event = stripe.Webhook.construct_event(
            request.body,
            request.META.get('HTTP_STRIPE_SIGNATURE', ''),
            settings.STRIPE_WEBHOOK_SECRET,
        ).to_dict()
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    event_id = event.get('id')
    event_type = event.get('type')
    if not event_id or not event_type:
        return HttpResponse(status=400)

    existing = WebhookEvent.objects.filter(event_id=event_id).first()
    if existing:
        return JsonResponse({'received': True, 'status': existing.status})

    payload = event.get('data', {}).get('object', {})
    metadata = payload.get('metadata') or {}
    order = None

    if metadata.get('order_id'):
        order = Order.objects.filter(pk=metadata.get('order_id')).first()
    if order is None and payload.get('id'):
        order = Order.objects.filter(stripe_session_id=payload.get('id')).first()
    if order is None and payload.get('id'):
        order = Order.objects.filter(transaction_id=payload.get('id')).first()

    if event_type in {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}:
        if order and payload.get('payment_status') == 'paid':
            mark_stripe_order_paid(order)
            try:
                update_stripe_payment_details(order, payload.get('payment_intent'))
            except stripe.error.StripeError:
                pass
            if not order.payment_confirmation_sent_at and send_order_confirmation(order.pk):
                order.payment_confirmation_sent_at = timezone.now()
                order.save(update_fields=['payment_confirmation_sent_at', 'updated_at'])
            Cart(request).clear()
    elif event_type in {'checkout.session.expired', 'checkout.session.async_payment_failed'}:
        if order and order.payment_status != 'failed':
            order.payment_status = 'failed'
            order.status = 'cancelled'
            order.save(update_fields=['payment_status', 'status'])
    elif event_type == 'payment_intent.payment_failed':
        if order and order.payment_status != 'failed':
            order.payment_status = 'failed'
            order.status = 'cancelled'
            order.save(update_fields=['payment_status', 'status'])
    elif event_type == 'charge.refunded':
        if order:
            refunded_amount = Decimal(str(payload.get('amount_refunded', 0))) / Decimal('100')
            fully_refunded = refunded_amount >= order.total
            refund_data = (payload.get('refunds', {}).get('data') or [{}])[0]
            order.refunded_amount = refunded_amount
            order.refund_status = 'refunded' if fully_refunded else 'partial'
            order.payment_status = 'refunded' if fully_refunded else 'partially_refunded'
            order.status = 'return'
            order.stripe_refund_id = refund_data.get('id', '')
            order.save(update_fields=['refunded_amount', 'refund_status', 'payment_status', 'status', 'stripe_refund_id', 'updated_at'])

    WebhookEvent.objects.create(
        event_id=event_id,
        event_type=event_type,
        payload=event,
        status='processed',
    )
    return JsonResponse({'received': True})


def enquiry(request):
    if request.method == 'POST':
        form = EnquiryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Thank you. We will be in touch shortly.')
            return redirect('contact')
    else:
        form = EnquiryForm()
    return render(request, 'shop/contact.html', {'form': form})


def account(request):
    if not request.user.is_authenticated:
        return render(request, 'registration/account_access.html', {
            'login_form': EmailAuthenticationForm(request),
            'signup_form': SignUpForm(),
        })
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    edit_mode = request.GET.get('edit') == '1'
    profile_form = CustomerProfileForm(request.POST or None, instance=profile)
    if request.method == 'POST' and profile_form.is_valid():
        profile_form.save()
        messages.success(request, 'Your customer details have been saved.')
        return redirect('account')
    if request.method == 'POST':
        edit_mode = True
    return render(request, 'shop/account.html', {
        'orders': request.user.orders.all().prefetch_related('items'),
        'profile': profile,
        'profile_form': profile_form,
        'edit_mode': edit_mode,
        'privacy_request_pending': PrivacyRequest.objects.filter(user=request.user, status='pending').exists(),
    })


def request_privacy_erasure(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if request.method == 'POST' and not PrivacyRequest.objects.filter(user=request.user, status='pending').exists():
        PrivacyRequest.objects.create(user=request.user, request_type='erasure')
        messages.success(request, 'Your personal data removal request has been received.')
    return redirect('account')


@csrf_exempt
def analytics_click(request):
    if request.method == 'POST' and has_cookie_consent(request, 'analytics'):
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (TypeError, ValueError):
            return JsonResponse({'ok': False}, status=400)
        AnalyticsEvent.objects.create(
            user=request.user if request.user.is_authenticated else None,
            event_type='click',
            product=Product.objects.filter(pk=payload.get('product_id')).first() if payload.get('product_id') else None,
            path=str(payload.get('path', ''))[:500],
            target=str(payload.get('target', ''))[:255],
            ip_address=request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR'),
            country=request.META.get('HTTP_CF_IPCOUNTRY', '')[:80],
            referrer=request.META.get('HTTP_REFERER', '')[:500],
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
        return JsonResponse({'ok': True})
    if request.method == 'POST':
        return JsonResponse({'ok': True, 'ignored': True})
    return JsonResponse({'ok': False}, status=405)


def submit_review(request):
    form = CustomerReviewForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Thank you. Your feedback has been submitted for approval.')
        return redirect('feedback')
    return render(request, 'shop/feedback.html', {
        'form': form,
        'approved_reviews': CustomerReview.objects.filter(status='approved'),
    })


@staff_member_required(login_url='login')
def control_panel(request):
    paid_orders = Order.objects.filter(payment_status='paid')
    stage_counts = paid_orders.aggregate(
        waiting=Count('pk', filter=Q(status__in=['pending', 'paid'])),
        processing=Count('pk', filter=Q(status='processing')),
        shipped=Count('pk', filter=Q(status='shipped')),
        completed=Count('pk', filter=Q(status='completed')),
    )
    duration_fields = {
        'waiting': ('paid_at', 'processing_started_at'),
        'processing': ('processing_started_at', 'shipped_at'),
        'delivery': ('shipped_at', 'completed_at'),
        'total': ('paid_at', 'completed_at'),
    }
    duration_expressions = {
        name: Avg(
            ExpressionWrapper(F(end) - F(start), output_field=DurationField()),
            filter=Q(**{f'{start}__isnull': False, f'{end}__isnull': False, f'{end}__gte': F(start)}),
        )
        for name, (start, end) in duration_fields.items()
    }
    duration_averages = paid_orders.aggregate(**duration_expressions)
    now = timezone.now()
    duration_labels = {
        name: timesince(now - duration, now) if duration is not None else 'Not recorded'
        for name, duration in duration_averages.items()
    }
    workload = list(paid_orders.filter(Q(assigned_to__isnull=False) | Q(status='processing')).values(
        'assigned_to_id', 'assigned_to__first_name', 'assigned_to__last_name',
    ).annotate(
        order_count=Count('pk', filter=Q(status='processing')),
        shipped_count=Count('pk', filter=Q(status='shipped')),
        completed_count=Count('pk', filter=Q(status='completed')),
        oldest_started_at=Min('processing_started_at', filter=Q(status='processing')),
        average_processing=duration_expressions['processing'],
        average_total=duration_expressions['total'],
    ).order_by('-order_count', 'assigned_to_id'))
    for member in workload:
        full_name = ' '.join(filter(None, [member['assigned_to__first_name'], member['assigned_to__last_name']])).strip()
        member['name'] = full_name or (f"Staff #{member['assigned_to_id']} (name not set)" if member['assigned_to_id'] else 'Unassigned')
        for field in ['average_processing', 'average_total']:
            duration = member[field]
            member[field] = timesince(now - duration, now) if duration is not None else 'Not recorded'
    queue = list(paid_orders.filter(status__in=['pending', 'paid', 'processing', 'shipped']).select_related('assigned_to').order_by('paid_at', 'created_at')[:20])
    for order in queue:
        started_at = {'pending': order.paid_at, 'paid': order.paid_at, 'processing': order.processing_started_at, 'shipped': order.shipped_at}.get(order.status)
        order.stage_duration = timesince(started_at, now) if started_at else 'Not recorded'
    inventory_counts = Product.objects.filter(active=True).aggregate(
        units=Sum('stock_quantity', default=0), products=Count('pk'),
        available=Count('pk', filter=Q(stock_quantity__gt=0)),
        low=Count('pk', filter=Q(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level'))),
        empty=Count('pk', filter=Q(stock_quantity=0)),
        healthy=Count('pk', filter=Q(stock_quantity__gt=F('reorder_level'))),
    )
    return render(request, 'shop/control_panel.html', {
        'product_count': Product.objects.count(),
        'active_product_count': Product.objects.filter(active=True).count(),
        'low_stock_count': Product.objects.filter(active=True, stock_quantity__lte=F('reorder_level')).count(),
        'order_count': Order.objects.count(),
        'pending_order_count': Order.objects.filter(payment_status='paid', status__in=['pending', 'paid', 'processing']).count(),
        'delivery_count': DeliveryOption.objects.filter(active=True).count(),
        'visitor_count': AnalyticsEvent.objects.filter(event_type='page_view').count(),
        'pending_review_count': CustomerReview.objects.filter(status='pending').count(),
        'custom_request_count': CustomDesignRequest.objects.count(),
        'fulfilment_counts': stage_counts,
        'fulfilment_averages': duration_labels,
        'processing_workload': workload,
        'fulfilment_queue': queue,
        'inventory_counts': inventory_counts,
        'inventory_preorder_interest_count': InterestSignup.objects.filter(interest_type='preorder', product__active=True, product__stock_quantity=0).count(),
        'inventory_products': Product.objects.filter(active=True, stock_quantity__lte=F('reorder_level')).annotate(
            preorder_interest_count=Count('interest_signups', filter=Q(interest_signups__interest_type='preorder', stock_quantity=0)),
        ).select_related('collection').order_by('stock_quantity', 'name', 'pk')[:12],
        'unassigned_processing_count': paid_orders.filter(status='processing', assigned_to__isnull=True).count(),
        'dashboard_updated_at': now,
    })


@staff_member_required(login_url='login')
def control_reviews(request):
    if request.method == 'POST':
        if request.POST.get('action') == 'reply_feedback':
            enquiry = get_object_or_404(Enquiry, pk=request.POST.get('enquiry_id'))
            reply_message = request.POST.get('message', '').strip()
            if not reply_message:
                messages.error(request, 'Enter a reply message before sending.')
            else:
                try:
                    send_mail(
                        f'Re: {enquiry.subject or "Your enquiry"} — MISHA Island Heritage',
                        reply_message,
                        settings.DEFAULT_FROM_EMAIL,
                        [enquiry.email],
                    )
                except (SMTPException, OSError):
                    logger.exception('Failed to send enquiry reply email to %s', enquiry.email)
                    messages.error(request, 'Reply saved, but the email could not be sent. Check email settings.')
                else:
                    messages.success(request, f'Reply sent to {enquiry.email}.')
                EnquiryReply.objects.create(enquiry=enquiry, message=reply_message)
                enquiry.handled = True
                enquiry.save(update_fields=['handled'])
            return redirect('control_reviews')
        review = get_object_or_404(CustomerReview, pk=request.POST.get('review_id'))
        review.status = request.POST.get('status', review.status)
        review.featured = request.POST.get('featured') == 'on'
        review.save(update_fields=['status', 'featured'])
        messages.success(request, 'Review moderation updated.')
        return redirect('control_reviews')

    feedback = list(Enquiry.objects.all().prefetch_related('replies')[:200])
    linked_users = {
        user.email.lower(): user
        for user in User.objects.filter(email__in={item.email for item in feedback if item.email})
    }
    for item in feedback:
        item.linked_user = linked_users.get(item.email.lower())
    return render(request, 'shop/control_reviews.html', {
        'reviews': CustomerReview.objects.all(),
        'feedback': feedback,
    })


@staff_member_required(login_url='login')
def control_custom_requests(request):
    if request.method == 'POST':
        custom_request = get_object_or_404(CustomDesignRequest, pk=request.POST.get('request_id'))
        status = request.POST.get('status', custom_request.status)
        valid_statuses = {value for value, label in CustomDesignRequest.STATUS_CHOICES}
        custom_request.status = status if status in valid_statuses else custom_request.status
        custom_request.save(update_fields=['status', 'updated_at'])
        reply_message = request.POST.get('message', '').strip()
        if reply_message:
            CustomDesignRequestReply.objects.create(
                request=custom_request,
                staff_user=request.user,
                message=reply_message,
            )
            try:
                send_mail(
                    f'Update on your custom design request: {custom_request.title}',
                    reply_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [custom_request.user.email],
                )
            except (SMTPException, OSError):
                logger.exception('Failed to send custom request reply to %s', custom_request.user.email)
                messages.error(request, 'Reply saved, but the email could not be sent. Check email settings.')
            else:
                messages.success(request, f'Reply sent to {custom_request.user.email}.')
        else:
            messages.success(request, 'Custom request status updated.')
        return redirect('control_custom_requests')

    return render(request, 'shop/control_custom_requests.html', {
        'custom_requests': CustomDesignRequest.objects.select_related('user').prefetch_related('replies__staff_user')[:200],
        'status_choices': CustomDesignRequest.STATUS_CHOICES,
    })


ANALYTICS_PAGE_SIZES = [15, 30, 50, 100]


def _parse_range_boundary(value, end_of_day=False):
    if not value:
        return None
    try:
        parsed_date = parse_date(value)
        parsed = datetime.combine(parsed_date, time.max if end_of_day else time.min) if parsed_date else parse_datetime(value)
    except ValueError:
        return None
    if parsed is None:
        return None
    if end_of_day and parsed.isoformat(timespec='minutes') == value:
        parsed = parsed.replace(second=59, microsecond=999999)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _filter_analytics_events(params):
    start_str = params.get('start', '').strip()
    end_str = params.get('end', '').strip()
    if not start_str and not end_str:
        today = timezone.localdate()
        start_str = timezone.make_aware(datetime.combine(today, time.min)).strftime('%Y-%m-%dT%H:%M')
        end_str = timezone.make_aware(datetime.combine(today, time(23, 59))).strftime('%Y-%m-%dT%H:%M')
    start_dt = _parse_range_boundary(start_str)
    end_dt = _parse_range_boundary(end_str, end_of_day=True)

    filtered_events = AnalyticsEvent.objects.all()
    if start_dt:
        filtered_events = filtered_events.filter(created_at__gte=start_dt)
    if end_dt:
        filtered_events = filtered_events.filter(created_at__lte=end_dt)

    filtered_user = None
    filtered_user_id = params.get('user')
    if filtered_user_id:
        filtered_user = get_object_or_404(User, pk=filtered_user_id)
        filtered_events = filtered_events.filter(user=filtered_user)
    country = params.get('country', '').strip()
    city = params.get('city', '').strip()
    if country or city:
        locations = IPGeolocation.objects.filter(is_private=False)
        if country:
            locations = locations.filter(country__icontains=country)
        if city:
            locations = locations.filter(city__icontains=city)
        filtered_events = filtered_events.filter(ip_address__in=locations.values('ip_address'))
    query = params.get('q', '').strip()
    if query:
        matching_locations = IPGeolocation.objects.filter(
            Q(country__icontains=query) | Q(city__icontains=query)
        )
        if query.casefold() in 'private network':
            matching_locations = matching_locations | IPGeolocation.objects.filter(is_private=True)
        location_match = Q(ip_address__in=matching_locations.values('ip_address'))
        if query.casefold() in 'unknown':
            known_locations = IPGeolocation.objects.filter(is_private=True) | IPGeolocation.objects.exclude(country='', city='')
            location_match |= Q(ip_address__isnull=True) | ~Q(ip_address__in=known_locations.values('ip_address'))
        matching_types = [value for value, label in AnalyticsEvent.EVENT_TYPES if query.casefold() in label.casefold()]
        filtered_events = filtered_events.filter(
            location_match | Q(event_type__in=matching_types) | Q(path__icontains=query)
            | Q(target__icontains=query) | Q(product__name__icontains=query)
            | Q(user__email__icontains=query) | Q(ip_address__icontains=query)
        )
    return filtered_events, filtered_user, start_str, end_str


@staff_member_required(login_url='login')
def control_analytics(request):
    if request.method == 'POST' and request.POST.get('action') == 'unblock_ip':
        blocked_ip = get_object_or_404(BlockedIP, pk=request.POST.get('block_id'))
        blocked_ip.blocked_until = timezone.now()
        blocked_ip.save(update_fields=['blocked_until'])
        cache.delete(f'security:blocked:{blocked_ip.ip_address}')
        cache.delete(f'security:block-lock:{blocked_ip.ip_address}')
        SecurityEvent.objects.create(
            ip_address=blocked_ip.ip_address,
            event_type='unblocked',
            reason='Staff removed the temporary IP block',
        )
        messages.success(request, f'IP address {blocked_ip.ip_address} unblocked.')
        return redirect('control_analytics')

    if request.method == 'POST' and request.POST.get('action') == 'purge':
        purge_events, _, start_str, end_str = _filter_analytics_events(request.POST)
        deleted_count = purge_events.delete()[0]
        messages.success(request, f'{deleted_count} activity record(s) purged.')
        query_params = {key: request.POST.get(key, '').strip() for key in ['user', 'country', 'city', 'q']}
        query_params.update(start=start_str, end=end_str)
        query_params = {key: value for key, value in query_params.items() if value}
        redirect_url = reverse('control_analytics')
        if query_params:
            redirect_url += '?' + urlencode(query_params)
        return redirect(redirect_url)

    filtered_events, filtered_user, start_str, end_str = _filter_analytics_events(request.GET)
    events = filtered_events.select_related('user', 'product').order_by('-created_at')
    try:
        page_size = int(request.GET.get('page_size', 30))
    except (TypeError, ValueError):
        page_size = 30
    if page_size not in ANALYTICS_PAGE_SIZES:
        page_size = 30
    paginator = Paginator(events, page_size)
    event_page = paginator.get_page(request.GET.get('page'))
    locations = resolve_ip_locations(event.ip_address for event in event_page)
    for event in event_page:
        event.location = locations.get(event.ip_address, 'Unknown')
    country = request.GET.get('country', '').strip()
    city = request.GET.get('city', '').strip()
    query = request.GET.get('q', '').strip()
    location_params = {key: value for key, value in {'country': country, 'city': city, 'q': query}.items() if value}
    chart_params = {'start': start_str, 'end': end_str, **({'user': filtered_user.pk} if filtered_user else {}), **location_params}
    clear_dates_params = {key: value for key, value in chart_params.items() if key not in {'start', 'end'}}
    clear_locations_params = {key: value for key, value in chart_params.items() if key not in {'country', 'city'}}
    return render(request, 'shop/control_analytics.html', {
        'events': event_page,
        'page_views': filtered_events.filter(event_type='page_view').count(),
        'clicks': filtered_events.filter(event_type='click').count(),
        'unique_ips': filtered_events.exclude(ip_address__isnull=True).values('ip_address').distinct().count(),
        'event_page': event_page,
        'filtered_user': filtered_user,
        'active_ip_blocks': BlockedIP.objects.filter(blocked_until__gt=timezone.now()).order_by('-blocked_at'),
        'security_events': SecurityEvent.objects.select_related().order_by('-created_at')[:100],
        'start_date': start_str,
        'end_date': end_str,
        'page_size': page_size,
        'page_size_options': ANALYTICS_PAGE_SIZES,
        'country_filter': country, 'city_filter': city,
        'activity_query': query,
        'analytics_clear_search_query': urlencode({**{key: value for key, value in chart_params.items() if key != 'q'}, 'page_size': page_size}),
        'country_options': IPGeolocation.objects.filter(is_private=False).exclude(country='').order_by('country').values_list('country', flat=True).distinct(),
        'city_options': IPGeolocation.objects.filter(is_private=False).exclude(city='').order_by('city').values_list('city', flat=True).distinct(),
        'analytics_chart_query': urlencode(chart_params),
        'analytics_pagination_query': urlencode({**chart_params, 'page_size': page_size}),
        'analytics_clear_dates_query': urlencode(clear_dates_params),
        'analytics_clear_locations_query': urlencode(clear_locations_params),
    })


@staff_member_required(login_url='login')
def control_products(request):
    editing = get_object_or_404(Product, pk=request.GET.get('edit')) if request.GET.get('edit') else None
    if request.method == 'POST':
        if request.POST.get('action') == 'delete':
            product = get_object_or_404(Product, pk=request.POST.get('product_id'))
            try:
                product.delete()
            except ProtectedError:
                messages.error(request, 'This product cannot be removed because it is associated with an order.')
            else:
                messages.success(request, 'Product removed.')
            return redirect('control_products')
        if request.POST.get('action') == 'delete_image':
            image = get_object_or_404(ProductImage, pk=request.POST.get('image_id'))
            product_id = image.product_id
            image.image.delete(save=False)
            image.delete()
            messages.success(request, 'Image removed.')
            return redirect(f"{reverse('control_products')}?edit={product_id}")
        form = ProductManagementForm(request.POST, instance=editing)
        if form.is_valid():
            product = form.save(commit=False)
            product.save(stock_actor=request.user)
            send_restock_notifications(product)
            uploaded_images = request.FILES.getlist('images')
            if uploaded_images:
                has_primary = product.images.filter(is_primary=True).exists()
                next_sort_order = product.images.order_by('-sort_order').values_list('sort_order', flat=True).first()
                next_sort_order = (next_sort_order + 1) if next_sort_order is not None else 0
                for offset, image in enumerate(uploaded_images):
                    ProductImage.objects.create(
                        product=product,
                        image=image,
                        is_primary=not has_primary and offset == 0,
                        sort_order=next_sort_order + offset,
                    )
            messages.success(request, 'Product saved.')
            return redirect('control_products')
    else:
        form = ProductManagementForm(instance=editing)
    query = request.GET.get('q', '').strip()
    collection_filter = request.GET.get('collection', '').strip()
    products = Product.objects.annotate(
        pending_orders=Count('orderitem', filter=Q(orderitem__order__status='pending')),
    ).all().prefetch_related('images')
    if query:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query))
    if collection_filter:
        products = products.filter(collection__slug=collection_filter)
    return render(request, 'shop/control_products.html', {
        'form': form,
        'products': products,
        'editing': editing,
        'query': query,
        'collections': Collection.objects.all(),
        'collection_filter': collection_filter,
    })


@staff_member_required(login_url='login')
def control_stock(request):
    query = request.GET.get('q', '').strip()
    collection_filter = request.GET.get('collection', '').strip()
    stock_filter = request.GET.get('stock', 'all')
    if stock_filter not in {'all', 'healthy', 'low', 'out', 'attention'}:
        stock_filter = 'all'
    visibility = request.GET.get('visibility', 'all')
    if visibility not in {'all', 'active', 'hidden'}:
        visibility = 'all'
    sort = request.GET.get('sort', 'stock_asc')
    sorting = {'stock_asc': ('stock_quantity', 'name', 'pk'), 'stock_desc': ('-stock_quantity', 'name', 'pk'), 'name': ('name', 'pk'), 'sku': ('sku', 'pk')}
    if sort not in sorting:
        sort = 'stock_asc'
    filters = {'stock': stock_filter, 'visibility': visibility, 'sort': sort}
    if query:
        filters['q'] = query
    if collection_filter:
        filters['collection'] = collection_filter
    stock_url = reverse('control_stock') + '?' + urlencode({**filters, **({'page': request.GET['page']} if request.GET.get('page') else {})})
    if request.method == 'POST':
        product = get_object_or_404(Product, pk=request.POST.get('product_id'))
        form = StockManagementForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save(commit=False)
            product.save(stock_actor=request.user)
            send_restock_notifications(product)
            messages.success(request, f'Stock updated for {product.name}.')
        else:
            messages.error(request, f'Could not update stock for {product.name}.')
        return redirect(stock_url)
    products = Product.objects.annotate(
        restock_interest_count=Count(
            'interest_signups',
            filter=Q(interest_signups__interest_type='restock'),
        ),
        restock_email_sent_count=Count(
            'interest_signups',
            filter=Q(
                interest_signups__interest_type='restock',
                interest_signups__notified_at__isnull=False,
            ),
        ),
        restock_pending_count=Count(
            'interest_signups',
            filter=Q(
                interest_signups__interest_type='restock',
                interest_signups__notified_at__isnull=True,
            ),
        ),
        preorder_interest_count=Count(
            'interest_signups',
            filter=Q(interest_signups__interest_type='preorder'),
        ),
        preorder_quantity_total=Sum(
            'interest_signups__desired_quantity',
            filter=Q(interest_signups__interest_type='preorder'),
        ),
    )
    if query:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query))
    if collection_filter:
        products = products.filter(collection__slug=collection_filter)
    if stock_filter == 'healthy':
        products = products.filter(stock_quantity__gt=F('reorder_level'))
    elif stock_filter == 'low':
        products = products.filter(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level'))
    elif stock_filter == 'out':
        products = products.filter(stock_quantity=0)
    elif stock_filter == 'attention':
        products = products.filter(stock_quantity__lte=F('reorder_level'))
    if visibility != 'all':
        products = products.filter(active=visibility == 'active')
    products = products.select_related('collection').order_by(*sorting[sort])
    product_page = Paginator(products, 50).get_page(request.GET.get('page'))
    return render(request, 'shop/control_stock.html', {
        'products': product_page,
        'product_page': product_page,
        'pagination_query': urlencode(filters),
        'collections': Collection.objects.order_by('name'),
        'collection_filter': collection_filter,
        'stock_filter': stock_filter,
        'visibility': visibility,
        'sort': sort,
        'stock_movements': StockMovement.objects.filter(product_id__in=products.values('pk'))[:20],
        'query': query,
        'total_units': Product.objects.aggregate(total=Sum('stock_quantity', default=0))['total'],
        'out_of_stock_count': Product.objects.filter(stock_quantity=0).count(),
        'low_stock_count': Product.objects.filter(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level')).count(),
        'preorder_interest_count': InterestSignup.objects.filter(interest_type='preorder').count(),
        'preorder_signups': InterestSignup.objects.filter(interest_type='preorder').select_related('product', 'user')[:200],
        'restock_signups': InterestSignup.objects.filter(interest_type='restock').select_related('product')[:200],
    })


@staff_member_required(login_url='login')
def control_stock_history(request):
    query = request.GET.get('q', '').strip()
    reason = request.GET.get('reason', '')
    if reason not in dict(StockMovement.REASON_CHOICES):
        reason = ''
    movements = StockMovement.objects.all()
    if query:
        movements = movements.filter(Q(product_name__icontains=query) | Q(sku__icontains=query) | Q(order_reference__icontains=query))
    if reason:
        movements = movements.filter(reason=reason)
    return render(request, 'shop/control_stock_history.html', {
        'movement_page': Paginator(movements, 50).get_page(request.GET.get('page')),
        'query': query, 'reason': reason, 'reason_choices': StockMovement.REASON_CHOICES,
        'pagination_query': urlencode({'q': query, 'reason': reason}),
    })


@staff_member_required(login_url='login')
def control_collections(request):
    editing = get_object_or_404(Collection, pk=request.GET.get('edit')) if request.GET.get('edit') else None
    if request.method == 'POST':
        if request.POST.get('action') == 'delete':
            get_object_or_404(Collection, pk=request.POST.get('collection_id')).delete()
            messages.success(request, 'Collection removed.')
            return redirect('control_collections')
        form = CollectionManagementForm(request.POST, instance=editing)
        if form.is_valid():
            form.save()
            messages.success(request, 'Collection saved.')
            return redirect('control_collections')
    else:
        form = CollectionManagementForm(instance=editing)
    return render(request, 'shop/control_collections.html', {'form': form, 'collections': Collection.objects.all(), 'editing': editing})


@staff_member_required(login_url='login')
def control_delivery(request):
    editing = get_object_or_404(DeliveryOption, pk=request.GET.get('edit')) if request.GET.get('edit') else None
    editing_rate = get_object_or_404(ShippingRate, pk=request.GET.get('edit_rate')) if request.GET.get('edit_rate') else None
    packaging, _ = ShippingPackaging.objects.get_or_create(pk=1)
    packaging_form = ShippingPackagingManagementForm(instance=packaging)
    rate_form = ShippingRateManagementForm(instance=editing_rate)
    product_shipping_forms = {}

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_packaging':
            packaging_form = ShippingPackagingManagementForm(request.POST, instance=packaging)
            if packaging_form.is_valid():
                packaging_form.save()
                messages.success(request, 'Outer packaging settings saved.')
                return redirect('control_delivery')
        elif action == 'save_rate':
            editing_rate = get_object_or_404(ShippingRate, pk=request.POST['rate_id']) if request.POST.get('rate_id') else None
            rate_form = ShippingRateManagementForm(request.POST, instance=editing_rate)
            if rate_form.is_valid():
                rate_form.save()
                messages.success(request, 'Parcel rate saved.')
                return redirect('control_delivery')
        elif action == 'delete_rate':
            get_object_or_404(ShippingRate, pk=request.POST.get('rate_id')).delete()
            messages.success(request, 'Parcel rate removed.')
            return redirect('control_delivery')
        elif action == 'save_product_shipping':
            product = get_object_or_404(Product, pk=request.POST.get('product_id'))
            product_form = ProductShippingForm(request.POST, instance=product)
            if product_form.is_valid():
                product_form.save()
                messages.success(request, f'Parcel measurements saved for {product.name}.')
                return redirect(f'{reverse("control_delivery")}#shipping-products')
            product_shipping_forms[product.pk] = product_form
        elif action == 'delete':
            get_object_or_404(DeliveryOption, pk=request.POST.get('delivery_id')).delete()
            messages.success(request, 'Delivery option removed.')
            return redirect('control_delivery')
        else:
            form = DeliveryOptionManagementForm(request.POST, instance=editing)
            if form.is_valid():
                form.save()
                messages.success(request, 'Delivery option saved.')
                return redirect('control_delivery')
    else:
        form = DeliveryOptionManagementForm(instance=editing)

    shipping_products = [
        {'product': product, 'form': product_shipping_forms.get(product.pk) or ProductShippingForm(instance=product)}
        for product in Product.objects.order_by('name')
    ]
    return render(request, 'shop/control_delivery.html', {
        'form': form,
        'options': DeliveryOption.objects.all(),
        'editing': editing,
        'packaging_form': packaging_form,
        'rate_form': rate_form,
        'rates': ShippingRate.objects.select_related('service').all(),
        'editing_rate': editing_rate,
        'shipping_products': shipping_products,
    })


@staff_member_required(login_url='login')
def control_orders(request):
    query = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'newest')
    if sort not in {'oldest', 'newest'}:
        sort = 'newest'
    selected_statuses = list(dict.fromkeys(value for value in request.GET.getlist('status') if value in dict(Order.STATUS_CHOICES)))
    today = timezone.localdate()
    default_start = today - timedelta(days=30)
    try:
        start_date = parse_date(request.GET['start']) if request.GET.get('start') else default_start
        end_date = parse_date(request.GET['end']) if request.GET.get('end') else today
        if start_date is None or end_date is None or start_date > end_date:
            raise ValueError
        start_at = timezone.make_aware(datetime.combine(start_date, time.min))
        end_at = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
    except (ValueError, OverflowError):
        start_date, end_date = default_start, today
        start_at = timezone.make_aware(datetime.combine(start_date, time.min))
        end_at = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        messages.error(request, 'Enter a valid date range with the start date on or before the end date.')
    filters = {}
    if query:
        filters['q'] = query
    if request.GET.get('sort') in {'oldest', 'newest'}:
        filters['sort'] = sort
    if selected_statuses:
        filters['status'] = selected_statuses
    if 'start' in request.GET or 'end' in request.GET:
        filters.update(start=start_date.isoformat(), end=end_date.isoformat())
    if request.GET.get('page'):
        filters['page'] = request.GET['page']
    orders_url = reverse('control_orders') + (f'?{urlencode(filters, doseq=True)}' if filters else '')
    editing = get_object_or_404(Order, pk=request.GET.get('edit')) if request.GET.get('edit') else None
    if request.method == 'POST':
        if request.POST.get('action') == 'batch_update':
            order_ids = request.POST.getlist('order_ids')
            batch_status = request.POST.get('batch_status')
            if order_ids and batch_status in dict(Order.STATUS_CHOICES):
                with transaction.atomic():
                    for selected_order in Order.objects.select_for_update().filter(pk__in=order_ids):
                        selected_order.status = batch_status
                        if batch_status == 'processing' and not selected_order.assigned_to_id:
                            selected_order.assigned_to = request.user
                        selected_order.save(update_fields=['status', 'assigned_to', 'updated_at'])
                messages.success(request, f'{len(order_ids)} order(s) updated.')
            else:
                messages.error(request, 'Select orders and a valid status first.')
            return redirect(orders_url)
        order = get_object_or_404(Order, pk=request.POST.get('order_id'))
        if request.POST.get('action') == 'refund_order':
            notes_form = RefundNotesForm(request.POST)
            if not notes_form.is_valid():
                messages.error(request, 'Enter a refund reason of no more than 2,000 characters.')
                return redirect(orders_url)
            notes = notes_form.cleaned_data['refund_notes']
            receipts = list(order.stripe_transactions.filter(status='paid'))
            if any(len('\n\n'.join(value for value in [record.refund_notes, notes] if value)) > 2000 for record in [order, *receipts]):
                messages.error(request, 'Refund notes cannot exceed 2,000 characters in total.')
                return redirect(orders_url)
            remaining = order.total - order.refunded_amount
            raw_amount = request.POST.get('refund_amount', '').strip()
            try:
                refund_amount = Decimal(raw_amount) if raw_amount else remaining
            except (TypeError, ValueError, InvalidOperation):
                messages.error(request, 'Enter a valid refund amount.')
                return redirect(orders_url)
            if refund_amount <= 0 or refund_amount > remaining:
                messages.error(request, f'Refund must be between 0.01 and {order.currency} {remaining:.2f}.')
                return redirect(orders_url)
            try:
                refund = refund_stripe_order(order, refund_amount)
            except (RuntimeError, stripe.error.StripeError) as error:
                messages.error(request, f'Refund could not be initiated: {error}')
                return redirect(orders_url)
            order.refunded_amount += refund_amount
            order.refund_status = 'refunded' if order.refunded_amount >= order.total else 'partial'
            order.payment_status = 'refunded' if order.refunded_amount >= order.total else 'partially_refunded'
            order.status = 'return'
            order.stripe_refund_id = refund.get('id', '')
            order.save(update_fields=['refunded_amount', 'refund_status', 'payment_status', 'status', 'stripe_refund_id', 'updated_at'])
            try:
                with transaction.atomic():
                    append_refund_notes(order, notes)
                    for receipt in receipts:
                        append_refund_notes(receipt, notes)
            except (ValueError, ObjectDoesNotExist) as error:
                messages.error(request, f'Refund initiated, but notes could not be saved: {error}')
                return redirect(orders_url)
            messages.success(request, f'Refund of {order.currency} {refund_amount:.2f} initiated for order #{order.order_reference or order.pk}.')
            return redirect(orders_url)
        form = OrderManagementForm(request.POST, instance=order, user=request.user)
        if form.is_valid():
            order = form.save(commit=False)
            if order.status == 'processing' and not order.assigned_to_id:
                order.assigned_to = request.user
            if order.status == 'shipped' and not order.tracking_number.strip():
                messages.error(request, 'Add a tracking number before saving an order as shipped.')
                return redirect(orders_url)
            order.shipping = order.delivery_cost
            if order.discount_amount > order.subtotal + order.shipping:
                messages.error(request, 'Discount cannot be greater than the order value.')
                return redirect(orders_url)
            order.total = max(Decimal('0'), order.subtotal + order.shipping - order.discount_amount)
            order.save()
            messages.success(request, f'Order #{order.pk} updated.')
            if order.status == 'shipped':
                if not order.tracking_number:
                    messages.error(request, 'Order saved as shipped, but add a tracking number before dispatch email can be sent.')
                elif not order.tracking_email_sent_at:
                    try:
                        if send_tracking_email(order.pk):
                            order.tracking_email_sent_at = timezone.now()
                            order.save(update_fields=['tracking_email_sent_at', 'updated_at'])
                            messages.success(request, f'Tracking email sent to {order.email}.')
                    except (SMTPException, OSError):
                        logger.exception('Failed to send tracking email to %s', order.email)
                        messages.error(request, 'Order saved, but the tracking email could not be sent. Check email settings.')
            return redirect(orders_url)
    else:
        form = OrderManagementForm(instance=editing, user=request.user)
    orders = Order.objects.filter(created_at__gte=start_at, created_at__lt=end_at).select_related('assigned_to').prefetch_related('items')
    if selected_statuses:
        orders = orders.filter(status__in=selected_statuses)
    if query:
        orders = orders.filter(
            Q(order_reference__icontains=query)
            | Q(email__icontains=query)
            | Q(shipping_postcode__icontains=query)
            | Q(billing_postcode__icontains=query)
        )
    orders = orders.order_by('created_at', 'pk') if sort == 'oldest' else orders.order_by('-created_at', '-pk')
    order_page = Paginator(orders, 50).get_page(request.GET.get('page'))
    for order in order_page:
        order.management_form = OrderManagementForm(instance=order, user=request.user)
    pagination_filters = {key: value for key, value in filters.items() if key != 'page'}
    pagination_filters.update(start=start_date.isoformat(), end=end_date.isoformat(), sort=sort)
    return render(request, 'shop/control_orders.html', {
        'form': form,
        'orders': order_page,
        'order_page': order_page,
        'pagination_query': urlencode(pagination_filters, doseq=True),
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'date_range_changed': start_date != default_start or end_date != today,
        'editing': editing,
        'query': query,
        'sort': sort,
        'selected_statuses': selected_statuses,
        'status_choices': Order.STATUS_CHOICES,
    })


@staff_member_required(login_url='login')
def control_order_pdf(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items__product'), pk=pk)
    response = HttpResponse(build_order_pdf(order), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="order-{order.order_reference or order.pk}.pdf"'
    return response


SALES_STATUSES = ['completed']
SALES_PERIOD_TRUNC = {'month': TruncMonth, 'quarter': TruncQuarter, 'year': TruncYear}
SALES_PERIOD_LIMITS = {'month': 24, 'quarter': 12, 'year': 6}


def _format_period_label(period_type, value):
    if not value:
        return '—'
    if period_type == 'month':
        return value.strftime('%b %Y')
    if period_type == 'quarter':
        return f'Q{(value.month - 1) // 3 + 1} {value.year}'
    return str(value.year)


@staff_member_required(login_url='login')
def control_sales(request):
    window = sales_window(request.GET)
    params = urlencode({'period': window['period'], 'year': window['year']})
    form = OfflineSaleForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'record_offline' else None)
    if request.method == 'POST':
        if request.POST.get('action') == 'sync_stripe':
            lock = 'sales:stripe-sync'
            if not cache.add(lock, True, 60):
                messages.warning(request, 'A Stripe refresh is already running. Please try again shortly.')
            else:
                try:
                    run = sync_stripe_transactions(window, request.user)
                    if run.status == 'complete':
                        messages.success(request, f'Stripe refresh complete: {run.transaction_count} transactions imported.')
                    else:
                        messages.warning(request, 'Stripe refresh is incomplete. Reconciliation is not final; check API permissions or retry.')
                finally:
                    cache.delete(lock)
            return redirect(reverse('control_sales') + '?' + params)
        if request.POST.get('action') == 'record_offline' and form.is_valid():
            try:
                order, created = record_offline_sale(form.cleaned_data, request.user)
            except (ValueError, IntegrityError) as error:
                form.add_error(None, str(error) if isinstance(error, ValueError) else 'This receipt or redemption has already been recorded.')
            else:
                messages.success(request, f'Entry #{order.pk} recorded.' if created else f'Entry #{order.pk} was already recorded; stock has not been deducted twice.')
                return redirect(reverse('control_sales') + '?' + params)
    report = completed_sales_report(window)
    return render(request, 'shop/control_sales.html', {
        'window': window, 'period': window['period'], 'periods': report['periods'], 'totals': report['totals'],
        'reconciliation': reconciliation_report(window), 'budget': replenishment_budget(), 'offline_form': form,
        'finance_query': params, 'finance_years': range(window['current_year'], 1999, -1),
    })


@staff_member_required(login_url='login')
def control_sales_details(request, section):
    window = sales_window(request.GET)
    params = urlencode({'period': window['period'], 'year': window['year']})
    report = completed_sales_report(window)
    reconciliation = reconciliation_report(window) if section == 'reconciliation' else None
    budget = replenishment_budget() if section == 'replenishment' else None
    kind = request.GET.get('kind', '')
    if kind not in {'refunds', 'failures'} or not reconciliation:
        kind = ''
    entries = reconciliation[kind]['entries'] if kind else reconciliation['entries'] if reconciliation else budget['items'] if budget else report['orders']
    if kind:
        params += '&' + urlencode({'kind': kind})
    detail_page = Paginator(entries, 50).get_page(request.GET.get('page'))
    if kind == 'refunds':
        for entry in detail_page:
            record = entry['receipt'] or entry['order']
            key = str(record.pk) if entry['receipt'] else 'order-' + str(record.pk)
            entry['notes'] = record.refund_notes
            entry['notes_key'] = key
            entry['editing_notes'] = request.GET.get('edit_notes') == key
            entry['notes_form'] = RefundNotesForm(auto_id=f'refund-notes-{key}-%s')
            entry['notes_form'].fields['refund_notes'].label = 'Additional notes'
    if request.method == 'POST':
        if kind != 'refunds' or request.POST.get('action') != 'append_refund_notes':
            messages.error(request, 'Use Edit to add additional notes in refund reconciliation.')
            return redirect(reverse('control_sales_reconciliation') + '?' + params)
        target = next(
            (entry for entry in detail_page if
             (entry['receipt'] and request.POST.get('receipt_id') == str(entry['receipt'].pk))
             or (not entry['receipt'] and request.POST.get('order_id') == str(entry['order'].pk))),
            None,
        )
        if target is None:
            messages.error(request, 'That refund is no longer on this page. Refresh and try again.')
            return redirect(reverse('control_sales_reconciliation') + '?' + params + f'&page={detail_page.number}')
        form = RefundNotesForm(request.POST, auto_id=target['notes_form'].auto_id)
        form.fields['refund_notes'].label = 'Additional notes'
        if form.is_valid():
            record = target['receipt'] or target['order']
            try:
                append_refund_notes(record, form.cleaned_data['refund_notes'])
            except ValueError as error:
                form.add_error('refund_notes', str(error))
            except type(record).DoesNotExist:
                messages.error(request, 'That refund was deleted before notes could be saved. Refresh and try again.')
                return redirect(reverse('control_sales_reconciliation') + '?' + params + f'&page={detail_page.number}')
            else:
                messages.success(request, 'Additional refund notes saved.')
                return redirect(reverse('control_sales_reconciliation') + '?' + params + f'&page={detail_page.number}')
        target['notes_form'] = form
        target['editing_notes'] = True
        messages.error(request, 'Refund notes were not saved. Please correct the errors below.')
    return render(request, 'shop/control_sales_details.html', {
        'section': section, 'kind': kind, 'window': window, 'finance_query': params, 'periods': report['periods'],
        'totals': report['totals'], 'reconciliation': reconciliation, 'budget': budget,
        'detail_page': detail_page,
    })


@staff_member_required(login_url='login')
def control_users(request):
    filters = user_filters(request.GET)
    params = urlencode(filters)
    return_url = reverse('control_users') + '?' + params + (f'&page={request.GET["page"]}' if request.GET.get('page', '').isdigit() else '')
    if request.method == 'POST':
        target_user = get_object_or_404(User, pk=request.POST.get('user_id'))
        action = request.POST.get('action')
        if action == 'toggle_active':
            if target_user == request.user:
                messages.error(request, "You can't disable your own account.")
            else:
                target_user.is_active = not target_user.is_active
                target_user.save(update_fields=['is_active'])
                messages.success(request, f"{target_user.email or target_user.username} {'enabled' if target_user.is_active else 'disabled'}.")
        elif action == 'edit_details':
            email = request.POST.get('email', '').strip().lower()
            is_staff = request.POST.get('is_staff') == 'on'
            if not email:
                messages.error(request, 'Email address is required.')
            elif target_user == request.user and not is_staff:
                messages.error(request, "You can't remove your own control panel access.")
            elif User.objects.exclude(pk=target_user.pk).filter(username=email).exists():
                messages.error(request, 'Another account already uses that email address.')
            else:
                target_user.first_name = request.POST.get('first_name', '').strip()
                target_user.last_name = request.POST.get('last_name', '').strip()
                target_user.email = email
                target_user.username = email
                target_user.is_staff = is_staff
                target_user.save(update_fields=['first_name', 'last_name', 'email', 'username', 'is_staff'])
                messages.success(request, 'User details updated.')
        elif action == 'reset_password':
            if not target_user.email:
                messages.error(request, 'This user has no email address on file.')
            else:
                reset_form = PasswordResetForm(data={'email': target_user.email})
                if reset_form.is_valid():
                    reset_form.save(
                        request=request,
                        use_https=request.is_secure(),
                        from_email=settings.PASSWORD_RESET_FROM_EMAIL,
                        email_template_name='registration/password_reset_email.txt',
                        subject_template_name='registration/password_reset_subject.txt',
                    )
                    messages.success(request, f'Password reset email sent to {target_user.email}.')
                else:
                    messages.error(request, 'Unable to send a password reset email to this address.')
        return redirect(return_url)
    users = filtered_users(filters)
    page = Paginator(users, 50).get_page(request.GET.get('page'))
    return render(request, 'shop/control_users.html', {
        'users': page, 'user_page': page, 'summary': user_summary(users),
        'query': filters['q'], 'filters': filters, 'user_query': params,
    })


def signup(request):
    if request.user.is_authenticated:
        return redirect('account')

    form = SignUpForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        user.is_active = False
        user.save(update_fields=['is_active'])
        activation_url = request.build_absolute_uri(reverse(
            'activate_account',
            args=[urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)],
        ))
        try:
            send_mail(
                'Activate your MISHA account',
                f'Welcome to MISHA Island Heritage. Activate your account here:\n\n{activation_url}\n\nYou must activate your account before placing an order.',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
            )
        except (SMTPException, OSError):
            logger.exception('Failed to send activation email to %s', user.email)
            messages.warning(request, 'Your account was created, but the activation email could not be sent. Contact support to activate it.')
        return render(request, 'registration/activation_sent.html', {'email': user.email})
    return render(request, 'registration/account_access.html', {
        'login_form': EmailAuthenticationForm(request),
        'signup_form': form,
    })


def activate_account(request, uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=['is_active'])
        messages.success(request, 'Your account is active. You can now sign in.')
        return redirect('login')
    return render(request, 'registration/activation_invalid.html')

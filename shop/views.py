import json
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt

import stripe

from .cart import Cart
from .forms import CheckoutForm, CollectionManagementForm, CustomerProfileForm, CustomerReviewForm, DeliveryOptionManagementForm, DELIVERY_OPTIONS, EmailAuthenticationForm, EnquiryForm, OrderManagementForm, ProductManagementForm, SignUpForm, StockManagementForm, get_delivery_options
from .models import AnalyticsEvent, Collection, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, Enquiry, EnquiryReply, Order, OrderItem, PrivacyRequest, Product, ProductImage, WebhookEvent
from .services import build_order_pdf, create_stripe_checkout, refund_stripe_order, resolve_ip_locations, send_order_confirmation, update_stripe_payment_details


def home(request):
    return render(request, 'shop/home.html', {'featured_products': Product.objects.filter(active=True, featured=True)[:8], 'collections': Collection.objects.filter(active=True)[:6], 'featured_reviews': CustomerReview.objects.filter(status='approved', featured=True)[:3]})

def catalogue(request):
    qs = Product.objects.filter(active=True).select_related('collection').prefetch_related('images')
    collection = request.GET.get('collection')
    q = request.GET.get('q')
    if collection: qs = qs.filter(collection__slug=collection)
    if q: qs = qs.filter(name__icontains=q)
    return render(request, 'shop/catalogue.html', {'products': qs, 'collections': Collection.objects.filter(active=True), 'current_collection': collection, 'query': q or ''})

def collection_detail(request, slug):
    collection = get_object_or_404(Collection, slug=slug, active=True)
    return render(request, 'shop/collection.html', {'collection': collection, 'products': collection.products.filter(active=True).prefetch_related('images')})

def product_detail(request, slug):
    product = get_object_or_404(Product.objects.prefetch_related('images'), slug=slug, active=True)
    return render(request, 'shop/product_detail.html', {'product': product})

def add_to_cart(request, pk):
    product = get_object_or_404(Product, pk=pk, active=True)
    if request.method != 'POST': return redirect(product.get_absolute_url())
    quantity = max(1, int(request.POST.get('quantity', 1)))
    Cart(request).add(product, quantity)
    messages.success(request, f'{product.name} added to your bag.', extra_tags='bag-added')
    return redirect(request.POST.get('next') or product.get_absolute_url())

def delivery_cost(method, subtotal):
    option = DeliveryOption.objects.filter(code=method, active=True).first()
    if option is None:
        return Decimal('4.95')
    if option.free_over is not None and subtotal >= option.free_over:
        return Decimal('0.00')
    return option.price


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
                return render(request, 'shop/checkout.html', {'form': form, 'cart': cart, 'items': items, 'delivery_cost': delivery_cost(form.cleaned_data.get('shipping_method', 'standard_uk'), cart.subtotal), 'delivery_rates': delivery_rates})

            with transaction.atomic():
                order = form.save(commit=False)
                order.user = request.user if request.user.is_authenticated else None
                order.order_reference = generate_order_reference()
                order.delivery_method = form.cleaned_data['shipping_method']
                order.delivery_cost = delivery_cost(order.delivery_method, cart.subtotal)
                billing = None
                profile = getattr(request.user, 'customer_profile', None) if request.user.is_authenticated else None
                shipping = CustomerAddress.objects.filter(pk=form.cleaned_data['shipping_address'], user=request.user, address_type='shipping').first() if request.user.is_authenticated and form.cleaned_data['shipping_address'] else None
                if shipping:
                    order.shipping_address1 = f'{shipping.house_number} {shipping.street_name}'.strip()
                    order.shipping_address2 = ''
                    order.shipping_city = shipping.city
                    order.shipping_postcode = shipping.postcode
                    order.shipping_country = shipping.country
                    if not order.shipping_name:
                        order.shipping_name = ' '.join(part for part in [request.user.first_name, request.user.last_name] if part)
                if form.cleaned_data.get('same_as_shipping'):
                    order.billing_name = order.shipping_name
                    order.billing_address1 = order.shipping_address1
                    order.billing_address2 = order.shipping_address2
                    order.billing_city = order.shipping_city
                    order.billing_postcode = order.shipping_postcode
                    order.billing_country = order.shipping_country
                else:
                    billing = CustomerAddress.objects.filter(pk=form.cleaned_data.get('billing_address'), user=request.user, address_type='billing').first() if request.user.is_authenticated and form.cleaned_data.get('billing_address') else None
                    if billing:
                        order.billing_name = order.shipping_name
                        order.billing_address1 = f'{billing.house_number} {billing.street_name}'.strip()
                        order.billing_address2 = ''
                        order.billing_city = billing.city
                        order.billing_postcode = billing.postcode
                        order.billing_country = billing.country
                    else:
                        order.billing_name = form.cleaned_data['billing_name'] or order.shipping_name
                        order.billing_address1 = form.cleaned_data['billing_address1']
                        order.billing_address2 = form.cleaned_data['billing_address2']
                        order.billing_city = form.cleaned_data['billing_city']
                        order.billing_postcode = form.cleaned_data['billing_postcode']
                        order.billing_country = form.cleaned_data['billing_country']
                    if request.user.is_authenticated and all([
                        form.cleaned_data.get('billing_address1'),
                        form.cleaned_data.get('billing_city'),
                        form.cleaned_data.get('billing_postcode'),
                        form.cleaned_data.get('billing_country'),
                    ]):
                        billing_address = CustomerAddress.objects.filter(
                            user=request.user,
                            address_type='billing',
                        ).first()
                        billing_values = {
                            'label': 'Billing address',
                            'house_number': form.cleaned_data['billing_address1'].split(' ', 1)[0],
                            'street_name': form.cleaned_data['billing_address1'].split(' ', 1)[1] if ' ' in form.cleaned_data['billing_address1'] else form.cleaned_data['billing_address1'],
                            'city': form.cleaned_data['billing_city'],
                            'postcode': form.cleaned_data['billing_postcode'],
                            'country': form.cleaned_data['billing_country'],
                        }
                        if billing_address:
                            for key, value in billing_values.items():
                                setattr(billing_address, key, value)
                            billing_address.save()
                        else:
                            CustomerAddress.objects.create(user=request.user, address_type='billing', **billing_values)
                if request.user.is_authenticated and form.cleaned_data['save_shipping_address'] and not shipping:
                    CustomerAddress.objects.create(
                        user=request.user,
                        address_type='shipping',
                        label=f'{form.cleaned_data["shipping_address1"]}, {form.cleaned_data["shipping_city"]}',
                        house_number=form.cleaned_data['shipping_address1'].split(' ', 1)[0],
                        street_name=form.cleaned_data['shipping_address1'].split(' ', 1)[1] if ' ' in form.cleaned_data['shipping_address1'] else form.cleaned_data['shipping_address1'],
                        city=form.cleaned_data['shipping_city'],
                        postcode=form.cleaned_data['shipping_postcode'],
                        country=form.cleaned_data['shipping_country'],
                    )
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
                    product.stock_quantity -= row['quantity']
                    product.save(update_fields=['stock_quantity', 'updated_at'])

                checkout_session = create_stripe_checkout(order, request)

            cart.clear()
            request.session.pop('delivery_method', None)
            request.session.modified = True
            return redirect(checkout_session.url)
    else:
        initial = {
            'email': request.user.email if request.user.is_authenticated else '',
            'shipping_method': request.session.get('delivery_method', 'standard_uk'),
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
        form = CheckoutForm(initial=initial, user=request.user)

    return render(request, 'shop/checkout.html', {
        'form': form,
        'cart': cart,
        'items': items,
        'delivery_cost': delivery_cost(form.initial.get('shipping_method', request.session.get('delivery_method', 'standard_uk')), cart.subtotal),
        'checkout_total': cart.subtotal + delivery_cost(form.initial.get('shipping_method', request.session.get('delivery_method', 'standard_uk')), cart.subtotal),
        'delivery_rates': delivery_rates,
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
                update_stripe_payment_details(order, session.get('payment_intent'))
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

    if event_type == 'checkout.session.completed':
        if order and order.payment_status != 'paid':
            order.payment_status = 'paid'
            order.status = 'paid'
            order.paid_at = timezone.now()
            order.save(update_fields=['payment_status', 'status', 'paid_at'])
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
    if request.method == 'POST':
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
            ip_address=request.META.get('REMOTE_ADDR'),
            country=request.META.get('HTTP_CF_IPCOUNTRY', '')[:80],
            referrer=request.META.get('HTTP_REFERER', '')[:500],
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
        return JsonResponse({'ok': True})
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
    return render(request, 'shop/control_panel.html', {
        'product_count': Product.objects.count(),
        'active_product_count': Product.objects.filter(active=True).count(),
        'low_stock_count': Product.objects.filter(active=True, stock_quantity__lte=5).count(),
        'order_count': Order.objects.count(),
        'pending_order_count': Order.objects.filter(status__in=['pending', 'processing']).count(),
        'delivery_count': DeliveryOption.objects.filter(active=True).count(),
        'visitor_count': AnalyticsEvent.objects.filter(event_type='page_view').count(),
        'pending_review_count': CustomerReview.objects.filter(status='pending').count(),
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
                send_mail(
                    f'Re: {enquiry.subject or "Your enquiry"} — MISHA Island Heritage',
                    reply_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [enquiry.email],
                )
                EnquiryReply.objects.create(enquiry=enquiry, message=reply_message)
                enquiry.handled = True
                enquiry.save(update_fields=['handled'])
                messages.success(request, f'Reply sent to {enquiry.email}.')
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


ANALYTICS_PAGE_SIZES = [15, 30, 50, 100]


def _parse_range_boundary(value, end_of_day=False):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date is None:
            return None
        parsed = datetime.combine(parsed_date, time(23, 59, 59) if end_of_day else time.min)
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
    return filtered_events, filtered_user, start_str, end_str


@staff_member_required(login_url='login')
def control_analytics(request):
    if request.method == 'POST' and request.POST.get('action') == 'purge':
        purge_events, _, start_str, end_str = _filter_analytics_events(request.POST)
        deleted_count = purge_events.delete()[0]
        messages.success(request, f'{deleted_count} activity record(s) purged.')
        query_parts = []
        if request.POST.get('user'):
            query_parts.append(f"user={request.POST['user']}")
        if start_str:
            query_parts.append(f'start={start_str}')
        if end_str:
            query_parts.append(f'end={end_str}')
        redirect_url = reverse('control_analytics')
        if query_parts:
            redirect_url += '?' + '&'.join(query_parts)
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
    today = timezone.localdate()
    daily_activity = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        daily_activity.append({
            'label': day.strftime('%a'),
            'views': filtered_events.filter(event_type='page_view', created_at__date=day).count(),
            'clicks': filtered_events.filter(event_type='click', created_at__date=day).count(),
        })
    top_pages = list(
        filtered_events.filter(event_type='page_view')
        .values('path')
        .annotate(total=Count('id'))
        .order_by('-total')[:6]
    )
    top_products = list(filtered_events.exclude(product__isnull=True).values('product__name').annotate(total=Count('id')).order_by('-total')[:6])
    return render(request, 'shop/control_analytics.html', {
        'events': event_page,
        'page_views': filtered_events.filter(event_type='page_view').count(),
        'clicks': filtered_events.filter(event_type='click').count(),
        'unique_ips': filtered_events.exclude(ip_address__isnull=True).values('ip_address').distinct().count(),
        'event_page': event_page,
        'top_products': top_products,
        'largest_product_count': top_products[0]['total'] if top_products else 1,
        'daily_activity': daily_activity,
        'top_pages': top_pages,
        'largest_page_count': top_pages[0]['total'] if top_pages else 1,
        'filtered_user': filtered_user,
        'start_date': start_str,
        'end_date': end_str,
        'page_size': page_size,
        'page_size_options': ANALYTICS_PAGE_SIZES,
    })


@staff_member_required(login_url='login')
def control_products(request):
    editing = get_object_or_404(Product, pk=request.GET.get('edit')) if request.GET.get('edit') else None
    if request.method == 'POST':
        if request.POST.get('action') == 'delete':
            get_object_or_404(Product, pk=request.POST.get('product_id')).delete()
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
            product = form.save()
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
    products = Product.objects.annotate(
        pending_orders=Count('orderitem', filter=Q(orderitem__order__status='pending')),
    ).all().prefetch_related('images')
    if query:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query))
    return render(request, 'shop/control_products.html', {'form': form, 'products': products, 'editing': editing, 'query': query})


@staff_member_required(login_url='login')
def control_stock(request):
    if request.method == 'POST':
        product = get_object_or_404(Product, pk=request.POST.get('product_id'))
        form = StockManagementForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f'Stock updated for {product.name}.')
        else:
            messages.error(request, f'Could not update stock for {product.name}.')
        return redirect('control_stock')

    query = request.GET.get('q', '').strip()
    products = Product.objects.all()
    if query:
        products = products.filter(name__icontains=query) | products.filter(sku__icontains=query)
    return render(request, 'shop/control_stock.html', {
        'products': products,
        'query': query,
        'total_units': sum(product.stock_quantity for product in Product.objects.all()),
        'out_of_stock_count': Product.objects.filter(stock_quantity=0).count(),
        'low_stock_count': Product.objects.filter(stock_quantity__gt=0, stock_quantity__lte=5).count(),
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
    if request.method == 'POST':
        if request.POST.get('action') == 'delete':
            get_object_or_404(DeliveryOption, pk=request.POST.get('delivery_id')).delete()
            messages.success(request, 'Delivery option removed.')
            return redirect('control_delivery')
        form = DeliveryOptionManagementForm(request.POST, instance=editing)
        if form.is_valid():
            form.save()
            messages.success(request, 'Delivery option saved.')
            return redirect('control_delivery')
    else:
        form = DeliveryOptionManagementForm(instance=editing)
    return render(request, 'shop/control_delivery.html', {'form': form, 'options': DeliveryOption.objects.all(), 'editing': editing})


@staff_member_required(login_url='login')
def control_orders(request):
    editing = get_object_or_404(Order, pk=request.GET.get('edit')) if request.GET.get('edit') else None
    if request.method == 'POST':
        if request.POST.get('action') == 'batch_update':
            order_ids = request.POST.getlist('order_ids')
            batch_status = request.POST.get('batch_status')
            if order_ids and batch_status in dict(Order.STATUS_CHOICES):
                Order.objects.filter(pk__in=order_ids).update(status=batch_status)
                messages.success(request, f'{len(order_ids)} order(s) updated.')
            else:
                messages.error(request, 'Select orders and a valid status first.')
            return redirect('control_orders')
        order = get_object_or_404(Order, pk=request.POST.get('order_id'))
        if request.POST.get('action') == 'refund_order':
            remaining = order.total - order.refunded_amount
            raw_amount = request.POST.get('refund_amount', '').strip()
            try:
                refund_amount = Decimal(raw_amount) if raw_amount else remaining
            except (TypeError, ValueError, InvalidOperation):
                messages.error(request, 'Enter a valid refund amount.')
                return redirect('control_orders')
            if refund_amount <= 0 or refund_amount > remaining:
                messages.error(request, f'Refund must be between 0.01 and {order.currency} {remaining:.2f}.')
                return redirect('control_orders')
            try:
                refund = refund_stripe_order(order, refund_amount)
            except (RuntimeError, stripe.error.StripeError) as error:
                messages.error(request, f'Refund could not be initiated: {error}')
                return redirect('control_orders')
            order.refunded_amount += refund_amount
            order.refund_status = 'refunded' if order.refunded_amount >= order.total else 'partial'
            order.status = 'return'
            order.stripe_refund_id = refund.get('id', '')
            order.save(update_fields=['refunded_amount', 'refund_status', 'status', 'stripe_refund_id', 'updated_at'])
            messages.success(request, f'Refund of {order.currency} {refund_amount:.2f} initiated for order #{order.order_reference or order.pk}.')
            return redirect('control_orders')
        form = OrderManagementForm(request.POST, instance=order)
        if form.is_valid():
            order = form.save(commit=False)
            order.shipping = order.delivery_cost
            if order.discount_amount > order.subtotal + order.shipping:
                messages.error(request, 'Discount cannot be greater than the order value.')
                return redirect('control_orders')
            order.total = max(Decimal('0'), order.subtotal + order.shipping - order.discount_amount)
            order.save()
            messages.success(request, f'Order #{order.pk} updated.')
            return redirect('control_orders')
    else:
        form = OrderManagementForm(instance=editing)
    query = request.GET.get('q', '').strip()
    orders = Order.objects.all().prefetch_related('items')
    if query:
        orders = orders.filter(
            Q(order_reference__icontains=query)
            | Q(email__icontains=query)
            | Q(shipping_postcode__icontains=query)
            | Q(billing_postcode__icontains=query)
        )
    for order in orders:
        order.management_form = OrderManagementForm(instance=order)
    return render(request, 'shop/control_orders.html', {
        'form': form,
        'orders': orders,
        'editing': editing,
        'query': query,
        'status_choices': Order.STATUS_CHOICES,
    })


@staff_member_required(login_url='login')
def control_order_pdf(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items__product'), pk=pk)
    response = HttpResponse(build_order_pdf(order), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="order-{order.order_reference or order.pk}.pdf"'
    return response


SALES_STATUSES = ['paid', 'processing', 'shipped', 'completed']
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
    period = request.GET.get('period', 'month')
    if period not in SALES_PERIOD_TRUNC:
        period = 'month'
    trunc = SALES_PERIOD_TRUNC[period]

    orders_qs = Order.objects.filter(status__in=SALES_STATUSES)
    periods = list(
        orders_qs
        .annotate(period=trunc('created_at'))
        .values('period')
        .annotate(revenue=Sum('total'), product_revenue=Sum('subtotal'), order_count=Count('id'))
        .order_by('-period')[:SALES_PERIOD_LIMITS[period]]
    )

    cost_field = ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=DecimalField(max_digits=12, decimal_places=2))
    cogs_rows = {
        row['period']: row['cogs'] or Decimal('0')
        for row in OrderItem.objects.filter(order__status__in=SALES_STATUSES)
        .annotate(period=trunc('order__created_at'))
        .values('period')
        .annotate(cogs=Sum(cost_field))
    }

    for row in periods:
        row['label'] = _format_period_label(period, row['period'])
        row['revenue'] = row['revenue'] or Decimal('0')
        row['product_revenue'] = row['product_revenue'] or Decimal('0')
        row['cogs'] = cogs_rows.get(row['period'], Decimal('0'))
        row['gross_profit'] = row['product_revenue'] - row['cogs']
        row['margin'] = (row['gross_profit'] / row['product_revenue'] * 100) if row['product_revenue'] else Decimal('0')
        row['aov'] = (row['revenue'] / row['order_count']) if row['order_count'] else Decimal('0')

    periods.reverse()

    forecast = None
    if len(periods) >= 2:
        recent = periods[-12:]
        xs = list(range(len(recent)))
        ys = [float(row['revenue']) for row in recent]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denominator = sum((x - mean_x) ** 2 for x in xs) or 1
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
        intercept = mean_y - slope * mean_x
        forecast_value = max(slope * n + intercept, 0)
        forecast = {
            'label': _format_period_label(period, None) or 'Next period',
            'revenue': Decimal(str(round(forecast_value, 2))),
            'trend': 'growing' if slope > 1 else ('declining' if slope < -1 else 'flat'),
            'change': Decimal(str(round(slope, 2))),
        }

    replenishment_items = []
    total_replenishment_cost = Decimal('0')
    for product in Product.objects.filter(active=True, stock_quantity__lte=F('reorder_level')).select_related('collection'):
        qty_needed = max(product.restock_target - product.stock_quantity, 0)
        if qty_needed <= 0:
            continue
        line_cost = qty_needed * product.cost_price
        total_replenishment_cost += line_cost
        replenishment_items.append({'product': product, 'qty_needed': qty_needed, 'line_cost': line_cost})
    replenishment_items.sort(key=lambda entry: entry['line_cost'], reverse=True)

    totals = {
        'revenue': sum((row['revenue'] for row in periods), Decimal('0')),
        'product_revenue': sum((row['product_revenue'] for row in periods), Decimal('0')),
        'cogs': sum((row['cogs'] for row in periods), Decimal('0')),
        'order_count': sum((row['order_count'] for row in periods), 0),
    }
    totals['gross_profit'] = totals['product_revenue'] - totals['cogs']
    totals['margin'] = (totals['gross_profit'] / totals['product_revenue'] * 100) if totals['product_revenue'] else Decimal('0')

    return render(request, 'shop/control_sales.html', {
        'period': period,
        'periods': periods,
        'totals': totals,
        'forecast': forecast,
        'replenishment_items': replenishment_items,
        'total_replenishment_cost': total_replenishment_cost,
    })


@staff_member_required(login_url='login')
def control_users(request):
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
                        email_template_name='registration/password_reset_email.txt',
                        subject_template_name='registration/password_reset_subject.txt',
                    )
                    messages.success(request, f'Password reset email sent to {target_user.email}.')
                else:
                    messages.error(request, 'Unable to send a password reset email to this address.')
        return redirect('control_users')
    query = request.GET.get('q', '').strip()
    users = User.objects.all().annotate(
        order_count=Count('orders', distinct=True),
        activity_count=Count('analyticsevent', distinct=True),
    ).order_by('-date_joined')
    if query:
        users = users.filter(
            Q(email__icontains=query)
            | Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    return render(request, 'shop/control_users.html', {'users': users, 'query': query})


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
        send_mail(
            'Activate your MISHA account',
            f'Welcome to MISHA Island Heritage. Activate your account here:\n\n{activation_url}\n\nYou must activate your account before placing an order.',
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
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

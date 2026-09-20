import json
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from .cart import Cart
from .forms import CheckoutForm, EnquiryForm
from .models import Collection, Enquiry, Order, OrderItem, Product
from .services import create_stripe_checkout


def home(request):
    return render(request, 'shop/home.html', {'featured_products': Product.objects.filter(active=True, featured=True)[:8], 'collections': Collection.objects.filter(active=True)[:6]})

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
    messages.success(request, f'{product.name} added to your bag.')
    return redirect(request.POST.get('next') or 'cart')

def cart_view(request): return render(request, 'shop/cart.html', {'cart': Cart(request), 'items': list(Cart(request).items())})

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
    if not items: return redirect('cart')
    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = form.save(commit=False)
                order.user = request.user if request.user.is_authenticated else None
                order.subtotal = cart.subtotal
                order.shipping = Decimal('0.00') if cart.subtotal >= Decimal('100') else Decimal('4.95')
                order.total = order.subtotal + order.shipping
                order.save()
                for row in items:
                    p = row['product']
                    OrderItem.objects.create(order=order, product=p, product_name=p.name, sku=p.sku, unit_price=p.price, quantity=row['quantity'])
            try:
                session = create_stripe_checkout(order, request)
            except Exception as exc:
                order.delete()
                messages.error(request, f'Checkout is not configured yet: {exc}')
                return redirect('checkout')
            return redirect(session.url)
    else:
        form = CheckoutForm(initial={'email': request.user.email if request.user.is_authenticated else ''})
    return render(request, 'shop/checkout.html', {'form': form, 'cart': cart, 'items': items})

def checkout_success(request):
    session_id = request.GET.get('session_id')
    order = get_object_or_404(Order, stripe_session_id=session_id) if session_id else None
    if order and order.status == 'pending':
        order.status = 'paid'; order.save(update_fields=['status'])
        Cart(request).clear()
    return render(request, 'shop/success.html', {'order': order})

@csrf_exempt
def stripe_webhook(request):
    import stripe
    if not settings.STRIPE_WEBHOOK_SECRET: return HttpResponse(status=400)
    try: event = stripe.Webhook.construct_event(request.body, request.META.get('HTTP_STRIPE_SIGNATURE',''), settings.STRIPE_WEBHOOK_SECRET)
    except Exception: return HttpResponse(status=400)
    if event['type'] == 'checkout.session.completed':
        sid = event['data']['object']['id']
        Order.objects.filter(stripe_session_id=sid).update(status='paid')
    return JsonResponse({'received': True})

def enquiry(request):
    if request.method == 'POST':
        form = EnquiryForm(request.POST)
        if form.is_valid(): form.save(); messages.success(request, 'Thank you. We will be in touch shortly.'); return redirect('contact')
    else: form = EnquiryForm()
    return render(request, 'shop/contact.html', {'form': form})

@login_required
def account(request):
    return render(request, 'shop/account.html', {'orders': request.user.orders.all()[:20]})

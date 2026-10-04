from django.conf import settings
from django.db.models import F

from .cart import Cart

def shop_context(request):
    cart = Cart(request)
    context = {
        'cart_count': cart.count,
        'cart_subtotal': cart.subtotal,
        'google_site_verification': settings.GOOGLE_SITE_VERIFICATION,
        'google_analytics_id': settings.GOOGLE_ANALYTICS_ID,
        'google_ads_id': settings.GOOGLE_ADS_ID,
        'gemini_chat_enabled': bool(settings.GEMINI_API_KEY),
    }
    if request.user.is_authenticated and request.user.is_staff:
        from django.urls import reverse
        from .models import CustomDesignRequest, CustomerReview, Enquiry, Order, Product

        pending_orders = Order.objects.filter(payment_status='paid', status__in=['pending', 'paid', 'processing']).count()
        low_stock = Product.objects.filter(active=True, stock_quantity__lte=F('reorder_level')).count()
        pending_reviews = CustomerReview.objects.filter(status='pending').count()
        pending_feedback = Enquiry.objects.filter(handled=False).count()
        pending_custom_requests = CustomDesignRequest.objects.filter(status='new').count()
        context['admin_notifications'] = [
            {'label': 'Pending orders', 'count': pending_orders, 'url': reverse('control_orders')},
            {'label': 'Stock alerts', 'count': low_stock, 'url': reverse('control_stock') + '?stock=attention&visibility=active'},
            {'label': 'Reviews awaiting approval', 'count': pending_reviews, 'url': reverse('control_reviews')},
            {'label': 'Feedback awaiting response', 'count': pending_feedback, 'url': reverse('control_reviews')},
            {'label': 'New custom requests', 'count': pending_custom_requests, 'url': reverse('control_custom_requests')},
        ]
        context['admin_notification_count'] = pending_orders + low_stock + pending_reviews + pending_feedback + pending_custom_requests
    return context

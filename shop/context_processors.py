from .cart import Cart

def shop_context(request):
    cart = Cart(request)
    context = {'cart_count': cart.count, 'cart_subtotal': cart.subtotal}
    if request.user.is_authenticated and request.user.is_staff:
        from django.urls import reverse
        from .models import CustomerReview, Enquiry, Order, Product

        pending_orders = Order.objects.filter(status__in=['pending', 'processing']).count()
        low_stock = Product.objects.filter(active=True, stock_quantity__lte=5).count()
        pending_reviews = CustomerReview.objects.filter(status='pending').count()
        pending_feedback = Enquiry.objects.filter(handled=False).count()
        context['admin_notifications'] = [
            {'label': 'Pending orders', 'count': pending_orders, 'url': reverse('control_orders')},
            {'label': 'Low stock products', 'count': low_stock, 'url': reverse('control_stock')},
            {'label': 'Reviews awaiting approval', 'count': pending_reviews, 'url': reverse('control_reviews')},
            {'label': 'Feedback awaiting response', 'count': pending_feedback, 'url': reverse('control_reviews')},
        ]
        context['admin_notification_count'] = pending_orders + low_stock + pending_reviews + pending_feedback
    return context

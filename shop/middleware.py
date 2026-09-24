from .models import AnalyticsEvent, Product


class AnalyticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method == 'GET' and response.status_code == 200 and request.path.startswith('/'):
            excluded = ('/static/', '/media/', '/admin/', '/controlpanel/', '/payments/')
            if not request.path.startswith(excluded):
                forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
                ip_address = forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')
                product = None
                if getattr(request, 'resolver_match', None) and request.resolver_match.url_name == 'product_detail':
                    product = Product.objects.filter(slug=request.resolver_match.kwargs.get('slug')).first()
                AnalyticsEvent.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    event_type='page_view',
                    product=product,
                    path=request.path[:500],
                    ip_address=ip_address,
                    country=request.META.get('HTTP_CF_IPCOUNTRY', '')[:80],
                    referrer=request.META.get('HTTP_REFERER', '')[:500],
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                )
        return response
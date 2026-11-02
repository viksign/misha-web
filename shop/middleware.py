import math
from urllib.parse import unquote

import pycountry

from .models import AnalyticsEvent, Product
from .cookie_consent import has_cookie_consent


def analytics_location_fields(request):
    country_code = request.META.get('HTTP_CF_IPCOUNTRY', '').strip().upper()
    country = pycountry.countries.get(alpha_2=country_code) if len(country_code) == 2 else None

    def location_header(name):
        return unquote(request.META.get(name, '').strip())[:120]

    def coordinate(name, lower, upper):
        try:
            value = float(request.META.get(name, ''))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and lower <= value <= upper else None

    return {
        'ip_address': request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR'),
        'country': country.name if country else '',
        'city': location_header('HTTP_CF_IPCITY'),
        'region': location_header('HTTP_CF_REGION'),
        'latitude': coordinate('HTTP_CF_IPLATITUDE', -90, 90),
        'longitude': coordinate('HTTP_CF_IPLONGITUDE', -180, 180),
    }


class AnalyticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if has_cookie_consent(request, 'analytics') and request.method == 'GET' and response.status_code == 200 and request.path.startswith('/'):
            excluded = ('/static/', '/media/', '/admin/', '/controlpanel/', '/payments/', '/order-tracking/')
            if not request.path.startswith(excluded):
                product = None
                if getattr(request, 'resolver_match', None) and request.resolver_match.url_name == 'product_detail':
                    product = Product.objects.filter(slug=request.resolver_match.kwargs.get('slug')).first()
                AnalyticsEvent.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    event_type='page_view',
                    product=product,
                    path=request.path[:500],
                    **analytics_location_fields(request),
                    referrer=request.META.get('HTTP_REFERER', '')[:500],
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                )
        return response
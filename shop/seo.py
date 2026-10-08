import json
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.sitemaps.views import index, sitemap
from django.http import FileResponse, HttpResponse
from django.templatetags.static import static
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe


PUBLIC_ORIGIN = 'https://mishaislandheritage.com'
PUBLIC_DOMAIN = 'mishaislandheritage.com'
BRAND = 'Misha Island Heritage'
PUBLIC_VIEWS = {'home', 'catalogue', 'collection', 'product_detail', 'heritage', 'contact'}
PRIVATE_PREFIXES = ('/admin/', '/controlpanel/', '/account/', '/bag/', '/checkout/', '/payments/', '/login/', '/logout/', '/password-reset/', '/assistant/', '/analytics/', '/custom-design/', '/feedback/', '/review/')


def absolute_url(path):
    return urljoin(PUBLIC_ORIGIN + '/', path)


def is_indexable(request):
    match = getattr(request, 'resolver_match', None)
    if not match or match.url_name not in PUBLIC_VIEWS:
        return False
    return not (match.url_name == 'catalogue' and any(request.GET.get(key) for key in ['q', 'collection', 'sort']))


def json_ld(data):
    encoded = json.dumps(data, ensure_ascii=True).replace('<', '\\u003C').replace('>', '\\u003E').replace('&', '\\u0026')
    return mark_safe(encoded)


def seo_context(request):
    match = getattr(request, 'resolver_match', None)
    name = match.url_name if match else ''
    titles = {'home': BRAND, 'catalogue': 'Jewellery | ' + BRAND, 'heritage': 'Our Heritage | ' + BRAND, 'contact': 'Contact | ' + BRAND}
    descriptions = {
        'home': 'Discover Misha Island Heritage jewellery, collections and the island stories behind each piece.',
        'catalogue': 'Browse jewellery from Misha Island Heritage. Explore available pieces, collections, prices and product details.',
        'heritage': 'Discover the island stories, cultures and traditions behind Misha Island Heritage jewellery.',
        'contact': 'Contact Misha Island Heritage with questions about jewellery, orders and delivery.',
    }
    data = {
        'seo_title': titles.get(name, BRAND), 'seo_description': descriptions.get(name, ''),
        'seo_canonical': absolute_url(request.path) if name in PUBLIC_VIEWS and not (name == 'catalogue' and request.GET.get('q')) else '',
        'seo_robots': 'index, follow' if is_indexable(request) else 'noindex, follow',
        'seo_image': absolute_url(static('images/mih_logo.png')), 'seo_type': 'website', 'seo_json_ld': '',
    }
    if name == 'catalogue':
        data['seo_canonical'] = '' if request.GET.get('q') else absolute_url('/jewellery/')
    if name == 'home':
        data['seo_json_ld'] = json_ld({'@context': 'https://schema.org', '@graph': [
            {'@type': 'Organization', '@id': PUBLIC_ORIGIN + '/#organization', 'name': BRAND, 'url': PUBLIC_ORIGIN + '/', 'logo': data['seo_image']},
            {'@type': 'WebSite', '@id': PUBLIC_ORIGIN + '/#website', 'name': BRAND, 'url': PUBLIC_ORIGIN + '/', 'publisher': {'@id': PUBLIC_ORIGIN + '/#organization'}},
        ]})
    return data


def product_metadata(product):
    images = list(product.images.all())
    primary = next((image for image in images if image.is_primary), images[0] if images else None)
    description = ' '.join(strip_tags(product.short_description or product.description or product.name).split())
    canonical = absolute_url(product.get_absolute_url())
    schema = {
        '@context': 'https://schema.org', '@type': 'Product', 'name': product.name,
        'description': description, 'sku': product.sku, 'url': canonical,
        'brand': {'@type': 'Brand', 'name': BRAND},
        'offers': {'@type': 'Offer', 'url': canonical, 'price': str(product.price), 'priceCurrency': 'GBP',
                   'availability': 'https://schema.org/InStock' if product.in_stock else 'https://schema.org/OutOfStock'},
    }
    if images:
        schema['image'] = [absolute_url(image.image.url) for image in images]
    return {
        'seo_title': product.name + ' | ' + BRAND, 'seo_description': description[:160],
        'seo_canonical': canonical, 'seo_image': absolute_url(primary.image.url) if primary else absolute_url(static('images/mih_logo.png')),
        'seo_type': 'product', 'seo_json_ld': json_ld(schema),
    }


def collection_metadata(collection):
    description = ' '.join(strip_tags(collection.description).split()) or f'Browse the {collection.name} collection at {BRAND}.'
    return {'seo_title': collection.name + ' | ' + BRAND, 'seo_description': description[:160],
            'seo_canonical': absolute_url(collection.get_absolute_url()),
            'seo_image': absolute_url(collection.image.url) if collection.image else absolute_url(static('images/mih_logo.png'))}


class SeoHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (response.get('Content-Type', '').startswith('text/html') and not is_indexable(request)) or request.path.startswith(PRIVATE_PREFIXES) or response.status_code >= 400:
            response['X-Robots-Tag'] = 'noindex, follow'
        return response


class CanonicalSitemapRequest:
    scheme = 'https'

    def __init__(self, request):
        self.request = request

    def get_host(self):
        return PUBLIC_DOMAIN

    def __getattr__(self, name):
        return getattr(self.request, name)


def sitemap_index(request, sitemaps):
    request.get_host()
    return index(CanonicalSitemapRequest(request), sitemaps=sitemaps)


def sitemap_section(request, sitemaps, section):
    request.get_host()
    return sitemap(CanonicalSitemapRequest(request), sitemaps=sitemaps, section=section)


def robots_response(request):
    lines = ['User-agent: *', 'Allow: /']
    lines.extend('Disallow: ' + path for path in PRIVATE_PREFIXES)
    lines.append('Sitemap: ' + PUBLIC_ORIGIN + '/sitemap.xml')
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')


def google_verification(request):
    return FileResponse(
        (settings.BASE_DIR / 'static/web/google26bfb82dbebf9ae3.html').open('rb'),
        content_type='text/html; charset=utf-8',
    )
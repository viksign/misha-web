from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Collection, Product
from .seo import PUBLIC_DOMAIN
from types import SimpleNamespace


class CanonicalSitemap(Sitemap):
    protocol = 'https'
    limit = 5000

    def get_urls(self, page=1, site=None, protocol=None):
        return super().get_urls(page=page, site=SimpleNamespace(domain=PUBLIC_DOMAIN), protocol='https')


class ProductSitemap(CanonicalSitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Product.objects.filter(active=True).order_by('pk')

    def lastmod(self, obj):
        return obj.updated_at


class CollectionSitemap(CanonicalSitemap):
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        return Collection.objects.filter(active=True).order_by('pk')


class StaticViewSitemap(CanonicalSitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return ['home', 'catalogue', 'heritage', 'contact']

    def location(self, item):
        return reverse(item)

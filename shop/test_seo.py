from html.parser import HTMLParser
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Collection, Product, ProductImage
from .seo import PUBLIC_ORIGIN


class PageMetadata(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.meta, self.links, self.images, self.scripts = {}, {}, [], []
        self.current_script = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'meta':
            self.meta[attributes.get('name') or attributes.get('property')] = attributes.get('content')
        elif tag == 'link':
            self.links[attributes.get('rel')] = attributes.get('href')
        elif tag == 'img':
            self.images.append(attributes)
        elif tag == 'script' and attributes.get('type') == 'application/ld+json':
            self.current_script = ''

    def handle_data(self, data):
        if self.current_script is not None:
            self.current_script += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.current_script is not None:
            self.scripts.append(self.current_script)
            self.current_script = None


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SeoTests(TestCase):
    def setUp(self):
        self.collection = Collection.objects.create(name='Island collection', slug='island-seo', description='Actual collection description.')
        self.product = Product.objects.create(name='Real pendant', slug='real-seo-pendant', sku='SEO-001', description='Actual stainless steel pendant.', price='29.99', stock_quantity=5, collection=self.collection)

    @override_settings(ALLOWED_HOSTS=['mishaislandheritage.com', 'www.mishaislandheritage.com'])
    def test_google_verification_is_public_at_exact_root_path(self):
        expected = b'google-site-verification: google26bfb82dbebf9ae3.html'
        for host in ['mishaislandheritage.com', 'www.mishaislandheritage.com']:
            with self.subTest(host=host):
                response = self.client.get('/google26bfb82dbebf9ae3.html', HTTP_HOST=host)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b''.join(response.streaming_content).strip(), expected)
                self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
                self.assertNotIn('Location', response)
                response.close()

    def test_public_pages_have_canonical_and_indexable_metadata(self):
        for path in ['/', '/jewellery/', '/heritage/', '/contact/', self.product.get_absolute_url(), self.collection.get_absolute_url()]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                metadata = PageMetadata(response.content.decode())
                self.assertEqual(metadata.links['canonical'], PUBLIC_ORIGIN + path)
                self.assertEqual(metadata.meta['robots'], 'index, follow')
                self.assertNotIn('noindex', response.get('X-Robots-Tag', ''))

    def test_private_and_internal_search_pages_are_noindex(self):
        for path in ['/login/', '/account/', '/bag/', '/password-reset/', '/jewellery/?q=pendant', '/jewellery/?sort=price_asc']:
            response = self.client.get(path)
            self.assertIn('noindex', response['X-Robots-Tag'])
            metadata = PageMetadata(response.content.decode())
            self.assertIn('noindex', metadata.meta['robots'])
        search = PageMetadata(self.client.get('/jewellery/?q=pendant').content.decode())
        self.assertNotIn('canonical', search.links)
        metadata = PageMetadata(self.client.get(self.product.get_absolute_url() + '?utm_source=test').content.decode())
        self.assertEqual(metadata.links['canonical'], PUBLIC_ORIGIN + self.product.get_absolute_url())

    def test_product_schema_uses_real_values_and_is_safe(self):
        self.product.name = 'Real </script><script>alert(1)</script> pendant'
        self.product.save()
        response = self.client.get(self.product.get_absolute_url())
        metadata = PageMetadata(response.content.decode())
        schema = json.loads(metadata.scripts[0])
        self.assertEqual(schema['@type'], 'Product')
        self.assertEqual(schema['name'], self.product.name)
        self.assertEqual(schema['sku'], 'SEO-001')
        self.assertEqual(schema['offers']['price'], '29.99')
        self.assertEqual(schema['offers']['priceCurrency'], 'GBP')
        self.assertEqual(schema['offers']['availability'], 'https://schema.org/InStock')
        self.assertNotIn('</script>', metadata.scripts[0])
        self.assertNotIn('aggregateRating', schema)
        self.assertIn('Actual stainless steel pendant', metadata.meta['description'])
        self.product.stock_quantity = 0
        self.product.preorder_enabled = True
        self.product.save()
        schema = json.loads(PageMetadata(self.client.get(self.product.get_absolute_url()).content.decode()).scripts[0])
        self.assertEqual(schema['offers']['availability'], 'https://schema.org/OutOfStock')

    def test_home_organization_website_and_collection_metadata(self):
        home = PageMetadata(self.client.get('/').content.decode())
        graph = json.loads(home.scripts[0])['@graph']
        self.assertEqual([entry['@type'] for entry in graph], ['Organization', 'WebSite'])
        self.assertNotIn('address', graph[0])
        collection = PageMetadata(self.client.get(self.collection.get_absolute_url()).content.decode())
        self.assertEqual(collection.meta['description'], self.collection.description)
        self.assertEqual(collection.meta['og:url'], PUBLIC_ORIGIN + self.collection.get_absolute_url())

    @override_settings(SITE_URL='http://localhost:8000')
    def test_robots_and_sitemaps_never_use_runtime_internal_hosts(self):
        robots = self.client.get('/robots.txt')
        self.assertEqual(robots.status_code, 200)
        self.assertContains(robots, 'Sitemap: https://mishaislandheritage.com/sitemap.xml')
        self.assertNotContains(robots, 'Disallow: /\n')
        self.assertContains(robots, 'Disallow: /admin/')
        root = ET.fromstring(self.client.get('/sitemap.xml').content)
        sections = [element.text for element in root.iter() if element.tag.endswith('loc')]
        self.assertEqual(len(sections), 3)
        public_urls = []
        for url in sections:
            self.assertEqual(urlparse(url).netloc, 'mishaislandheritage.com')
            self.assertEqual(urlparse(url).scheme, 'https')
            response = self.client.get(urlparse(url).path)
            self.assertEqual(response.status_code, 200)
            public_urls.extend(element.text for element in ET.fromstring(response.content).iter() if element.tag.endswith('loc'))
        self.assertIn(PUBLIC_ORIGIN + self.product.get_absolute_url(), public_urls)
        self.assertIn(PUBLIC_ORIGIN + self.collection.get_absolute_url(), public_urls)
        for url in public_urls:
            self.assertTrue(url.startswith(PUBLIC_ORIGIN + '/'))
            self.assertEqual(self.client.get(urlparse(url).path).status_code, 200)
            self.assertNotIn('/checkout/', url)
            self.assertNotIn('/account/', url)

    def test_sitemap_excludes_inactive_records_and_supports_large_catalogues(self):
        Product.objects.create(name='Hidden', slug='seo-hidden', sku='SEO-HIDDEN', price='20', active=False)
        Product.objects.bulk_create([Product(name=f'Product {index}', slug=f'seo-large-{index}', sku=f'SEO-LARGE-{index}', price='20') for index in range(5000)])
        root = ET.fromstring(self.client.get('/sitemap.xml').content)
        self.assertTrue(any(element.text.endswith('/sitemap-products.xml?p=2') for element in root.iter() if element.tag.endswith('loc')))
        response = self.client.get('/sitemap-products.xml?p=2')
        urls = [element.text for element in ET.fromstring(response.content).iter() if element.tag.endswith('loc')]
        self.assertEqual(len(urls), 1)
        self.assertNotContains(response, 'seo-hidden')

    def test_product_images_have_dimensions_alt_text_and_loading_metadata(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            buffer = BytesIO()
            Image.new('RGB', (640, 800), 'white').save(buffer, format='PNG')
            image = ProductImage.objects.create(product=self.product, image=SimpleUploadedFile('seo.png', buffer.getvalue(), content_type='image/png'), alt_text='Actual pendant image', is_primary=True)
            metadata = PageMetadata(self.client.get(self.product.get_absolute_url()).content.decode())
            photo = next(attributes for attributes in metadata.images if 'data-gallery-image' in attributes)
            self.assertEqual((photo['width'], photo['height'], photo['alt']), ('640', '800', 'Actual pendant image'))
            self.assertEqual(photo['loading'], 'eager')
            schema = json.loads(metadata.scripts[0])
            self.assertEqual(schema['image'], [PUBLIC_ORIGIN + image.image.url])
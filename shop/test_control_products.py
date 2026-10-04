from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from html.parser import HTMLParser

from .models import Collection, Order, OrderItem, Product


class ControlProductTests(TestCase):
    def test_stock_inventory_headers_align_with_row_cells(self):
        staff = User.objects.create_user(username='stock-layout-staff', is_staff=True)
        self.client.force_login(staff)
        product = Product.objects.create(name='Sacred Roots Pendant with a detailed collection name', slug='stock-layout-product', sku='STOCK-LAYOUT-001', price='29.99', stock_quantity=12)
        response = self.client.get(reverse('control_stock'))

        class InventoryParser(HTMLParser):
            active = False
            headers = 0
            cells = 0

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == 'table' and 'stock-inventory-table' in attributes.get('class', '').split():
                    self.active = True
                if self.active and tag == 'th':
                    self.headers += 1
                if self.active and tag == 'td':
                    self.cells += 1

            def handle_endtag(self, tag):
                if tag == 'table':
                    self.active = False

        parser = InventoryParser()
        parser.feed(response.content.decode())
        self.assertEqual(parser.headers, 9)
        self.assertEqual(parser.cells, 9)
        self.assertContains(response, product.name)
        self.assertContains(response, 'Stock &amp; visibility', html=False)
        self.assertContains(response, 'name="stock_quantity"', html=False)

    def test_product_list_shows_feature_and_collection_and_filters_by_collection(self):
        staff_user = User.objects.create_user(username='staff', password='test-password', is_staff=True)
        featured_collection = Collection.objects.create(name='Featured collection', slug='featured-collection')
        other_collection = Collection.objects.create(name='Other collection', slug='other-collection')
        Product.objects.create(
            name='Featured product',
            slug='featured-product',
            sku='FEAT-001',
            collection=featured_collection,
            description='A test product.',
            price='50.00',
            featured=True,
        )
        Product.objects.create(
            name='Other product',
            slug='other-product',
            sku='OTHER-001',
            collection=other_collection,
            description='A test product.',
            price='40.00',
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('control_products'), {'collection': featured_collection.slug})

        self.assertContains(response, '<th>Collection</th>', html=False)
        self.assertContains(response, '<th>Featured</th>', html=False)
        self.assertContains(response, 'Featured product')
        self.assertContains(response, 'Featured collection')
        self.assertContains(response, '<td>Yes</td>', html=False)
        self.assertNotContains(response, 'Other product')

    def test_product_associated_with_order_cannot_be_removed(self):
        staff_user = User.objects.create_user(username='staff', password='test-password', is_staff=True)
        product = Product.objects.create(
            name='Ordered necklace',
            slug='ordered-necklace',
            sku='ORD-001',
            description='A test product.',
            price='95.00',
        )
        order = Order.objects.create(
            email='customer@example.com',
            shipping_name='Customer',
            shipping_address1='1 Test Street',
            shipping_city='London',
            shipping_postcode='N1 1AA',
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            unit_price=product.price,
            quantity=1,
        )
        self.client.force_login(staff_user)

        response = self.client.post(reverse('control_products'), {
            'action': 'delete',
            'product_id': product.pk,
        }, follow=True, secure=True)

        self.assertRedirects(response, reverse('control_products'))
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertTrue(product.orderitem_set.exists())
        self.assertContains(response, 'This product cannot be removed because it is associated with an order.')
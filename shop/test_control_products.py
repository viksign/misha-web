from django.contrib.auth.models import User
from django.contrib import admin
from django.test import Client, RequestFactory, TestCase, override_settings
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
        self.assertContains(response, 'Product deletion is locked.')


@override_settings(SECURE_SSL_REDIRECT=False, STAFF_MFA_REQUIRED=False)
class ProductDeletionLockTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='product-lock-staff', is_staff=True)
        self.client.force_login(self.staff)
        self.product = Product.objects.create(
            name='Protected pendant', slug='protected-pendant', sku='LOCK-001',
            description='Keep this piece.', price='20.00', stock_quantity=5,
        )
        self.url = reverse('control_products')

    def test_unordered_product_deletion_is_locked_and_remove_button_is_absent(self):
        response = self.client.post(self.url, {
            'action': 'delete', 'product_id': self.product.pk,
        }, follow=True)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertContains(response, 'Product deletion is locked.')
        self.assertNotContains(response, '>Hide from listing<')
        self.assertNotContains(response, 'value="hide"')
        self.assertNotContains(response, 'value="delete"')
        self.assertContains(response, 'class="product-catalogue-actions"')
        self.assertContains(response, 'class="button product-add-button"')

    def test_hide_and_restore_preserve_product_stock_history_and_orders(self):
        order = Order.objects.create(email='customer@example.com', shipping_name='Customer')
        OrderItem.objects.create(
            order=order, product=self.product, product_name=self.product.name,
            sku=self.product.sku, unit_price=self.product.price, quantity=1,
        )
        movements = list(self.product.stock_movements.values_list('pk', flat=True))
        response = self.client.post(self.url, {'action': 'hide', 'product_id': self.product.pk}, follow=True)
        self.product.refresh_from_db()
        self.assertFalse(self.product.active)
        self.assertEqual(self.product.stock_quantity, 5)
        self.assertTrue(self.product.orderitem_set.exists())
        self.assertEqual(list(self.product.stock_movements.values_list('pk', flat=True)), movements)
        self.assertNotContains(response, '>Show in listing<')
        self.assertNotContains(response, 'value="show"')
        self.assertNotContains(self.client.get(reverse('catalogue')), self.product.name)
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 404)
        self.assertRedirects(self.client.post(self.url, {'action': 'show', 'product_id': self.product.pk}), self.url)
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)
        self.assertContains(self.client.get(reverse('catalogue')), self.product.name)
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 200)
        self.assertEqual(list(self.product.stock_movements.values_list('pk', flat=True)), movements)

    def test_get_request_cannot_change_visibility(self):
        self.client.get(self.url, {'action': 'hide', 'product_id': self.product.pk})
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)

    def test_customer_cannot_hide_product(self):
        self.client.force_login(User.objects.create_user(username='product-lock-customer'))
        self.assertEqual(self.client.post(self.url, {'action': 'hide', 'product_id': self.product.pk}).status_code, 302)
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)

    def test_hide_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(self.url, {'action': 'hide', 'product_id': self.product.pk}).status_code, 403)
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)

    def test_admin_deletion_is_locked_even_for_superusers(self):
        model_admin = admin.site._registry[Product]
        request = RequestFactory().get('/admin/shop/product/')
        request.user = User.objects.create_superuser(username='product-lock-admin', email='admin@example.com', password='test-password')
        self.assertFalse(model_admin.has_delete_permission(request, self.product))
        self.assertNotIn('delete_selected', model_admin.get_actions(request))
        self.client.force_login(request.user)
        self.assertEqual(self.client.post(reverse('admin:shop_product_delete', args=[self.product.pk]), {'post': 'yes'}).status_code, 403)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
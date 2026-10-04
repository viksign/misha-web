from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.apps import apps
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Collection, Order, OrderItem, Product, StockMovement
from .services import mark_stripe_order_paid


@override_settings(SECURE_SSL_REDIRECT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class StockMovementTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='inventory-staff', first_name='Asha', last_name='Patel', is_staff=True)
        self.product = Product.objects.create(name='Inventory pendant', slug='inventory-pendant', sku='INV-001', price=Decimal('29.99'), stock_quantity=75)

    def test_opening_restocks_and_adjustments_preserve_actor_and_balances(self):
        opening = self.product.stock_movements.get()
        self.assertEqual((opening.reason, opening.quantity_change, opening.balance_before, opening.balance_after), ('opening', 75, 0, 75))
        self.product.stock_quantity = 100
        self.product.save(stock_actor=self.staff)
        restock = self.product.stock_movements.first()
        self.assertEqual((restock.reason, restock.quantity_change, restock.balance_before, restock.balance_after), ('restock', 25, 75, 100))
        self.assertEqual(restock.actor_name, 'Asha Patel')
        self.product.stock_quantity = 98
        self.product.save(stock_actor=self.staff)
        self.assertEqual(self.product.stock_movements.first().quantity_change, -2)
        self.assertEqual(self.product.stock_movements.first().reason, 'adjustment')
        count = self.product.stock_movements.count()
        self.product.price = Decimal('30.99')
        self.product.save()
        self.assertEqual(self.product.stock_movements.count(), count)

    def test_paid_order_has_one_linked_movement_on_repeated_confirmation(self):
        order = Order.objects.create(email='customer@example.com', shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA')
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name, sku=self.product.sku, unit_price=self.product.price, quantity=3)
        mark_stripe_order_paid(order)
        mark_stripe_order_paid(Order.objects.get(pk=order.pk))
        sale = StockMovement.objects.get(order=order, reason='sale')
        self.assertEqual((sale.quantity_change, sale.balance_before, sale.balance_after), (-3, 75, 72))
        self.assertEqual(sale.order_reference, str(order.pk))

    def test_rollback_rolls_back_stock_and_history_together(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.product.stock_quantity = 60
                self.product.save(stock_actor=self.staff)
                raise RuntimeError('Rollback test')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 75)
        self.assertEqual(self.product.stock_movements.count(), 1)

    def test_deleted_product_keeps_ledger_snapshot(self):
        product_name, sku = self.product.name, self.product.sku
        self.product.delete()
        movement = StockMovement.objects.get(sku=sku)
        self.assertIsNone(movement.product)
        self.assertEqual(movement.product_name, product_name)

    def test_existing_stock_is_snapshotted_without_deducting_or_inventing_movements(self):
        self.product.stock_movements.all().delete()
        migration = import_module('shop.migrations.0041_stock_movement_history')
        migration.record_opening_balances(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        opening = self.product.stock_movements.get()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 75)
        self.assertEqual((opening.quantity_change, opening.balance_before, opening.balance_after), (0, 75, 75))


@override_settings(SECURE_SSL_REDIRECT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class InventoryBrowsingTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='inventory-browser', first_name='Asha', last_name='Patel', is_staff=True)
        self.collection = Collection.objects.create(name='Main collection', slug='inventory-main')
        self.client.force_login(self.staff)

    def test_101_products_paginate_with_filters_and_stable_sorting(self):
        Product.objects.bulk_create([
            Product(name=f'Product {index:03}', slug=f'inventory-product-{index}', sku=f'INV-{index:03}', collection=self.collection, price='29.99', stock_quantity=20)
            for index in range(101)
        ])
        params = {'collection': self.collection.slug, 'stock': 'healthy', 'visibility': 'active', 'sort': 'sku', 'q': 'INV-'}
        first = self.client.get(reverse('control_stock'), params)
        self.assertEqual(first.context['product_page'].paginator.count, 101)
        self.assertEqual(len(first.context['products']), 50)
        self.assertEqual(first.context['products'][0].sku, 'INV-000')
        self.assertEqual(parse_qs(first.context['pagination_query']), {key: [value] for key, value in params.items()})
        second = self.client.get(reverse('control_stock'), {**params, 'page': 2})
        self.assertEqual(len(second.context['products']), 50)
        self.assertEqual(second.context['products'][0].sku, 'INV-050')
        last = self.client.get(reverse('control_stock'), {**params, 'page': 3})
        self.assertEqual([product.sku for product in last.context['products']], ['INV-100'])

    def test_per_product_thresholds_drive_filters_chart_and_replenishment(self):
        low = Product.objects.create(name='High reorder pendant', slug='high-reorder-pendant', sku='REORDER-01', collection=self.collection, price='29.99', stock_quantity=10, reorder_level=20, restock_target=50)
        Product.objects.create(name='Healthy pendant', slug='healthy-reorder-pendant', sku='REORDER-02', collection=self.collection, price='29.99', stock_quantity=30, reorder_level=20)
        Product.objects.create(name='Hidden pendant', slug='hidden-reorder-pendant', sku='REORDER-03', collection=self.collection, price='29.99', stock_quantity=0, active=False)
        response = self.client.get(reverse('control_stock'), {'collection': self.collection.slug, 'stock': 'low', 'visibility': 'active', 'q': 'High reorder'})
        self.assertEqual([product.pk for product in response.context['products']], [low.pk])
        self.assertEqual(low.stock_status, 'low')
        self.assertEqual(low.replenishment_units, 40)
        chart = self.client.get(reverse('dashboard_inventory_chart')).json()
        self.assertEqual(chart['datasets'][0]['data'], [1, 1, 0])
        dashboard = self.client.get(reverse('control_panel'))
        self.assertEqual([product.pk for product in dashboard.context['inventory_products']], [low.pk])
        self.assertContains(dashboard, 'Priority replenishment')

    def test_stock_update_keeps_filters_and_records_staff(self):
        product = Product.objects.create(name='Editable stock pendant', slug='editable-stock-pendant', sku='EDIT-STOCK', collection=self.collection, price='29.99', stock_quantity=3)
        url = reverse('control_stock') + '?collection=inventory-main&stock=attention&visibility=active&sort=stock_desc&q=EDIT-STOCK&page=2'
        response = self.client.post(url, {'product_id': product.pk, 'stock_quantity': 25, 'active': 'on'})
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlparse(response['Location']).query)
        self.assertEqual(params['collection'], ['inventory-main'])
        self.assertEqual(params['stock'], ['attention'])
        self.assertEqual(params['page'], ['2'])
        movement = StockMovement.objects.filter(product=product, reason='restock').get()
        self.assertEqual((movement.quantity_change, movement.balance_after), (22, 25))
        self.assertEqual(movement.actor, self.staff)
        self.assertEqual(movement.actor_name, 'Asha Patel')

    def test_history_is_paginated_filtered_and_staff_only(self):
        product = Product.objects.create(name='History pendant', slug='history-pendant', sku='HISTORY-01', price='29.99', stock_quantity=10)
        for index in range(51):
            product.stock_quantity += 1
            product.save(stock_actor=self.staff)
        params = {'q': product.sku, 'reason': 'restock'}
        first = self.client.get(reverse('control_stock_history'), params)
        self.assertEqual(first.context['movement_page'].paginator.count, 51)
        self.assertEqual(len(first.context['movement_page']), 50)
        self.assertContains(first, 'Asha Patel')
        last = self.client.get(reverse('control_stock_history'), {**params, 'page': 2})
        self.assertEqual(len(last.context['movement_page']), 1)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('control_stock_history')).status_code, 302)
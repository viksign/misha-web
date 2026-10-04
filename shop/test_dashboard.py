from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import InterestSignup, Order, Product


@override_settings(SECURE_SSL_REDIRECT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class DashboardChartTests(TestCase):
    endpoints = ['dashboard_inventory_chart', 'dashboard_fulfilment_chart', 'dashboard_team_chart']

    def setUp(self):
        self.staff = User.objects.create_user(username='dashboard-staff', first_name='Asha', last_name='Patel', is_staff=True)

    def test_chart_endpoints_require_active_staff(self):
        for name in self.endpoints:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        customer = User.objects.create_user(username='dashboard-customer')
        self.client.force_login(customer)
        for name in self.endpoints:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_inventory_chart_is_ordered_by_stock_and_excludes_inactive_products(self):
        Product.objects.create(name='Available pendant', slug='available-chart', sku='CHART-01', price='29.99', stock_quantity=75)
        Product.objects.create(name='Empty pendant', slug='empty-chart', sku='CHART-02', price='29.99', stock_quantity=0)
        Product.objects.create(name='Hidden pendant', slug='hidden-chart', sku='CHART-03', price='29.99', stock_quantity=50, active=False)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('dashboard_inventory_chart'))
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        data = response.json()
        self.assertEqual(data['labels'], ['Healthy stock', 'Low stock', ['Out of stock', 'Pre-order interest: 0']])
        self.assertEqual(data['datasets'][0]['data'], [1, 0, 1])
        self.assertFalse(data['empty'])

    def test_fulfilment_and_workload_charts_include_only_paid_orders(self):
        for status, payment_status, assignee in [('pending', 'paid', None), ('processing', 'paid', self.staff), ('processing', 'pending', self.staff), ('shipped', 'paid', self.staff)]:
            Order.objects.create(email='customer@example.com', shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA', status=status, payment_status=payment_status, assigned_to=assignee)
        self.client.force_login(self.staff)
        stages = self.client.get(reverse('dashboard_fulfilment_chart')).json()
        self.assertEqual(stages['labels'], ['Pending', 'Processing', 'Shipped', 'Completed'])
        self.assertEqual(stages['datasets'][0]['data'], [1, 1, 1, 0])
        workload = self.client.get(reverse('dashboard_team_chart')).json()
        self.assertEqual(workload['labels'], ['Asha Patel'])
        self.assertEqual(workload['datasets'][0]['data'], [1])
        self.assertEqual(workload['percentages'], [100.0])
        self.assertEqual(workload['total'], 1)

    def test_empty_charts_return_a_valid_empty_state(self):
        self.client.force_login(self.staff)
        for name in self.endpoints:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['empty'])

    def test_dashboard_has_three_operational_sections_and_precise_inventory_summary(self):
        Product.objects.create(name='Available inventory', slug='summary-available', sku='SUMMARY-01', price='29.99', stock_quantity=75)
        Product.objects.create(name='Low inventory', slug='summary-low', sku='SUMMARY-02', price='29.99', stock_quantity=3)
        Product.objects.create(name='Unavailable inventory', slug='summary-empty', sku='SUMMARY-03', price='29.99', stock_quantity=0)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.context['inventory_counts'], {'units': 78, 'products': 3, 'available': 2, 'low': 1, 'empty': 1, 'healthy': 1})
        self.assertEqual({product.sku for product in response.context['inventory_products']}, {'SUMMARY-02', 'SUMMARY-03'})
        self.assertContains(response, 'data-dashboard-chart', count=3)
        for name in self.endpoints:
            self.assertContains(response, reverse(name))
        self.assertContains(response, 'Inventory')
        self.assertContains(response, 'Order fulfilment')
        self.assertContains(response, 'Team workload')
        self.assertNotContains(response, 'class="control-card"', html=False)

    def test_team_percentages_include_unassigned_and_exclude_unpaid_orders(self):
        other = User.objects.create_user(username='other-chart-staff', first_name='Ravi', last_name='Shah', is_staff=True)
        for assignee, quantity in [(self.staff, 3), (other, 1), (None, 2)]:
            for index in range(quantity):
                Order.objects.create(email='customer@example.com', shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA', status='processing', payment_status='paid', assigned_to=assignee)
        Order.objects.create(email='unpaid@example.com', shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA', status='processing', payment_status='pending', assigned_to=other)
        self.client.force_login(self.staff)
        data = self.client.get(reverse('dashboard_team_chart')).json()
        self.assertEqual(data['total'], 6)
        shares = dict(zip(data['labels'], data['percentages']))
        self.assertEqual(shares, {'Asha Patel': 50.0, 'Ravi Shah': 16.7, 'Unassigned': 33.3})
        self.assertEqual(len(set(data['datasets'][0]['backgroundColor'])), 3)
        response = self.client.get(reverse('control_panel'))
        self.assertContains(response, 'data-chart-type="doughnut"', count=1)
        self.assertContains(response, 'Share of processing orders by staff member')

    def test_out_of_stock_label_counts_preorder_requests_not_units(self):
        out = Product.objects.create(name='Pre-order pendant', slug='interest-chart-out', sku='INTEREST-01', price='29.99', stock_quantity=0, preorder_enabled=True)
        available = Product.objects.create(name='Restocked pendant', slug='interest-chart-available', sku='INTEREST-02', price='29.99', stock_quantity=10, preorder_enabled=True)
        hidden = Product.objects.create(name='Hidden pre-order pendant', slug='interest-chart-hidden', sku='INTEREST-03', price='29.99', stock_quantity=0, preorder_enabled=True, active=False)
        for email, product, interest_type, quantity in [
            ('first@example.com', out, 'preorder', 3),
            ('second@example.com', out, 'preorder', 7),
            ('restock@example.com', out, 'restock', 1),
            ('available@example.com', available, 'preorder', 1),
            ('hidden@example.com', hidden, 'preorder', 1),
        ]:
            InterestSignup.objects.create(user=self.staff, email=email, product=product, interest_type=interest_type, desired_quantity=quantity)
        self.client.force_login(self.staff)
        data = self.client.get(reverse('dashboard_inventory_chart')).json()
        self.assertEqual(data['preorder_interest_count'], 2)
        self.assertEqual(data['labels'][2], ['Out of stock', 'Pre-order interest: 2'])
        self.assertEqual(data['datasets'][0]['data'], [1, 0, 1])
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.context['inventory_preorder_interest_count'], 2)
        products = {product.pk: product for product in response.context['inventory_products']}
        self.assertEqual(products[out.pk].preorder_interest_count, 2)
        self.assertContains(response, '<th>Pre-order interest</th>', html=False)
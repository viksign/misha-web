from importlib import import_module
from types import SimpleNamespace
from datetime import datetime, time, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from .forms import OrderManagementForm
from .models import DeliveryOption, Order, OrderItem, Product
from .services import mark_stripe_order_paid


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class OrderFulfilmentTests(TestCase):
    def create_order(self, **values):
        return Order.objects.create(
            email='customer@example.com',
            shipping_name='Customer',
            shipping_address1='1 Test Street',
            shipping_city='London',
            shipping_postcode='N1 1AA',
            **values,
        )

    def test_confirmed_payment_enters_pending_fulfilment(self):
        order = self.create_order()
        mark_stripe_order_paid(order)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.payment_status, 'paid')
        self.assertIsNotNone(order.paid_at)

    def test_repeat_confirmation_preserves_fulfilment_and_payment_time(self):
        for status in ['processing', 'shipped', 'completed']:
            with self.subTest(status=status):
                order = self.create_order()
                mark_stripe_order_paid(order)
                paid_at = order.paid_at
                order.status = status
                order.save(update_fields=['status'])
                mark_stripe_order_paid(order)
                order.refresh_from_db()
                self.assertEqual(order.status, status)
                self.assertEqual(order.paid_at, paid_at)

    def test_legacy_paid_confirmation_enters_pending_fulfilment(self):
        order = self.create_order(status='paid', payment_status='paid')
        mark_stripe_order_paid(order)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

    def test_staff_open_order_counts_include_only_paid_unfulfilled_orders(self):
        staff = User.objects.create_user(username='order-staff', password='test-password', is_staff=True)
        self.client.force_login(staff)
        for status in ['pending', 'paid', 'processing', 'shipped', 'completed', 'cancelled', 'return']:
            self.create_order(status=status, payment_status='paid')
        self.create_order(status='pending', payment_status='pending')
        self.create_order(status='pending', payment_status='failed')
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pending_order_count'], 3)
        notification = next(item for item in response.context['admin_notifications'] if item['label'] == 'Pending orders')
        self.assertEqual(notification['count'], 3)

    def test_migration_queues_only_legacy_paid_unprocessed_orders(self):
        legacy = self.create_order(status='paid', payment_status='paid')
        unpaid = self.create_order(status='paid', payment_status='pending')
        shipped = self.create_order(status='shipped', payment_status='paid')
        migration = import_module('shop.migrations.0038_paid_orders_pending_fulfilment')
        migration.queue_paid_orders(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        legacy.refresh_from_db()
        unpaid.refresh_from_db()
        shipped.refresh_from_db()
        self.assertEqual(legacy.status, 'pending')
        self.assertEqual(legacy.payment_status, 'paid')
        self.assertEqual(unpaid.status, 'paid')
        self.assertEqual(shipped.status, 'shipped')

    def management_data(self, order, **values):
        data = {'order_id': order.pk, 'status': 'processing', 'assigned_to': '', 'delivery_method': 'standard_uk', 'delivery_cost': '0.00', 'discount_amount': '0.00'}
        data.update(values)
        return data

    def test_status_and_assignment_choices_are_restricted(self):
        self.assertNotIn('paid', dict(Order.STATUS_CHOICES))
        self.assertIn('paid', dict(Order.PAYMENT_STATUS_CHOICES))
        order = self.create_order(payment_status='paid')
        form = OrderManagementForm(self.management_data(order, status='paid'), instance=order)
        self.assertFalse(form.is_valid())
        self.assertIn('status', form.errors)
        for username, is_staff, is_active in [('customer', False, True), ('inactive', True, False)]:
            user = User.objects.create_user(username=username, is_staff=is_staff, is_active=is_active)
            form = OrderManagementForm(self.management_data(order, assigned_to=user.pk), instance=order)
            self.assertFalse(form.is_valid())
            self.assertIn('assigned_to', form.errors)

    def test_single_order_processing_assigns_staff_and_records_time(self):
        staff = User.objects.create_user(username='processor', first_name='Asha', last_name='Patel', is_staff=True)
        self.client.force_login(staff)
        order = self.create_order(payment_status='paid')
        response = self.client.post(reverse('control_orders'), self.management_data(order))
        self.assertRedirects(response, reverse('control_orders'), fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.status, 'processing')
        self.assertEqual(order.assigned_to, staff)
        self.assertEqual(order.payment_status, 'paid')
        self.assertIsNotNone(order.processing_started_at)
        response = self.client.get(reverse('control_orders'))
        self.assertContains(response, '<th>Processing by</th>', html=False)
        self.assertContains(response, 'Asha Patel')
        self.assertContains(response, 'Fulfilment timeline')

    def test_manual_assignment_and_batch_updates_preserve_existing_processor(self):
        staff = User.objects.create_user(username='manager', is_staff=True)
        processor = User.objects.create_user(username='other-processor', is_staff=True)
        self.client.force_login(staff)
        assigned = self.create_order(payment_status='paid')
        self.client.post(reverse('control_orders'), self.management_data(assigned, assigned_to=processor.pk))
        assigned.refresh_from_db()
        first_started_at = assigned.processing_started_at
        unassigned = self.create_order(payment_status='paid')
        response = self.client.post(reverse('control_orders'), {'action': 'batch_update', 'batch_status': 'processing', 'order_ids': [assigned.pk, unassigned.pk]})
        self.assertRedirects(response, reverse('control_orders'), fetch_redirect_response=False)
        assigned.refresh_from_db()
        unassigned.refresh_from_db()
        self.assertEqual(assigned.assigned_to, processor)
        self.assertEqual(assigned.processing_started_at, first_started_at)
        self.assertEqual(unassigned.assigned_to, staff)
        self.assertIsNotNone(unassigned.processing_started_at)
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.context['fulfilment_counts']['processing'], 2)
        self.assertEqual(len(response.context['processing_workload']), 2)
        self.assertContains(response, 'Team workload')

    def test_timestamps_are_preserved_and_dashboard_averages_are_correct(self):
        paid_at = timezone.now() - timedelta(days=2)
        order = self.create_order(payment_status='paid', paid_at=paid_at)
        stages = [('processing', 'processing_started_at', 2), ('shipped', 'shipped_at', 5), ('completed', 'completed_at', 9)]
        for status, field, hours in stages:
            with patch('shop.models.timezone.now', return_value=paid_at + timedelta(hours=hours)):
                order.status = status
                order.save(update_fields=['status'])
            order.refresh_from_db()
            self.assertEqual(getattr(order, field), paid_at + timedelta(hours=hours))
        completed_at = order.completed_at
        order.save()
        mark_stripe_order_paid(order)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.completed_at, completed_at)
        staff = User.objects.create_user(username='timeline-staff', is_staff=True)
        order.assigned_to = staff
        order.save(update_fields=['assigned_to'])
        self.client.force_login(staff)
        response = self.client.get(reverse('control_orders'))
        for duration in ['Waiting time: 2', 'Processing time: 3', 'Delivery time: 4', 'Total fulfilment time: 9']:
            self.assertContains(response, duration)
        response = self.client.get(reverse('control_panel'))
        averages = {name: ' '.join(duration.split()) for name, duration in response.context['fulfilment_averages'].items()}
        self.assertEqual(averages, {'waiting': '2 hours', 'processing': '3 hours', 'delivery': '4 hours', 'total': '9 hours'})
        self.assertEqual(response.context['fulfilment_counts']['completed'], 1)
        member = response.context['processing_workload'][0]
        self.assertEqual(member['completed_count'], 1)
        self.assertEqual(' '.join(member['average_processing'].split()), '3 hours')
        self.assertEqual(' '.join(member['average_total'].split()), '9 hours')

    def test_missing_historical_times_are_not_invented_or_averaged(self):
        order = self.create_order(payment_status='paid')
        Order.objects.filter(pk=order.pk).update(status='processing')
        order.refresh_from_db()
        order.save()
        order.refresh_from_db()
        self.assertIsNone(order.processing_started_at)
        staff = User.objects.create_user(username='history-staff', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.context['fulfilment_averages']['processing'], 'Not recorded')
        self.assertEqual(response.context['fulfilment_queue'][0].stage_duration, 'Not recorded')

    def test_assignment_migration_removes_remaining_paid_fulfilment_status(self):
        order = self.create_order(status='paid', payment_status='pending')
        migration = import_module('shop.migrations.0039_order_processing_assignment')
        migration.remove_legacy_paid_status(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.payment_status, 'pending')
        self.assertIsNone(order.processing_started_at)

    def test_assignment_defaults_to_logged_in_staff_and_uses_full_name_labels(self):
        staff = User.objects.create_user(username='manager@example.com', email='manager@example.com', first_name='Asha', last_name='Patel', is_staff=True)
        other = User.objects.create_user(username='other@example.com', email='other@example.com', first_name='Ravi', last_name='Shah', is_staff=True)
        order = self.create_order(payment_status='paid')
        form = OrderManagementForm(instance=order, user=staff)
        self.assertEqual(form.initial['assigned_to'], staff.pk)
        self.assertEqual(form.fields['assigned_to'].label_from_instance(staff), 'Asha Patel')
        self.assertEqual(form.fields['assigned_to'].label_from_instance(other), 'Ravi Shah')
        self.client.force_login(staff)
        response = self.client.get(reverse('control_orders'))
        displayed_order = next(item for item in response.context['orders'] if item.pk == order.pk)
        self.assertEqual(displayed_order.management_form.initial['assigned_to'], staff.pk)
        order.refresh_from_db()
        self.assertIsNone(order.assigned_to)
        response = self.client.post(reverse('control_orders'), self.management_data(order, assigned_to=other.pk))
        self.assertRedirects(response, reverse('control_orders'), fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.assigned_to, other)
        self.assertEqual(OrderManagementForm(instance=order, user=staff).initial['assigned_to'], other.pk)
        self.assertEqual(order.processor_name, 'Ravi Shah')
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.context['processing_workload'][0]['name'], 'Ravi Shah')

    def test_staff_without_full_name_never_falls_back_to_email(self):
        staff = User.objects.create_user(username='staff@example.com', email='staff@example.com', is_staff=True)
        order = self.create_order(status='processing', payment_status='paid', assigned_to=staff)
        expected = f'Staff #{staff.pk} (name not set)'
        self.assertEqual(order.processor_name, expected)
        form = OrderManagementForm(instance=order, user=staff)
        self.assertEqual(form.fields['assigned_to'].label_from_instance(staff), expected)
        self.client.force_login(staff)
        response = self.client.get(reverse('control_panel'))
        self.assertEqual(response.context['processing_workload'][0]['name'], expected)

    def test_order_sorting_preserves_search_and_shows_date_and_time(self):
        staff = User.objects.create_user(username='sorting-staff', is_staff=True)
        self.client.force_login(staff)
        older = self.create_order()
        newer = self.create_order()
        unrelated = self.create_order()
        now = timezone.now()
        Order.objects.filter(pk=older.pk).update(created_at=now - timedelta(hours=2))
        Order.objects.filter(pk=newer.pk).update(created_at=now - timedelta(hours=1))
        Order.objects.filter(pk=unrelated.pk).update(email='unrelated@example.com')
        newer.refresh_from_db()
        for sort, expected in [('oldest', [older.pk, newer.pk]), ('newest', [newer.pk, older.pk])]:
            with self.subTest(sort=sort):
                response = self.client.get(reverse('control_orders'), {'q': 'customer@example.com', 'sort': sort})
                self.assertEqual([order.pk for order in response.context['orders']], expected)
                self.assertEqual(response.context['sort'], sort)
                self.assertContains(response, f'<option value="{sort}" selected>')
                self.assertContains(response, '<th>Date &amp; time</th>', html=False)
                self.assertContains(response, date_format(timezone.localtime(newer.created_at), 'j M Y, H:i T'))

    def test_newest_is_default_and_tied_timestamps_have_stable_order(self):
        staff = User.objects.create_user(username='default-sort-staff', is_staff=True)
        self.client.force_login(staff)
        first = self.create_order()
        second = self.create_order()
        Order.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=timezone.now())
        for params, expected in [({}, [second.pk, first.pk]), ({'sort': 'invalid'}, [second.pk, first.pk]), ({'sort': 'oldest'}, [first.pk, second.pk])]:
            response = self.client.get(reverse('control_orders'), params)
            self.assertEqual([order.pk for order in response.context['orders']], expected)
        response = self.client.get(reverse('control_orders'), {'sort': 'invalid'})
        self.assertEqual(response.context['sort'], 'newest')

    def test_multiple_status_filters_sort_only_matching_categories(self):
        staff = User.objects.create_user(username='filter-staff', is_staff=True)
        self.client.force_login(staff)
        pending = self.create_order(status='pending')
        processing = self.create_order(status='processing')
        shipped = self.create_order(status='shipped')
        now = timezone.now()
        Order.objects.filter(pk=pending.pk).update(created_at=now - timedelta(hours=2))
        Order.objects.filter(pk=processing.pk).update(created_at=now - timedelta(hours=1))
        for sort, expected in [('oldest', [pending.pk, processing.pk]), ('newest', [processing.pk, pending.pk])]:
            response = self.client.get(reverse('control_orders'), {'status': ['pending', 'processing'], 'sort': sort, 'q': 'customer@example.com'})
            self.assertEqual([order.pk for order in response.context['orders']], expected)
            self.assertEqual(response.context['selected_statuses'], ['pending', 'processing'])
            self.assertContains(response, 'name="status" value="pending" checked', html=False)
            self.assertContains(response, 'Reset filters')
        response = self.client.get(reverse('control_orders'), {'sort': 'oldest'})
        self.assertEqual(response.context['selected_statuses'], [])
        self.assertEqual({order.pk for order in response.context['orders']}, {pending.pk, processing.pk, shipped.pk})

    def test_invalid_status_filters_are_ignored(self):
        staff = User.objects.create_user(username='invalid-filter-staff', is_staff=True)
        self.client.force_login(staff)
        pending = self.create_order(status='pending')
        self.create_order(status='completed')
        response = self.client.get(reverse('control_orders'), {'status': ['pending', 'invalid', 'paid', 'pending']})
        self.assertEqual(response.context['selected_statuses'], ['pending'])
        self.assertEqual([order.pk for order in response.context['orders']], [pending.pk])

    def test_batch_update_keeps_status_search_and_sort_filters(self):
        staff = User.objects.create_user(username='filtered-batch-staff', is_staff=True)
        self.client.force_login(staff)
        order = self.create_order(payment_status='paid')
        url = reverse('control_orders') + '?status=pending&status=processing&sort=oldest&q=customer%40example.com'
        response = self.client.post(url, {'action': 'batch_update', 'batch_status': 'processing', 'order_ids': [order.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response['Location']).path, reverse('control_orders'))
        self.assertEqual(parse_qs(urlparse(response['Location']).query), {'status': ['pending', 'processing'], 'sort': ['oldest'], 'q': ['customer@example.com']})
        order.refresh_from_db()
        self.assertEqual(order.status, 'processing')
        self.assertEqual(order.assigned_to, staff)
        response = self.client.get(response['Location'])
        self.assertEqual([item.pk for item in response.context['orders']], [order.pk])

    def test_order_pages_are_limited_to_50_and_preserve_filters(self):
        staff = User.objects.create_user(username='pagination-staff', is_staff=True)
        self.client.force_login(staff)
        orders = Order.objects.bulk_create([
            Order(email='customer@example.com', shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA')
            for index in range(51)
        ])
        self.create_order(status='completed')
        params = {'status': ['pending'], 'sort': 'oldest', 'q': 'customer@example.com'}
        first = self.client.get(reverse('control_orders'), params)
        self.assertEqual(len(first.context['orders']), 50)
        self.assertEqual(first.context['order_page'].paginator.count, 51)
        self.assertEqual(first.context['order_page'].paginator.num_pages, 2)
        self.assertEqual(first.context['orders'][0].pk, orders[0].pk)
        filters = parse_qs(first.context['pagination_query'])
        self.assertEqual(filters['status'], ['pending'])
        self.assertEqual(filters['sort'], ['oldest'])
        self.assertEqual(filters['q'], ['customer@example.com'])
        self.assertEqual(filters['start'], [first.context['start_date']])
        self.assertEqual(filters['end'], [first.context['end_date']])
        second = self.client.get(reverse('control_orders'), {**params, 'page': 2})
        self.assertEqual([order.pk for order in second.context['orders']], [orders[-1].pk])
        self.assertContains(second, 'Page 2 of 2')
        self.assertContains(second, f'manage-order-{orders[-1].pk}')
        self.assertNotContains(second, f'id="manage-order-{orders[0].pk}"')

    def test_default_date_range_includes_today_and_30_days_back(self):
        staff = User.objects.create_user(username='date-boundary-staff', is_staff=True)
        self.client.force_login(staff)
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today - timedelta(days=30), time.min))
        end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
        included_start = self.create_order()
        included_end = self.create_order()
        old = self.create_order()
        future = self.create_order()
        for order, created_at in [(included_start, start), (included_end, end - timedelta(microseconds=1)), (old, start - timedelta(microseconds=1)), (future, end)]:
            Order.objects.filter(pk=order.pk).update(created_at=created_at)
        response = self.client.get(reverse('control_orders'))
        self.assertEqual({order.pk for order in response.context['orders']}, {included_start.pk, included_end.pk})
        self.assertEqual(response.context['start_date'], (today - timedelta(days=30)).isoformat())
        self.assertEqual(response.context['end_date'], today.isoformat())

    def test_custom_range_can_include_older_orders_and_invalid_ranges_fall_back(self):
        staff = User.objects.create_user(username='custom-date-staff', is_staff=True)
        self.client.force_login(staff)
        old = self.create_order()
        older_date = timezone.localdate() - timedelta(days=60)
        Order.objects.filter(pk=old.pk).update(created_at=timezone.make_aware(datetime.combine(older_date, time(23, 59))))
        current = self.create_order()
        response = self.client.get(reverse('control_orders'), {'start': older_date.isoformat(), 'end': older_date.isoformat()})
        self.assertEqual([order.pk for order in response.context['orders']], [old.pk])
        self.assertTrue(response.context['date_range_changed'])
        for params in [{'start': 'not-a-date'}, {'start': timezone.localdate().isoformat(), 'end': older_date.isoformat()}, {'end': '9999-12-31'}]:
            response = self.client.get(reverse('control_orders'), params)
            self.assertEqual([order.pk for order in response.context['orders']], [current.pk])
            self.assertContains(response, 'Enter a valid date range')

    def test_order_update_preserves_date_range_and_page(self):
        staff = User.objects.create_user(username='date-update-staff', is_staff=True)
        self.client.force_login(staff)
        order = self.create_order(payment_status='paid')
        start = (timezone.localdate() - timedelta(days=10)).isoformat()
        end = timezone.localdate().isoformat()
        url = reverse('control_orders') + f'?start={start}&end={end}&page=2&status=pending&sort=oldest'
        response = self.client.post(url, {'action': 'batch_update', 'batch_status': 'processing', 'order_ids': [order.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlparse(response['Location']).query), {'start': [start], 'end': [end], 'page': ['2'], 'status': ['pending'], 'sort': ['oldest']})

    def test_paid_order_deducts_item_quantities_once_even_with_stale_callbacks(self):
        product = Product.objects.create(name='Stock test pendant', slug='stock-test-pendant', sku='STOCK-PAY-001', price='29.99', stock_quantity=95)
        order = self.create_order()
        for quantity in [2, 3]:
            OrderItem.objects.create(order=order, product=product, product_name=product.name, sku=product.sku, unit_price=product.price, quantity=quantity)
        stale_order = Order.objects.get(pk=order.pk)
        mark_stripe_order_paid(order)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 90)
        deducted_at = order.stock_deducted_at
        self.assertIsNotNone(deducted_at)
        mark_stripe_order_paid(stale_order)
        product.refresh_from_db()
        stale_order.refresh_from_db()
        self.assertEqual(product.stock_quantity, 90)
        self.assertEqual(stale_order.stock_deducted_at, deducted_at)

    def test_starting_checkout_does_not_deduct_unpaid_stock(self):
        product = Product.objects.create(name='Checkout stock pendant', slug='checkout-stock-pendant', sku='STOCK-PAY-002', price='29.99', stock_quantity=95)
        DeliveryOption.objects.get_or_create(code='standard_uk', defaults={'label': 'UK Standard', 'price': '4.95'})
        session = self.client.session
        session['misha_cart'] = {str(product.pk): 3}
        session.save()
        with patch('shop.views.create_stripe_checkout', return_value=SimpleNamespace(url='https://checkout.stripe.com/test-checkout')):
            response = self.client.post(reverse('checkout'), {
                'email': 'customer@example.com', 'shipping_method': 'standard_uk',
                'billing_name': 'Test Customer', 'billing_address1': '10 High Street',
                'billing_city': 'London', 'billing_postcode': 'SW1A 1AA', 'billing_country': 'United Kingdom',
            })
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 95)
        order = Order.objects.get()
        self.assertEqual(order.payment_status, 'pending')
        self.assertIsNone(order.stock_deducted_at)
        mark_stripe_order_paid(order)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 92)

    def test_unpaid_checkout_event_does_not_mark_paid_or_deduct_stock(self):
        product = Product.objects.create(name='Webhook stock pendant', slug='webhook-stock-pendant', sku='STOCK-PAY-003', price='29.99', stock_quantity=95)
        order = self.create_order()
        OrderItem.objects.create(order=order, product=product, product_name=product.name, sku=product.sku, unit_price=product.price, quantity=2)
        event = {'id': 'evt_unpaid_stock_test', 'type': 'checkout.session.completed', 'data': {'object': {'metadata': {'order_id': str(order.pk)}, 'payment_status': 'unpaid'}}}
        with override_settings(STRIPE_WEBHOOK_SECRET='test-secret'), patch('shop.views.stripe.Webhook.construct_event') as construct_event:
            construct_event.return_value.to_dict.return_value = event
            response = self.client.post(reverse('stripe_webhook'), data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.payment_status, 'pending')
        self.assertIsNone(order.stock_deducted_at)
        self.assertEqual(product.stock_quantity, 95)

    def test_legacy_deduction_marker_prevents_double_deducting_existing_orders(self):
        product = Product.objects.create(name='Legacy stock pendant', slug='legacy-stock-pendant', sku='STOCK-PAY-004', price='29.99', stock_quantity=95)
        order = self.create_order(payment_status='paid')
        OrderItem.objects.create(order=order, product=product, product_name=product.name, sku=product.sku, unit_price=product.price, quantity=20)
        migration = import_module('shop.migrations.0040_order_stock_deducted_at')
        migration.mark_existing_checkout_deductions(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        order.refresh_from_db()
        self.assertIsNotNone(order.stock_deducted_at)
        mark_stripe_order_paid(order)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 95)
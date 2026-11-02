from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.contrib.auth.models import User
from django.db import close_old_connections
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .cart import Cart
from .checkout_recovery import update_unpaid_checkout
from .models import CartItem, CheckoutAttempt, DeliveryOption, Order, Product, WebhookEvent


@override_settings(
    SECURE_SSL_REDIRECT=False, STRIPE_SECRET_KEY='sk_test_recovery',
    STRIPE_WEBHOOK_SECRET='whsec_test', STRIPE_CURRENCY='gbp',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class CheckoutRecoveryTests(TestCase):
    def setUp(self):
        DeliveryOption.objects.get_or_create(code='standard_uk', defaults={'label': 'UK Standard', 'price': Decimal('3.99')})
        self.product = Product.objects.create(
            name='Recovery pendant', slug='recovery-pendant', sku='RECOVERY-001',
            price=Decimal('29.99'), stock_quantity=20,
        )
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 2}
        session['delivery_method'] = 'standard_uk'
        session.save()
        self.data = {
            'email': 'guest@example.com', 'shipping_method': 'standard_uk',
            'phone_country': 'GB', 'phone': '7123456789',
            'billing_name': 'Guest Customer', 'billing_house_flat': '10',
            'billing_address1': 'High Street', 'billing_city': 'London',
            'billing_postcode': 'SW1A 1AA', 'billing_country': 'United Kingdom',
        }
        create_patch = patch('shop.services.stripe.checkout.Session.create')
        self.create = create_patch.start()
        self.addCleanup(create_patch.stop)
        self.create.side_effect = lambda **kwargs: SimpleNamespace(
            id=f'cs_test_{self.create.call_count}', url=f'https://checkout.stripe.com/test-{self.create.call_count}',
        )
        retrieve_patch = patch('shop.checkout_recovery.stripe.checkout.Session.retrieve')
        self.retrieve = retrieve_patch.start()
        self.addCleanup(retrieve_patch.stop)
        self.retrieve.return_value.to_dict.return_value = {
            'status': 'open', 'payment_status': 'unpaid', 'url': 'https://checkout.stripe.com/resume',
        }

    def start_checkout(self):
        response = self.client.post(reverse('checkout'), self.data)
        self.assertEqual(response.status_code, 302)
        return CheckoutAttempt.objects.get().order

    def confirm_payment(self, client=None):
        self.retrieve.return_value.to_dict.return_value = {
            'status': 'complete', 'payment_status': 'paid', 'payment_intent': 'pi_test',
        }
        order = CheckoutAttempt.objects.get().order
        with patch('shop.views.update_stripe_payment_details'), patch('shop.views.send_order_confirmation', return_value=True):
            return (client or self.client).get(reverse('checkout_success'), {'session_id': order.stripe_session_id})

    def webhook(self, event_type, order, payment_status='paid'):
        event = {
            'id': f'evt_{event_type}', 'type': event_type,
            'data': {'object': {
                'id': order.stripe_session_id, 'metadata': {'order_id': str(order.pk)},
                'payment_status': payment_status,
            }},
        }
        with patch('shop.views.stripe.Webhook.construct_event') as construct, patch('shop.views.update_stripe_payment_details'), patch('shop.views.send_order_confirmation', return_value=True):
            construct.return_value.to_dict.return_value = event
            return self.client.post(reverse('stripe_webhook'), '{}', content_type='application/json')

    def test_cancel_keeps_bag_and_prefills_guest_details(self):
        order = self.start_checkout()
        response = self.client.get(reverse('checkout'), {'payment_cancelled': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your bag and details have been kept')
        self.assertContains(response, 'value="High Street"')
        self.assertEqual(response.context['pending_checkout'].pk, order.pk)
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})
        self.assertEqual(Order.objects.count(), 1)
        self.retrieve.assert_not_called()

    def test_identical_resubmission_resumes_same_order_and_session(self):
        order = self.start_checkout()
        for unused in range(2):
            response = self.client.post(reverse('checkout'), self.data)
            self.assertRedirects(response, 'https://checkout.stripe.com/resume', fetch_redirect_response=False)
        self.assertEqual(Order.objects.get().pk, order.pk)
        self.create.assert_called_once()

    def test_signed_in_retry_retains_database_cart(self):
        user = User.objects.create_user(username='recovery-customer')
        self.client.force_login(user)
        self.start_checkout()
        self.client.get(reverse('checkout'), {'payment_cancelled': '1'})
        self.client.post(reverse('checkout'), self.data)
        self.assertEqual(CartItem.objects.get(user=user).quantity, 2)
        self.assertEqual(Order.objects.count(), 1)
        self.create.assert_called_once()
        self.confirm_payment()
        self.assertFalse(CartItem.objects.filter(user=user).exists())

    @patch('shop.checkout_recovery.stripe.checkout.Session.expire')
    def test_changed_bag_expires_old_session_before_creating_replacement(self, expire):
        old_order = self.start_checkout()
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 3}
        session.save()
        expire.return_value.to_dict.return_value = {'status': 'expired'}
        response = self.client.post(reverse('checkout'), self.data)
        self.assertEqual(response.status_code, 302)
        expire.assert_called_once_with(old_order.stripe_session_id)
        old_order.refresh_from_db()
        self.assertEqual(old_order.payment_status, 'cancelled')
        self.assertEqual(old_order.status, 'cancelled')
        self.assertEqual(CheckoutAttempt.objects.get().order.items.get().quantity, 3)
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(Order.objects.count(), 2)

    @patch('shop.checkout_recovery.stripe.checkout.Session.expire')
    def test_changed_address_and_current_price_are_not_silently_reused(self, expire):
        self.start_checkout()
        self.product.price = Decimal('31.99')
        self.product.save(update_fields=['price'])
        expire.return_value.to_dict.return_value = {'status': 'expired'}
        response = self.client.post(reverse('checkout'), {**self.data, 'billing_house_flat': '20'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CheckoutAttempt.objects.get().order.total, Decimal('67.97'))
        self.assertEqual(CheckoutAttempt.objects.get().order.billing_address1, '20 High Street')
        expire.assert_called_once()

    @patch('shop.checkout_recovery.stripe.checkout.Session.expire', side_effect=stripe.APIConnectionError('Network unavailable'))
    def test_expiration_failure_blocks_replacement_and_preserves_entered_details(self, expire):
        self.start_checkout()
        with self.assertLogs('shop.views', level='ERROR'):
            response = self.client.post(reverse('checkout'), {**self.data, 'billing_house_flat': '20'})
        self.assertContains(response, 'We could not check your previous payment')
        self.assertContains(response, 'value="20"')
        self.assertEqual(Order.objects.count(), 1)
        self.create.assert_called_once()
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})

    def test_ambiguous_session_creation_failure_retries_same_idempotency_key_and_order(self):
        self.create.side_effect = stripe.APIConnectionError('Response lost')
        with self.assertLogs('shop.views', level='ERROR'):
            response = self.client.post(reverse('checkout'), self.data)
        self.assertContains(response, 'We could not start your payment')
        order = Order.objects.get()
        first_parameters = self.create.call_args.kwargs
        self.create.side_effect = None
        self.create.return_value = SimpleNamespace(id='cs_recovered', url='https://checkout.stripe.com/recovered')
        response = self.client.post(reverse('checkout'), self.data)
        self.assertRedirects(response, 'https://checkout.stripe.com/resume', fetch_redirect_response=False)
        self.assertEqual(self.create.call_args.kwargs['idempotency_key'], first_parameters['idempotency_key'])
        self.assertEqual(self.create.call_args.kwargs['metadata']['order_id'], str(order.pk))
        self.assertEqual(Order.objects.count(), 1)

    def test_already_paid_session_redirects_to_existing_order_instead_of_reordering(self):
        order = self.start_checkout()
        self.retrieve.return_value.to_dict.return_value = {'status': 'complete', 'payment_status': 'paid'}
        response = self.client.post(reverse('checkout'), {**self.data, 'billing_house_flat': '20'})
        self.assertIn(reverse('checkout_success'), response['Location'])
        self.create.assert_called_once()
        self.confirm_payment()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'paid')
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {})

    @patch('shop.checkout_recovery.stripe.checkout.Session.expire', side_effect=stripe.InvalidRequestError('Already completed', param='id'))
    def test_payment_winning_expiration_race_redirects_to_existing_order(self, expire):
        self.start_checkout()
        self.retrieve.return_value.to_dict.side_effect = [
            {'status': 'open', 'payment_status': 'unpaid'},
            {'status': 'complete', 'payment_status': 'paid'},
        ]
        response = self.client.post(reverse('checkout'), {**self.data, 'billing_house_flat': '20'})
        self.assertIn(reverse('checkout_success'), response['Location'])
        self.assertEqual(Order.objects.count(), 1)
        self.create.assert_called_once()

    def test_async_payment_blocks_reordering_and_keeps_bag_until_paid(self):
        self.start_checkout()
        self.retrieve.return_value.to_dict.return_value = {'status': 'complete', 'payment_status': 'unpaid'}
        response = self.client.post(reverse('checkout'), self.data)
        self.assertIn(reverse('checkout_success'), response['Location'])
        with patch('shop.views.update_stripe_payment_details'):
            response = self.client.get(response['Location'])
        self.assertContains(response, 'Payment confirmation pending')
        self.assertContains(response, 'Please do not place another order')
        self.assertEqual(Order.objects.get().payment_status, 'processing')
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})
        self.create.assert_called_once()

    def test_paid_bag_removal_preserves_new_items_and_extra_quantity_and_is_once_only(self):
        self.start_checkout()
        other = Product.objects.create(name='New pendant', slug='new-pendant', sku='RECOVERY-002', price='10', stock_quantity=10)
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 3, str(other.pk): 1}
        session.save()
        self.confirm_payment()
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 1, str(other.pk): 1})
        self.confirm_payment()
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 1, str(other.pk): 1})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 18)

    def test_another_browser_success_does_not_clear_its_bag(self):
        self.start_checkout()
        other_client = Client()
        session = other_client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 5}
        session.save()
        self.confirm_payment(other_client)
        self.assertEqual(other_client.session[Cart.SESSION_KEY], {str(self.product.pk): 5})
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})
        self.assertIsNone(CheckoutAttempt.objects.get().cart_cleared_at)

    def test_webhook_does_not_clear_bag_and_success_then_clears_it(self):
        order = self.start_checkout()
        response = self.webhook('checkout.session.completed', order)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})
        event = WebhookEvent.objects.get()
        self.assertIsNotNone(event.processed_at)
        self.confirm_payment()
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {})

    def test_late_failure_and_expiration_cannot_overwrite_paid_order(self):
        order = self.start_checkout()
        self.confirm_payment()
        for event_type in ['payment_intent.payment_failed', 'checkout.session.expired', 'checkout.session.async_payment_failed']:
            self.webhook(event_type, order, payment_status='unpaid')
            order.refresh_from_db()
            self.assertEqual(order.payment_status, 'paid')
            self.assertEqual(order.status, 'pending')

    def test_card_decline_leaves_order_retryable_without_new_order(self):
        order = self.start_checkout()
        self.webhook('payment_intent.payment_failed', order, payment_status='unpaid')
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'failed')
        self.assertEqual(order.status, 'pending')
        response = self.client.post(reverse('checkout'), self.data)
        self.assertRedirects(response, 'https://checkout.stripe.com/resume', fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'pending')
        self.create.assert_called_once()

    def test_stale_unpaid_event_cannot_overwrite_newly_confirmed_payment(self):
        order = self.start_checkout()
        self.confirm_payment()
        update_unpaid_checkout(order, 'failed', cancel_order=True)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'paid')
        self.assertEqual(order.status, 'pending')

    def test_new_purchase_after_confirmed_bag_removal_creates_new_order(self):
        self.start_checkout()
        self.confirm_payment()
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 1}
        session.save()
        response = self.client.post(reverse('checkout'), self.data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(CheckoutAttempt.objects.get().order.items.get().quantity, 1)

    def test_legacy_paid_status_is_protected_even_without_payment_timestamp(self):
        order = self.start_checkout()
        Order.objects.filter(pk=order.pk).update(payment_status='paid')
        update_unpaid_checkout(order, 'failed', cancel_order=True)
        self.assertEqual(order.payment_status, 'paid')
        self.assertEqual(order.status, 'pending')


@override_settings(
    SECURE_SSL_REDIRECT=False, STRIPE_SECRET_KEY='sk_test_concurrent', STRIPE_CURRENCY='gbp',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class ConcurrentCheckoutTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    @patch('shop.checkout_recovery.stripe.checkout.Session.retrieve')
    @patch('shop.services.stripe.checkout.Session.create')
    def test_simultaneous_submissions_share_one_order_and_payment_session(self, create, retrieve):
        product = Product.objects.create(name='Concurrent pendant', slug='concurrent-pendant', sku='CONCURRENT-001', price='29.99', stock_quantity=10)
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(product.pk): 1}
        session.save()
        self.client.get(reverse('checkout'))
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        create.return_value = SimpleNamespace(id='cs_concurrent', url='https://checkout.stripe.com/concurrent')
        retrieve.return_value.to_dict.return_value = {
            'status': 'open', 'payment_status': 'unpaid', 'url': 'https://checkout.stripe.com/concurrent',
        }
        data = {
            'email': 'guest@example.com', 'shipping_method': 'standard_uk',
            'phone_country': 'GB', 'phone': '7123456789',
            'billing_name': 'Guest', 'billing_house_flat': '10', 'billing_address1': 'High Street',
            'billing_city': 'London', 'billing_postcode': 'SW1A 1AA', 'billing_country': 'United Kingdom',
        }
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            client = Client()
            client.cookies[settings.SESSION_COOKIE_NAME] = cookie
            try:
                barrier.wait(timeout=10)
                response = client.post(reverse('checkout'), data)
                return response.status_code, response.get('Location')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda unused: submit(), range(2)))
        self.assertEqual(results, [(302, create.return_value.url)] * 2)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(CheckoutAttempt.objects.count(), 1)
        create.assert_called_once()

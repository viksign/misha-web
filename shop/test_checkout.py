from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .cart import Cart
from .models import CartItem, CustomerAddress, DeliveryOption, Order, OrderItem, Product


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STRIPE_SECRET_KEY='sk_test_checkout',
    STRIPE_CURRENCY='gbp',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class CheckoutPaymentTests(TestCase):
    def setUp(self):
        DeliveryOption.objects.get_or_create(
            code='standard_uk',
            defaults={'label': 'UK Standard', 'price': Decimal('3.99')},
        )
        self.product = Product.objects.create(
            name='Checkout pendant', slug='checkout-pendant', sku='CHECKOUT-001',
            price=Decimal('29.99'), stock_quantity=5,
        )
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 2}
        session['delivery_method'] = 'standard_uk'
        session.save()
        self.data = {
            'email': 'customer@example.com',
            'shipping_method': 'standard_uk',
            'billing_name': 'Test Customer',
            'billing_address1': '10 High Street',
            'billing_city': 'London',
            'billing_postcode': 'SW1A 1AA',
            'billing_country': 'United Kingdom',
        }

    @patch('shop.services.stripe.checkout.Session.create')
    def test_success_uses_dashboard_payment_methods_and_preserves_totals(self, create):
        create.return_value = SimpleNamespace(id='cs_test_checkout', url='https://checkout.stripe.com/test')
        response = self.client.post(reverse('checkout'), self.data)
        self.assertRedirects(response, create.return_value.url, fetch_redirect_response=False)
        parameters = create.call_args.kwargs
        self.assertNotIn('payment_method_types', parameters)
        self.assertEqual(parameters['mode'], 'payment')
        self.assertEqual(parameters['customer_email'], self.data['email'])
        self.assertEqual(parameters['line_items'][0]['quantity'], 2)
        self.assertEqual(parameters['line_items'][0]['price_data']['unit_amount'], 2999)
        self.assertEqual(parameters['line_items'][1]['price_data']['unit_amount'], 399)
        order = Order.objects.get()
        self.assertEqual(order.stripe_session_id, 'cs_test_checkout')
        self.assertEqual(order.total, Decimal('63.97'))
        self.assertEqual(order.payment_status, 'pending')
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {})
        self.assertNotIn('delivery_method', self.client.session)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    def assert_failed_checkout(self):
        with self.assertLogs('shop.views', level='ERROR'):
            response = self.client.post(reverse('checkout'), self.data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'We could not start your payment.')
        self.assertContains(response, '10 High Street')
        self.assertEqual(response.context['checkout_total'], Decimal('63.97'))
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderItem.objects.exists())
        self.assertEqual(self.client.session['delivery_method'], 'standard_uk')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    def test_stripe_failures_keep_guest_cart_and_roll_back_orders(self):
        for error in (
            stripe.InvalidRequestError('Unsupported parameter', param='payment_method_types'),
            stripe.APIConnectionError('Connection failed'),
            stripe.AuthenticationError('Invalid key'),
        ):
            with self.subTest(error=type(error).__name__):
                with patch('shop.services.stripe.checkout.Session.create', side_effect=error):
                    self.assert_failed_checkout()
                self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})

    @override_settings(STRIPE_SECRET_KEY='')
    @patch('shop.services.stripe.checkout.Session.create')
    def test_missing_configuration_keeps_cart(self, create):
        self.assert_failed_checkout()
        create.assert_not_called()
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})

    @patch('shop.services.stripe.checkout.Session.create', side_effect=stripe.APIConnectionError('Connection failed'))
    def test_failure_keeps_signed_in_cart_and_rolls_back_saved_address(self, create):
        user = User.objects.create_user(username='checkout-customer')
        self.client.force_login(user)
        self.assert_failed_checkout()
        self.assertEqual(CartItem.objects.get(user=user, product=self.product).quantity, 2)
        self.assertFalse(CustomerAddress.objects.filter(user=user).exists())

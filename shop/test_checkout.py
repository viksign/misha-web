from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .cart import Cart
from .forms import CheckoutForm
from .models import CartItem, CheckoutAttempt, CustomerAddress, CustomerProfile, DeliveryOption, DeliveryRegion, Order, OrderItem, Product, ProductOption
from .views import delivery_cost


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
            'phone_country': 'GB', 'phone': '7123456789',
            'shipping_method': 'standard_uk',
            'billing_name': 'Test Customer',
            'billing_house_flat': '10',
            'billing_address1': 'High Street',
            'billing_address2': 'Flat 2',
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
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})
        self.assertEqual(self.client.session['delivery_method'], 'standard_uk')
        self.assertEqual(parameters['idempotency_key'], f'checkout-order-{order.pk}')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    def assert_failed_checkout(self):
        with self.assertLogs('shop.views', level='ERROR'):
            response = self.client.post(reverse('checkout'), self.data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your bag has been kept.')
        self.assertContains(response, 'value="10"')
        self.assertContains(response, 'value="High Street"')
        self.assertEqual(response.context['checkout_total'], Decimal('63.97'))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertEqual(CheckoutAttempt.objects.get().order_id, Order.objects.get().pk)
        self.assertEqual(Order.objects.get().payment_status, 'pending')
        self.assertEqual(self.client.session['delivery_method'], 'standard_uk')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    @patch('shop.services.stripe.checkout.Session.create')
    def test_stripe_returns_to_same_https_host_and_preserves_browser_session(self, create):
        for host in ('mishaislandheritage.com', 'www.mishaislandheritage.com'):
            with self.subTest(host=host):
                client = Client()
                session = client.session
                session[Cart.SESSION_KEY] = {str(self.product.pk): 1}
                session.save()
                create.return_value = SimpleNamespace(id=f'cs_{host}', url='https://checkout.stripe.com/test')
                response = client.post(reverse('checkout'), self.data, secure=True, HTTP_HOST=host)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(create.call_args.kwargs['cancel_url'], f'https://{host}/checkout/?payment_cancelled=1')
                self.assertEqual(create.call_args.kwargs['success_url'], f'https://{host}/checkout/success/?session_id={{CHECKOUT_SESSION_ID}}')

    @patch('shop.services.stripe.checkout.Session.create')
    def test_new_billing_address_saves_house_flat_separately(self, create):
        create.return_value = SimpleNamespace(id='cs_test_address', url='https://checkout.stripe.com/test')
        user = User.objects.create_user(username='checkout-address-customer')
        self.client.force_login(user)

        response = self.client.post(reverse('checkout'), self.data)

        self.assertRedirects(response, create.return_value.url, fetch_redirect_response=False)
        order = Order.objects.get()
        self.assertEqual(order.billing_address1, '10 High Street')
        self.assertEqual(order.billing_address2, 'Flat 2')
        address = CustomerAddress.objects.get(user=user, address_type='billing')
        self.assertEqual(address.house_number, '10')
        self.assertEqual(address.street_name, 'High Street')
        self.assertEqual(address.address2, 'Flat 2')

    def test_stripe_failures_keep_guest_cart_and_reuse_persisted_order(self):
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
    def test_failure_keeps_signed_in_cart_and_saved_address_for_retry(self, create):
        user = User.objects.create_user(username='checkout-customer')
        self.client.force_login(user)
        self.assert_failed_checkout()
        self.assertEqual(CartItem.objects.get(user=user, product=self.product).quantity, 2)
        self.assertTrue(CustomerAddress.objects.filter(user=user, address_type='billing').exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class BagAndCheckoutLayoutTests(TestCase):
    def setUp(self):
        self.uk = DeliveryRegion.objects.get(name='UK')
        self.europe = DeliveryRegion.objects.get(name='Europe')
        DeliveryOption.objects.create(
            code='standard_uk', label='UK Standard', transit_time='3-5 business days',
            price=Decimal('3.99'), region=self.uk, sort_order=1,
        )
        DeliveryOption.objects.create(
            code='standard_eu', label='Europe Standard', transit_time='7-15 business days',
            price=Decimal('9.99'), region=self.europe, sort_order=2,
        )
        self.product = Product.objects.create(
            name='Layout pendant', slug='layout-pendant', sku='LAYOUT-001',
            price=Decimal('29.99'), stock_quantity=5,
        )
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 2}
        session.save()

    def test_bag_lists_items_without_a_delivery_picker(self):
        response = self.client.get(reverse('cart'))

        self.assertContains(response, 'Layout pendant')
        self.assertContains(response, 'Delivery is calculated at checkout')
        self.assertNotContains(response, 'delivery-picker')
        self.assertNotContains(response, 'name="shipping_method"')
        self.assertNotContains(response, 'Estimated total')
        self.assertNotContains(response, 'UK Standard')
        self.assertNotContains(response, '£3.99')

    def test_bag_shows_each_chosen_option_on_its_own_row(self):
        option = ProductOption.objects.create(
            product=self.product, kind='Chain length', label='55+5 cm', is_default=True,
        )
        session = self.client.session
        session[Cart.SESSION_KEY] = {}
        session.save()
        self.client.post(reverse('add_to_cart', args=[self.product.pk]), {'options': [option.pk], 'quantity': 1})

        response = self.client.get(reverse('cart'))

        self.assertContains(response, 'bag-row-option')
        self.assertContains(response, '<dt>Chain length</dt><dd>55+5 cm</dd>', html=True)

    def test_checkout_asks_for_email_then_country_then_delivery_method(self):
        response = self.client.get(reverse('checkout'))
        content = response.content.decode()

        self.assertLess(content.index('id="id_email"'), content.index('id="id_billing_country"'))
        self.assertLess(content.index('id="id_billing_house_flat"'), content.index('id="id_billing_address1"'))
        self.assertLess(content.index('id="id_billing_address1"'), content.index('id="id_billing_country"'))
        self.assertLess(content.index('id="id_billing_country"'), content.index('id="id_shipping_method"'))
        self.assertContains(response, 'Billing House or Number:')
        self.assertContains(response, 'Shipping House or Number:')
        self.assertContains(response, 'Billing Address Line 2')
        self.assertContains(response, 'class="checkout-required"')
        self.assertNotContains(response, '(mandatory)')
        self.assertNotContains(response, '(optional)')
        self.assertNotContains(response, 'House/Flat')
        self.assertContains(response, 'Choose your country or region above to see the delivery methods')
        self.assertContains(response, 'data-delivery-service')
        self.assertContains(response, 'data-delivery-transit')
        self.assertContains(response, 'Delivery costs and times vary by destination.')
        self.assertContains(response, 'Delivery costs and times vary by destination. Please refer to our')
        self.assertNotContains(response, 'International customs charges may apply.')
        self.assertNotContains(response, 'data-delivery-note')
        self.assertContains(response, f'href="{reverse("terms_and_conditions")}#international-shipping"')

    def test_checkout_server_validation_marks_missing_required_fields(self):
        response = self.client.post(reverse('checkout'), {
            'billing_country': 'United Kingdom',
            'shipping_method': 'standard_uk',
        })

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        for name in ('email', 'billing_name', 'billing_house_flat', 'billing_address1', 'billing_city', 'billing_postcode'):
            with self.subTest(field=name):
                self.assertIn(name, form.errors)
                self.assertIn('aria-invalid="true"', str(form[name]))
        self.assertNotIn('billing_address2', form.errors)
        self.assertNotIn('shipping_house_flat', form.errors)
        self.assertContains(response, 'This field is required.')

    def test_separate_shipping_requires_same_fields_except_address_line_two(self):
        form = CheckoutForm({
            'email': 'customer@example.com',
            'billing_house_flat': '10', 'billing_address1': 'High Street',
            'billing_city': 'London', 'billing_postcode': 'SW1A 1AA',
            'billing_country': 'United Kingdom',
            'ship_to_different_address': 'on',
            'shipping_country': 'United Kingdom',
            'shipping_method': 'standard_uk',
        }, user=AnonymousUser())

        self.assertFalse(form.is_valid())
        for name in ('shipping_name', 'shipping_house_flat', 'shipping_address1', 'shipping_city', 'shipping_postcode'):
            self.assertIn(name, form.errors)
        self.assertNotIn('shipping_address2', form.errors)
        self.assertEqual(form.fields['billing_address1'].widget.attrs['autocomplete'], 'section-billing billing address-line1')

    def test_saved_shipping_skips_new_shipping_fields_but_billing_is_always_required(self):
        user = User.objects.create_user(username='saved-address-customer')
        addresses = {}
        for address_type in ('billing', 'shipping'):
            addresses[address_type] = CustomerAddress.objects.create(
                user=user, address_type=address_type,
                house_number='10', street_name='High Street',
                city='London', postcode='SW1A 1AA', country='United Kingdom',
            )
        form = CheckoutForm({
            'email': 'customer@example.com',
            'billing_address': str(addresses['billing'].pk),
            'ship_to_different_address': 'on',
            'shipping_address': str(addresses['shipping'].pk),
            'shipping_method': 'standard_uk',
        }, user=user)

        self.assertFalse(form.is_valid())
        self.assertNotIn('billing_address', form.fields)
        for field in ('billing_name', 'billing_house_flat', 'billing_address1', 'billing_city', 'billing_postcode', 'billing_country'):
            self.assertIn(field, form.errors)
        self.assertFalse(form.fields['shipping_house_flat'].required)
        self.assertNotIn('shipping_name', form.errors)

    def test_guest_checkout_has_no_address_selectors_or_save_controls(self):
        response = self.client.get(reverse('checkout'))
        self.assertNotContains(response, 'id="id_billing_address"')
        self.assertNotContains(response, 'id="id_shipping_address"')
        self.assertNotContains(response, 'id="id_save_shipping_address"')
        self.assertTrue(response.context['form'].fields['billing_name'].required)

    def test_saved_shipping_controls_country_and_rejects_other_customer_addresses(self):
        user = User.objects.create_user(username='saved-shipping-customer')
        address = CustomerAddress.objects.create(
            user=user, address_type='shipping', house_number='25', street_name='Rue Example',
            city='Paris', postcode='75001', country='France',
        )
        data = {
            'email': 'customer@example.com', 'billing_name': 'Customer',
            'phone_country': 'GB', 'phone': '7123456789',
            'billing_house_flat': '10', 'billing_address1': 'High Street',
            'billing_city': 'London', 'billing_postcode': 'SW1A 1AA',
            'billing_country': 'United Kingdom', 'ship_to_different_address': 'on',
            'shipping_address': str(address.pk), 'shipping_method': 'standard_eu',
        }
        form = CheckoutForm(data, user=user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual([code for code, _ in form.fields['shipping_method'].choices], ['standard_eu'])
        for selected in (str(address.pk), 'invalid-address'):
            with self.subTest(selected=selected):
                other = User.objects.create_user(username=f'other-address-owner-{selected}')
                form = CheckoutForm({**data, 'shipping_address': selected}, user=other)
                self.assertFalse(form.is_valid())
                self.assertIn('shipping_address', form.errors)
                self.assertIn('shipping_name', form.errors)

    def test_registered_checkout_prefills_profile_house_and_street_separately(self):
        user = User.objects.create_user(
            username='prefilled-customer', email='prefill@example.com',
            first_name='Test', last_name='Customer',
        )
        CustomerProfile.objects.create(
            user=user, house_number='12', street_name='Example Street',
            city='London', postcode='SW1A 1AA', country='United Kingdom',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('checkout'))
        form = response.context['form']
        self.assertEqual(form['email'].value(), user.email)
        self.assertEqual(form['billing_name'].value(), 'Test Customer')
        self.assertEqual(form['billing_house_flat'].value(), '12')
        self.assertEqual(form['billing_address1'].value(), 'Example Street')
        self.assertNotContains(response, 'id="id_billing_address"')
        self.assertContains(response, 'id="id_shipping_address"')
        self.assertContains(response, 'Edit your account details')
        self.assertContains(response, 'Edit or remove saved shipping addresses')
        self.assertFalse(CustomerAddress.objects.filter(user=user).exists())

    def test_saved_billing_prefills_checkout_and_account_edits_update_it(self):
        user = User.objects.create_user(
            username='billing-customer', email='billing@example.com',
            first_name='Billing', last_name='Customer',
        )
        billing = CustomerAddress.objects.create(
            user=user, address_type='billing', house_number='14', street_name='Saved Street',
            address2='Suite 2', city='London', postcode='SW1A 1AA', country='United Kingdom',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('checkout'))
        self.assertEqual(response.context['form']['billing_house_flat'].value(), '14')
        self.assertEqual(response.context['form']['billing_address2'].value(), 'Suite 2')
        edit = self.client.get(reverse('account'), {'edit': '1'})
        self.assertEqual(edit.context['profile_form']['house_number'].value(), '14')
        response = self.client.post(reverse('account'), {
            'first_name': 'Updated', 'last_name': 'Customer', 'email': user.email,
            'house_number': '20', 'street_name': 'New Street', 'address': 'Suite 3',
            'city': 'London', 'postcode': 'N1 1AA', 'country': 'United Kingdom',
        })
        self.assertRedirects(response, reverse('account'))
        billing.refresh_from_db()
        self.assertEqual(billing.house_number, '20')
        self.assertEqual(billing.address2, 'Suite 3')
        checkout = self.client.get(reverse('checkout'))
        self.assertEqual(checkout.context['form']['billing_name'].value(), 'Updated Customer')
        self.assertEqual(checkout.context['form']['billing_house_flat'].value(), '20')
        self.assertEqual(checkout.context['form']['billing_address2'].value(), 'Suite 3')

    def test_registered_customer_without_profile_name_reuses_previous_billing_name(self):
        user = User.objects.create_user(username='email-only-customer', email='email-only@example.com')
        Order.objects.create(user=user, email=user.email, billing_name='Previous Billing Name')
        self.client.force_login(user)
        response = self.client.get(reverse('checkout'))
        self.assertEqual(response.context['form']['billing_name'].value(), 'Previous Billing Name')
        self.assertEqual(response.context['form']['shipping_name'].value(), 'Previous Billing Name')

    def test_shipping_address_edit_delete_and_owner_protection(self):
        user = User.objects.create_user(username='address-owner')
        address = CustomerAddress.objects.create(
            user=user, address_type='shipping', house_number='10', street_name='High Street',
            city='London', postcode='SW1A 1AA', country='United Kingdom',
        )
        url = reverse('account_shipping_address', args=[address.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(User.objects.create_user(username='not-address-owner'))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'action': 'delete'}).status_code, 404)
        self.client.force_login(user)
        self.assertEqual(self.client.get(url + '#remove-address').status_code, 200)
        self.assertTrue(CustomerAddress.objects.filter(pk=address.pk).exists())
        edited = {
            'label': 'Home', 'house_number': '22', 'street_name': 'New Street',
            'address2': 'Suite 4', 'city': 'Paris', 'postcode': '75001', 'country': 'France',
        }
        response = self.client.post(url, edited)
        self.assertEqual(response.status_code, 302)
        address.refresh_from_db()
        self.assertEqual(address.country, 'France')
        self.assertEqual(address.address2, 'Suite 4')
        self.assertContains(self.client.get(reverse('account')), 'Saved shipping addresses')
        self.assertEqual(self.client.post(url, {'action': 'delete'}).status_code, 302)
        self.assertFalse(CustomerAddress.objects.filter(pk=address.pk).exists())
        self.client.get(reverse('checkout'))
        self.assertFalse(CustomerAddress.objects.filter(user=user, address_type='shipping').exists())

    def test_checkout_charges_the_delivery_fee_for_the_destination_group(self):
        form = CheckoutForm({
            'email': 'customer@example.com',
            'phone_country': 'FR', 'phone': '612345678',
            'shipping_method': 'standard_eu',
            'billing_name': 'Test Customer',
            'billing_house_flat': '25',
            'billing_address1': 'Rue Example',
            'billing_city': 'Paris',
            'billing_postcode': '75001',
            'billing_country': 'France',
        }, user=AnonymousUser())

        self.assertEqual([code for code, _ in form.fields['shipping_method'].choices], ['standard_eu'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(delivery_cost('standard_eu', Decimal('59.98'), 'France'), Decimal('9.99'))

    def test_checkout_rejects_a_method_that_is_not_offered_for_the_destination(self):
        form = CheckoutForm({
            'email': 'customer@example.com',
            'shipping_method': 'standard_uk',
            'billing_name': 'Test Customer',
            'billing_house_flat': '25',
            'billing_address1': 'Rue Example',
            'billing_city': 'Paris',
            'billing_postcode': '75001',
            'billing_country': 'France',
        }, user=AnonymousUser())

        self.assertFalse(form.is_valid())
        self.assertIn('shipping_method', form.errors)

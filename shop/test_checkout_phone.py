from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import phonenumbers
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .cart import Cart
from .forms import CheckoutForm, COUNTRIES
from .models import CustomerProfile, DeliveryOption, Order, Product
from .phone import phone_countries, validate_checkout_phone
from .services import build_order_pdf


@override_settings(
    SECURE_SSL_REDIRECT=False, STRIPE_SECRET_KEY='sk_test_phone',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class CheckoutPhoneTests(TestCase):
    def setUp(self):
        DeliveryOption.objects.get_or_create(code='standard_uk', defaults={'label': 'UK Standard', 'price': Decimal('3.99')})
        self.product = Product.objects.create(name='Phone pendant', slug='phone-pendant', sku='PHONE-001', price='29.99', stock_quantity=10)
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 1}
        session.save()
        self.data = {
            'email': 'customer@example.com',
            'phone_country': 'GB', 'phone': '7123456789',
            'shipping_method': 'standard_uk',
            'billing_name': 'Customer', 'billing_house_flat': '10',
            'billing_address1': 'High Street', 'billing_city': 'London',
            'billing_postcode': 'SW1A 1AA', 'billing_country': 'United Kingdom',
        }

    def test_country_metadata_covers_every_checkout_country(self):
        self.assertEqual(set(phone_countries()), set(COUNTRIES))
        self.assertEqual(phone_countries()['United Kingdom']['calling_code'], '+44')
        self.assertEqual(phone_countries()['Mauritius']['calling_code'], '+230')
        for country, data in phone_countries().items():
            with self.subTest(country=country):
                if data['example']:
                    value = validate_checkout_phone(data['region'], data['example'])
                    self.assertTrue(value.startswith(data['calling_code']))
                    self.assertLessEqual(len(value), 16)

    def test_phone_is_mandatory_and_invalid_checkout_keeps_bag(self):
        for field in ['phone', 'phone_country']:
            with self.subTest(field=field):
                data = dict(self.data)
                data.pop(field)
                with patch('shop.views.create_stripe_checkout') as create:
                    response = self.client.post(reverse('checkout'), data)
                self.assertContains(response, 'This field is required.')
                self.assertIn(field, response.context['form'].errors)
                self.assertFalse(Order.objects.exists())
                self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 1})
                create.assert_not_called()

    def test_valid_phone_is_compressed_to_e164(self):
        for region, national, expected in [
            ('GB', '7123456789', '+447123456789'),
            ('GB', '7123 456-789', '+447123456789'),
            ('FR', '612345678', '+33612345678'),
            ('MU', '52512345', '+23052512345'),
            ('US', '2025550123', '+12025550123'),
            ('IT', '0212345678', '+390212345678'),
        ]:
            with self.subTest(region=region):
                form = CheckoutForm({**self.data, 'phone_country': region, 'phone': national}, user=AnonymousUser())
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['phone'], expected)

    def test_invalid_numbers_and_domestic_prefix_are_rejected(self):
        for region, national in [
            ('GB', '07123456789'), ('GB', '447123456789'),
            ('GB', '123'), ('GB', '712345678901234567'),
            ('GB', '+447123456789'), ('GB', '7123456789 ext 2'),
            ('GB', 'abcdef'), ('GB', '１２３４５６７８９'),
            ('FR', '7123456789'), ('US', '1111111111'),
        ]:
            with self.subTest(region=region, national=national):
                form = CheckoutForm({**self.data, 'phone_country': region, 'phone': national}, user=AnonymousUser())
                self.assertFalse(form.is_valid())
                self.assertIn('phone', form.errors)
        form = CheckoutForm({**self.data, 'phone': '07123456789'}, user=AnonymousUser())
        self.assertFalse(form.is_valid())
        self.assertIn('7123456789 after +44', str(form.errors['phone']))

    def test_billing_country_preselects_phone_region_without_javascript(self):
        for country, region in [('United Kingdom', 'GB'), ('Mauritius', 'MU'), ('France', 'FR')]:
            form = CheckoutForm(initial={'billing_country': country}, user=AnonymousUser())
            self.assertEqual(form['phone_country'].value(), region)
        form = CheckoutForm(self.data, user=AnonymousUser())
        self.assertEqual(form['phone_country'].value(), 'GB')

    def test_saved_phone_decompresses_for_profile_and_retry(self):
        form = CheckoutForm(initial={'billing_country': 'France', 'phone': '+447123456789'}, user=AnonymousUser())
        self.assertEqual(form['phone_country'].value(), 'GB')
        self.assertEqual(form['phone'].value(), '7123456789')

    @patch('shop.services.stripe.checkout.Session.create')
    def test_phone_is_saved_on_order_and_profile_and_retained_after_cancel(self, create):
        create.return_value = SimpleNamespace(id='cs_phone', url='https://checkout.stripe.com/test')
        user = User.objects.create_user(username='phone-customer')
        self.client.force_login(user)
        response = self.client.post(reverse('checkout'), self.data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.phone, '+447123456789')
        self.assertEqual(CustomerProfile.objects.get(user=user).phone, order.phone)
        response = self.client.get(reverse('checkout'), {'payment_cancelled': '1'})
        self.assertEqual(response.context['form']['phone'].value(), '7123456789')
        self.assertEqual(response.context['form']['phone_country'].value(), 'GB')
        for template in ['shop/order_confirmation_email.html', 'shop/order_confirmation_email.txt']:
            self.assertIn(order.phone, render_to_string(template, {'order': order}))
        self.assertTrue(build_order_pdf(order).startswith(b'%PDF'))

    def test_phone_fields_and_country_metadata_are_in_checkout(self):
        response = self.client.get(reverse('checkout'))
        self.assertContains(response, 'id="id_phone_country"')
        self.assertContains(response, 'id="id_phone"')
        self.assertContains(response, 'autocomplete="tel-national"')
        self.assertContains(response, 'placeholder="Enter your phone number without the leading 0"')
        self.assertContains(response, 'id="phone-countries"')
        self.assertTrue(response.context['form'].fields['phone'].required)
        self.assertTrue(response.context['form'].fields['phone_country'].required)
        self.assertContains(response, 'aria-label="Phone number format help"')
        self.assertContains(response, 'class="checkout-phone-help" role="tooltip"')
        self.assertNotContains(response, 'data-phone-error')

    def test_phone_instructions_are_shown_after_validation_error(self):
        response = self.client.post(reverse('checkout'), {**self.data, 'phone': '07123456789'})
        self.assertIn('phone', response.context['form'].errors)
        self.assertContains(response, 'class="checkout-phone-heading" data-phone-error')
        self.assertContains(response, 'For the UK: +44 7XXXXXXXXX, without the leading 0.')

    def test_legacy_orders_may_keep_phone_blank_but_non_e164_values_are_invalid(self):
        order = Order(email='legacy@example.com', shipping_name='Customer')
        order.full_clean(exclude=['shipping_address1', 'shipping_city', 'shipping_postcode'])
        order.phone = '07123456789'
        with self.assertRaises(ValidationError):
            order.full_clean(exclude=['shipping_address1', 'shipping_city', 'shipping_postcode'])

    @patch('shop.checkout_recovery.stripe.checkout.Session.expire')
    @patch('shop.checkout_recovery.stripe.checkout.Session.retrieve')
    @patch('shop.services.stripe.checkout.Session.create')
    def test_changed_phone_safely_replaces_old_unpaid_checkout(self, create, retrieve, expire):
        create.side_effect = [
            SimpleNamespace(id='cs_phone_old', url='https://checkout.stripe.com/old'),
            SimpleNamespace(id='cs_phone_new', url='https://checkout.stripe.com/new'),
        ]
        self.client.post(reverse('checkout'), self.data)
        old = Order.objects.get()
        retrieve.return_value.to_dict.return_value = {'status': 'open', 'payment_status': 'unpaid'}
        expire.return_value.to_dict.return_value = {'status': 'expired'}
        response = self.client.post(reverse('checkout'), {**self.data, 'phone': '7400123456'})
        self.assertEqual(response.status_code, 302)
        expire.assert_called_once_with('cs_phone_old')
        old.refresh_from_db()
        self.assertEqual(old.payment_status, 'cancelled')
        self.assertEqual(Order.objects.get(stripe_session_id='cs_phone_new').phone, '+447400123456')

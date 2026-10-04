from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase

from .forms import CheckoutForm, get_delivery_options_for_country
from .models import DeliveryOption, ShippingPackaging, ShippingRate
from .shipping import matching_shipping_rate, parcel_metrics
from .views import checkout_destination_country, delivery_cost


def boxed_product(weight=150, length='10', width='5', height='2'):
    return SimpleNamespace(
        shipping_box_weight_grams=weight,
        shipping_box_length_cm=Decimal(length) if length is not None else None,
        shipping_box_width_cm=Decimal(width) if width is not None else None,
        shipping_box_height_cm=Decimal(height) if height is not None else None,
    )


class ParcelMetricsTests(SimpleTestCase):
    def test_sums_box_weight_and_stacks_box_dimensions(self):
        packaging = SimpleNamespace(
            packaging_weight_grams=100,
            extra_length_cm=Decimal('1'),
            extra_width_cm=Decimal('1'),
            extra_height_cm=Decimal('1'),
        )

        parcel = parcel_metrics([{'product': boxed_product(), 'quantity': 2}], packaging)

        self.assertEqual(parcel, {
            'weight_grams': 400,
            'length_cm': Decimal('11'),
            'width_cm': Decimal('6'),
            'height_cm': Decimal('5'),
        })

    def test_missing_boxed_dimensions_disables_parcel_rate(self):
        packaging = SimpleNamespace(
            packaging_weight_grams=100,
            extra_length_cm=Decimal('0'),
            extra_width_cm=Decimal('0'),
            extra_height_cm=Decimal('0'),
        )

        self.assertIsNone(parcel_metrics([{'product': boxed_product(height=None), 'quantity': 1}], packaging))


class ShippingRateTests(TestCase):
    def setUp(self):
        self.service = DeliveryOption.objects.create(
            code='standard_uk',
            label='UK Standard',
            price=Decimal('4.95'),
            sort_order=1,
        )
        ShippingPackaging.objects.create(
            packaging_weight_grams=100,
            extra_length_cm=Decimal('1'),
            extra_width_cm=Decimal('1'),
            extra_height_cm=Decimal('1'),
        )

    def test_selects_rate_that_fits_country_service_weight_and_size(self):
        rate = ShippingRate.objects.create(
            country='United Kingdom',
            service=self.service,
            max_weight_grams=500,
            max_length_cm=Decimal('12'),
            max_width_cm=Decimal('7'),
            max_height_cm=Decimal('6'),
            price=Decimal('6.25'),
        )

        matched, parcel = matching_shipping_rate(
            [{'product': boxed_product(), 'quantity': 2}],
            'united kingdom',
            'standard_uk',
        )

        self.assertEqual(matched, rate)
        self.assertEqual(parcel['weight_grams'], 400)

    def test_does_not_select_rate_when_parcel_exceeds_dimensions(self):
        ShippingRate.objects.create(
            country='United Kingdom',
            service=self.service,
            max_weight_grams=500,
            max_length_cm=Decimal('10'),
            max_width_cm=Decimal('7'),
            max_height_cm=Decimal('6'),
            price=Decimal('6.25'),
        )

        matched, _ = matching_shipping_rate(
            [{'product': boxed_product(), 'quantity': 2}],
            'United Kingdom',
            'standard_uk',
        )

        self.assertIsNone(matched)


class CheckoutAddressTests(TestCase):
    def setUp(self):
        DeliveryOption.objects.get_or_create(
            code='standard_uk',
            defaults={'label': 'UK Standard', 'price': Decimal('4.95')},
        )
        DeliveryOption.objects.get_or_create(
            code='international',
            defaults={'label': 'International Tracked', 'price': Decimal('24.95')},
        )

    def billing_data(self):
        return {
            'email': 'customer@example.com',
            'shipping_method': 'standard_uk',
            'billing_name': 'Test Customer',
            'billing_address1': '10 High Street',
            'billing_city': 'London',
            'billing_postcode': 'SW1A 1AA',
            'billing_country': 'United Kingdom',
        }

    def test_billing_address_is_enough_when_shipping_is_not_different(self):
        form = CheckoutForm(self.billing_data(), user=AnonymousUser())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data['ship_to_different_address'])
        self.assertEqual(checkout_destination_country(form, AnonymousUser()), 'United Kingdom')

    def test_different_shipping_requires_shipping_fields(self):
        data = self.billing_data()
        data['ship_to_different_address'] = 'on'
        form = CheckoutForm(data, user=AnonymousUser())

        self.assertFalse(form.is_valid())
        self.assertIn('shipping_address1', form.errors)

    def test_delivery_methods_are_restricted_to_destination_rates(self):
        international = DeliveryOption.objects.get(code='international')
        ShippingRate.objects.create(
            country='France',
            service=international,
            max_weight_grams=500,
            max_length_cm=Decimal('20'),
            max_width_cm=Decimal('10'),
            max_height_cm=Decimal('8'),
            price=Decimal('12.50'),
        )
        data = self.billing_data()
        data.update({
            'ship_to_different_address': 'on',
            'shipping_method': 'international',
            'shipping_name': 'Test Customer',
            'shipping_address1': '25 Rue Example',
            'shipping_city': 'Paris',
            'shipping_postcode': '75001',
            'shipping_country': 'France',
        })

        form = CheckoutForm(data, user=AnonymousUser())

        self.assertEqual([code for code, _ in form.fields['shipping_method'].choices], ['international'])
        self.assertEqual(form.fields['shipping_method'].choices[0][1], 'Europe Standard Delivery (Royal Mail)')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(checkout_destination_country(form, AnonymousUser()), 'France')

    def test_standard_shipping_prices_match_destination_tiers(self):
        self.assertEqual(delivery_cost('standard_uk', Decimal('0'), 'United Kingdom'), Decimal('3.99'))
        self.assertEqual(delivery_cost('international', Decimal('0'), 'United States'), Decimal('11.99'))
        self.assertEqual(delivery_cost('international', Decimal('0'), 'France'), Decimal('9.99'))
        self.assertEqual(delivery_cost('international', Decimal('0'), 'Canada'), Decimal('11.99'))

    def test_checkout_offers_destination_specific_standard_service(self):
        for country, expected_label in (
            ('United States', 'USA Standard Delivery (Royal Mail)'),
            ('France', 'Europe Standard Delivery (Royal Mail)'),
            ('Canada', 'Canada & International Standard Delivery (Royal Mail)'),
        ):
            choices = get_delivery_options_for_country(country)
            self.assertEqual([code for code, _ in choices], ['international'])
            self.assertEqual(choices[0][1], expected_label)

        uk_choices = get_delivery_options_for_country('United Kingdom')
        self.assertEqual([code for code, _ in uk_choices], ['standard_uk', 'express_uk'])
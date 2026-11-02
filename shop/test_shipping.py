from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase

from .forms import CheckoutForm, get_delivery_options_for_country
from .models import DeliveryOption, DeliveryRegion, ShippingPackaging, ShippingRate
from .shipping import (
    checkout_shipping_configuration,
    delivery_region_for_country,
    matching_shipping_rate,
    parcel_metrics,
    shipping_delivery_summary,
)
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
            'phone_country': 'GB', 'phone': '7123456789',
            'shipping_method': 'standard_uk',
            'billing_name': 'Test Customer',
            'billing_house_flat': '10',
            'billing_address1': 'High Street',
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
        self.assertIn('shipping_house_flat', form.errors)
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
            'shipping_house_flat': '25',
            'shipping_address1': 'Rue Example',
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

class DeliveryRegionGroupingTests(TestCase):
    def setUp(self):
        self.uk = DeliveryRegion.objects.get(name='UK')
        self.europe = DeliveryRegion.objects.get(name='Europe')
        self.international = DeliveryRegion.objects.get(name='International')
        self.uk_standard = DeliveryOption.objects.create(
            code='standard_uk', label='UK Standard', transit_time='3-5 business days',
            price=Decimal('3.99'), region=self.uk, sort_order=1,
        )
        self.uk_express = DeliveryOption.objects.create(
            code='express_uk', label='UK Express', transit_time='1-2 business days',
            price=Decimal('9.95'), region=self.uk, sort_order=2,
        )
        self.eu_standard = DeliveryOption.objects.create(
            code='standard_eu', label='Europe Standard', transit_time='7-15 business days',
            price=Decimal('9.99'), region=self.europe, sort_order=3,
        )
        self.worldwide = DeliveryOption.objects.create(
            code='international', label='International Standard', transit_time='10-25 business days',
            price=Decimal('11.99'), region=self.international, sort_order=4,
        )

    def test_default_groups_are_seeded_with_a_single_catch_all(self):
        self.assertEqual(
            [region.name for region in DeliveryRegion.objects.all()],
            ['UK', 'USA', 'Europe', 'International'],
        )
        self.assertEqual([region.name for region in DeliveryRegion.objects.filter(is_default=True)], ['International'])
        self.assertIn('United Kingdom', self.uk.country_list())
        self.assertIn('France', self.europe.country_list())

    def test_country_resolves_to_its_group_and_unlisted_countries_use_the_catch_all(self):
        self.assertEqual(delivery_region_for_country('United Kingdom'), self.uk)
        self.assertEqual(delivery_region_for_country('france'), self.europe)
        self.assertEqual(delivery_region_for_country('United States'), DeliveryRegion.objects.get(name='USA'))
        self.assertEqual(delivery_region_for_country('Sri Lanka'), self.international)
        self.assertIsNone(delivery_region_for_country(''))

    def test_checkout_offers_only_the_methods_grouped_for_the_destination(self):
        self.assertEqual(
            [code for code, _ in get_delivery_options_for_country('United Kingdom')],
            ['standard_uk', 'express_uk'],
        )
        self.assertEqual([code for code, _ in get_delivery_options_for_country('France')], ['standard_eu'])
        self.assertEqual([code for code, _ in get_delivery_options_for_country('Sri Lanka')], ['international'])

    def test_grouped_methods_are_priced_from_their_own_record(self):
        self.assertEqual(delivery_cost('standard_uk', Decimal('0'), 'United Kingdom'), Decimal('3.99'))
        self.assertEqual(delivery_cost('standard_eu', Decimal('0'), 'France'), Decimal('9.99'))
        self.assertEqual(delivery_cost('international', Decimal('0'), 'Sri Lanka'), Decimal('11.99'))

    def test_free_over_threshold_still_applies_to_grouped_methods(self):
        self.uk_standard.free_over = Decimal('100.00')
        self.uk_standard.save(update_fields=['free_over'])

        self.assertEqual(delivery_cost('standard_uk', Decimal('150.00'), 'United Kingdom'), Decimal('0.00'))

    def test_hidden_methods_are_not_offered(self):
        self.uk_express.active = False
        self.uk_express.save(update_fields=['active'])

        self.assertEqual(
            [code for code, _ in get_delivery_options_for_country('United Kingdom')],
            ['standard_uk'],
        )

    def test_checkout_configuration_exposes_groups_for_the_browser(self):
        config = checkout_shipping_configuration()
        groups = {region['name']: region for region in config['regions']}

        self.assertEqual([service['code'] for service in groups['Europe']['services']], ['standard_eu'])
        self.assertEqual(groups['Europe']['services'][0]['transit'], '7-15 business days')
        self.assertTrue(groups['International']['is_default'])

    def test_shipping_summary_prefers_configured_groups(self):
        summary = shipping_delivery_summary()

        self.assertEqual(
            [(row['label'], row['transit'], row['price']) for row in summary],
            [
                ('UK Standard', '3-5 business days', Decimal('3.99')),
                ('UK Express', '1-2 business days', Decimal('9.95')),
                ('Europe Standard', '7-15 business days', Decimal('9.99')),
                ('International Standard', '10-25 business days', Decimal('11.99')),
            ],
        )

    def test_parcel_rate_bands_still_override_the_group_price(self):
        ShippingPackaging.objects.create(
            packaging_weight_grams=0,
            extra_length_cm=Decimal('0'), extra_width_cm=Decimal('0'), extra_height_cm=Decimal('0'),
        )
        ShippingRate.objects.create(
            country='France', service=self.eu_standard, max_weight_grams=500,
            max_length_cm=Decimal('30'), max_width_cm=Decimal('20'), max_height_cm=Decimal('10'),
            price=Decimal('7.50'),
        )
        items = [{'product': boxed_product(), 'quantity': 1}]

        self.assertEqual(delivery_cost('standard_eu', Decimal('0'), 'France', items), Decimal('7.50'))


class UngroupedDeliveryFallbackTests(TestCase):
    def setUp(self):
        DeliveryOption.objects.create(code='standard_uk', label='UK Standard', price=Decimal('3.99'), sort_order=1)
        DeliveryOption.objects.create(code='express_uk', label='UK Express', price=Decimal('9.95'), sort_order=2)
        DeliveryOption.objects.create(code='international', label='International', price=Decimal('11.99'), sort_order=3)

    def test_methods_without_a_group_keep_the_existing_destination_tiers(self):
        self.assertEqual(delivery_cost('international', Decimal('0'), 'France'), Decimal('9.99'))
        self.assertEqual([code for code, _ in get_delivery_options_for_country('France')], ['international'])

    def test_summary_falls_back_to_the_standard_tiers(self):
        summary = shipping_delivery_summary()

        self.assertEqual(
            [row['label'] for row in summary],
            [
                'UK Standard Delivery',
                'USA Standard Delivery (Royal Mail)',
                'Europe Standard Delivery (Royal Mail)',
                'Canada & International Standard Delivery (Royal Mail)',
            ],
        )

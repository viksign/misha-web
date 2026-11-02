from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import render_to_string
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .cart import Cart
from .models import CartItem, Order, Product, ProductOption
from .services import build_order_pdf


@override_settings(SECURE_SSL_REDIRECT=False, STAFF_MFA_REQUIRED=False)
class ProductOptionTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name='Option pendant', slug='option-pendant', sku='OPTION-001',
            description='A pendant with choices.', price='30.00', stock_quantity=10,
            chain_style='Cable', colour='Gold', dimensions='20 x 15 mm',
        )
        self.short = ProductOption.objects.create(
            product=self.product, kind='Chain length', label='16 inches', description='A classic drop that sits close to the collar.', additional_cost='0.00', is_default=True,
        )
        self.long = ProductOption.objects.create(
            product=self.product, kind='Chain length', label='20 inches', description='A longer drop that falls beautifully and suits a taller frame.', additional_cost='5.25',
        )
        self.small = ProductOption.objects.create(
            product=self.product, kind='Size', label='Small', description='Best for a more snug fit around a smaller neck.', additional_cost='0.00', is_default=True,
        )
        self.large = ProductOption.objects.create(
            product=self.product, kind='Size', label='Large', description='Best for gentlemen or anyone looking for a more generous drop and a looser fit around a larger neck.', additional_cost='3.50',
        )

    def add(self, chain_length=None, size=None, quantity=1):
        return self.client.post(reverse('add_to_cart', args=[self.product.pk]), {
            'options': [
                self.long.pk if chain_length is None else chain_length,
                self.large.pk if size is None else size,
            ],
            'quantity': quantity, 'next': reverse('cart'),
            'unit_price': '0.01', 'additional_cost': '-1000',
        })

    def test_detail_has_dropdowns_and_new_details(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, '<select name="options" required data-product-option>', count=2, html=False)
        self.assertContains(response, '<span>Chain length</span>', html=False)
        self.assertContains(response, '<span>Size</span>', html=False)
        self.assertContains(response, 'Choose your quantity first, then select the available length or size.')
        content = response.content.decode()
        self.assertLess(content.index('name="quantity"'), content.index('name="options"'))
        self.assertLess(content.index('name="options"'), content.index('Add to bag</button>'))
        self.assertContains(response, f'<option value="{self.short.pk}" data-additional-cost="0.00" data-option-description="A classic drop that sits close to the collar." data-option-kind="Chain length" selected>16 inches</option>', html=True)
        self.assertContains(response, f'<option value="{self.long.pk}" data-additional-cost="5.25" data-option-description="A longer drop that falls beautifully and suits a taller frame." data-option-kind="Chain length">20 inches — +£5.25</option>', html=True)
        self.assertContains(response, f'<option value="{self.small.pk}" data-additional-cost="0.00" data-option-description="Best for a more snug fit around a smaller neck." data-option-kind="Size" selected>Small</option>', html=True)
        self.assertContains(response, 'Large — +£3.50')
        self.assertContains(response, 'Best for gentlemen or anyone looking for a more generous drop and a looser fit around a larger neck.')
        self.assertNotContains(response, 'Choose chain length')
        self.assertContains(response, 'data-base-price="30.00"', html=False)
        self.assertContains(response, '<dt>Chain Style</dt><dd>Cable</dd>', html=True)
        self.assertContains(response, '<dt>Colour</dt><dd>Gold</dd>', html=True)
        self.assertContains(response, 'Pendant Dimensions')
        self.assertContains(response, 'Shipping &amp; Delivery')
        self.assertContains(response, 'UK Standard Delivery (3-5 business days)')
        self.assertNotContains(response, 'compare_at_price')

    def test_zero_cost_alternatives_have_no_included_suffix(self):
        for option in (self.long, self.large):
            option.additional_cost = Decimal('0.00')
            option.save(update_fields=['additional_cost'])
        response = self.client.get(self.product.get_absolute_url())
        self.assertNotContains(response, ' — Included')
        self.assertContains(response, 'data-option-kind="Chain length">20 inches</option>')
        self.assertContains(response, 'data-option-kind="Size">Large</option>')
        self.assertContains(response, 'data-option-kind="Chain length" selected>16 inches</option>')
        self.assertContains(response, 'data-option-kind="Size" selected>Small</option>')

    def test_any_option_type_is_offered_and_priced(self):
        gold = ProductOption.objects.create(product=self.product, kind='Colour', label='Gold', is_default=True)
        rose = ProductOption.objects.create(product=self.product, kind='Colour', label='Rose gold', additional_cost='2.00')
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, '<select name="options" required data-product-option>', count=3, html=False)
        self.assertContains(response, '<span>Colour</span>', html=False)
        self.assertContains(response, f'<option value="{gold.pk}" data-additional-cost="0.00" data-option-description="" data-option-kind="Colour" selected>Gold — Included</option>', html=True)
        self.assertContains(response, 'Rose gold — +£2.00')
        self.client.post(reverse('add_to_cart', args=[self.product.pk]), {
            'options': [self.long.pk, self.small.pk, rose.pk], 'quantity': 1,
        })
        row = self.client.get(reverse('cart')).context['items'][0]
        self.assertEqual(row['unit_price'], Decimal('37.25'))
        self.assertEqual([option.kind for option in row['options']], ['Chain length', 'Size', 'Colour'])
        self.add(quantity=1)
        self.assertContains(self.client.get(self.product.get_absolute_url()), 'Please choose an available colour')

    def test_legacy_session_keys_still_load(self):
        session = self.client.session
        session[Cart.SESSION_KEY] = {f'{self.product.pk}:{self.long.pk}:{self.large.pk}': 2}
        session.save()
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['cart'].subtotal, Decimal('77.50'))
        self.assertEqual(response.context['items'][0]['key'], Cart.key(self.product, [self.long.pk, self.large.pk]))

    def test_store_assistant_uses_new_product_details_and_option_costs(self):
        response = self.client.post(reverse('store_assistant'), json.dumps({
            'message': 'What is the price of Option pendant?',
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        reply = response.json()['reply']
        self.assertIn('chain length: 20 inches (+£5.25)', reply)
        self.assertIn('size: Large (+£3.50)', reply)
        self.assertIn('chain style: Cable', reply)
        self.assertIn('colour: Gold', reply)
        self.assertIn('pendant dimensions: 20 x 15 mm', reply)

    def test_server_calculates_price_and_keeps_selections_in_guest_bag(self):
        self.add(quantity=2)
        response = self.client.get(reverse('cart'))
        row = response.context['items'][0]
        self.assertEqual(row['unit_price'], Decimal('38.75'))
        self.assertEqual(row['total'], Decimal('77.50'))
        self.assertEqual(response.context['cart'].subtotal, Decimal('77.50'))
        self.assertContains(response, 'Chain length: 20 inches')
        self.assertContains(response, 'Size: Large')
        self.assertContains(response, '£38.75 each')
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {
            Cart.key(self.product, [self.long.pk, self.large.pk]): 2,
        })

    def test_missing_wrong_kind_foreign_and_invalid_choices_are_rejected(self):
        other = Product.objects.create(name='Other', slug='other', sku='OTHER', price='1.00')
        foreign = ProductOption.objects.create(product=other, kind='Chain length', label='Foreign')
        for chain_length, size in [
            ('', self.large.pk), (self.long.pk, ''), (foreign.pk, self.large.pk),
            (self.long.pk, self.short.pk), (self.large.pk, self.small.pk),
        ]:
            with self.subTest(chain_length=chain_length, size=size):
                response = self.add(chain_length, size)
                self.assertRedirects(response, self.product.get_absolute_url(), fetch_redirect_response=False)
                self.assertEqual(self.client.session.get(Cart.SESSION_KEY, {}), {})
                self.assertContains(self.client.get(self.product.get_absolute_url()), 'Please choose an available')

    def test_distinct_selections_can_be_updated_and_removed_independently(self):
        self.add()
        self.add(self.short.pk, self.small.pk, quantity=2)
        self.add()
        response = self.client.get(reverse('cart'))
        self.assertEqual(len(response.context['items']), 2)
        key = Cart.key(self.product, [self.long.pk, self.large.pk])
        self.client.post(reverse('update_cart', args=[self.product.pk]), {'key': key, 'quantity': 3})
        self.assertEqual(self.client.session[Cart.SESSION_KEY][key], 3)
        self.client.post(reverse('remove_from_cart', args=[self.product.pk]), {'key': key})
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {
            Cart.key(self.product, [self.short.pk, self.small.pk]): 2,
        })

    def test_guest_choices_merge_into_signed_in_bag_once(self):
        self.add()
        self.add(self.short.pk, self.small.pk)
        user = User.objects.create_user(username='option-customer')
        CartItem.objects.create(
            user=user, product=self.product, selection_key=f'{self.long.pk}:{self.large.pk}', quantity=2,
        )
        self.client.force_login(user)
        self.client.get(reverse('cart'))
        rows = CartItem.objects.filter(user=user)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.get(selection_key=Cart.selection_key([self.long.pk, self.large.pk])).quantity, 3)
        self.assertNotIn(Cart.SESSION_KEY, self.client.session)
        self.client.get(reverse('cart'))
        self.assertEqual(rows.get(selection_key=Cart.selection_key([self.long.pk, self.large.pk])).quantity, 3)
        self.client.logout()
        self.client.force_login(user)
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['cart'].subtotal, Decimal('146.25'))
        self.client.post(reverse('remove_from_cart', args=[self.product.pk]), {
            'key': Cart.key(self.product, [self.short.pk, self.small.pk]),
        })
        self.assertEqual(rows.count(), 1)

    def test_deleted_options_block_checkout_instead_of_using_base_price(self):
        self.add()
        self.long.delete()
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'Unavailable')
        self.assertContains(response, 'Remove this item and choose your options again.')
        self.assertNotContains(response, 'Proceed to checkout')
        self.assertIsNone(response.context['items'][0]['unit_price'])
        self.assertRedirects(self.client.post(reverse('checkout'), {}), reverse('cart'))
        self.assertFalse(Order.objects.exists())

    def test_legacy_product_only_bag_requires_options_if_product_now_has_choices(self):
        session = self.client.session
        session[Cart.SESSION_KEY] = {str(self.product.pk): 1}
        session.save()
        self.assertContains(self.client.get(reverse('cart')), 'Please choose an available chain length')
        self.assertRedirects(self.client.get(reverse('checkout')), reverse('cart'))

    def test_no_option_products_keep_original_bag_format_and_price(self):
        self.product.options.all().delete()
        self.client.post(reverse('add_to_cart', args=[self.product.pk]), {'quantity': 2})
        response = self.client.get(reverse('cart'))
        self.assertEqual(self.client.session[Cart.SESSION_KEY], {str(self.product.pk): 2})
        self.assertEqual(response.context['cart'].subtotal, Decimal('60.00'))
        self.assertNotContains(self.client.get(self.product.get_absolute_url()), 'name="options"')

    def test_one_option_dimension_does_not_require_the_other(self):
        self.product.options.filter(kind='Size').delete()
        self.add(size='')
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.context['items'][0]['unit_price'], Decimal('35.25'))
        self.assertEqual(response.context['items'][0]['options'], [self.long])

    def test_bag_and_checkout_recalculate_current_option_prices(self):
        self.add(quantity=2)
        self.long.additional_cost = Decimal('7.50')
        self.long.save(update_fields=['additional_cost'])
        bag = self.client.get(reverse('cart'))
        self.assertEqual(bag.context['items'][0]['unit_price'], Decimal('41.00'))
        checkout = self.client.get(reverse('checkout'))
        self.assertEqual(checkout.context['checkout_subtotal'], Decimal('82.00'))
        self.assertEqual(checkout.context['checkout_total'], Decimal('85.99'))

    @patch('shop.views.create_stripe_checkout', side_effect=ImproperlyConfigured('Test payment unavailable'))
    def test_payment_failure_retains_signed_in_selections_and_prices(self, create):
        user = User.objects.create_user(username='option-payment-customer')
        self.client.force_login(user)
        self.add(quantity=2)
        with self.assertLogs('shop.views', level='ERROR'):
            response = self.client.post(reverse('checkout'), self.checkout_data())
        self.assertContains(response, 'We could not start your payment.')
        self.assertEqual(response.context['checkout_subtotal'], Decimal('77.50'))
        self.assertEqual(response.context['checkout_total'], Decimal('81.49'))
        self.assertEqual(Order.objects.get().payment_status, 'pending')
        row = CartItem.objects.get(user=user)
        self.assertEqual(row.selection_key, Cart.selection_key([self.long.pk, self.large.pk]))
        self.assertEqual(row.quantity, 2)
        create.assert_called_once()

    def test_restock_link_requires_option_selection(self):
        token = signing.dumps({'product_id': self.product.pk}, salt='restock-add-to-bag')
        response = self.client.get(reverse('restock_add_to_bag', args=[self.product.slug]), {'token': token})
        self.assertRedirects(response, self.product.get_absolute_url())
        self.assertEqual(self.client.session.get(Cart.SESSION_KEY, {}), {})

    @override_settings(STRIPE_SECRET_KEY='sk_test_options', STRIPE_CURRENCY='gbp')
    @patch('shop.services.stripe.checkout.Session.create')
    def test_checkout_payment_and_order_outputs_use_snapshotted_choices(self, create):
        create.return_value = SimpleNamespace(id='cs_test_options', url='https://checkout.stripe.com/test')
        self.add(quantity=2)
        checkout = self.client.get(reverse('checkout'))
        self.assertContains(checkout, 'Chain length: 20 inches')
        self.assertEqual(checkout.context['checkout_subtotal'], Decimal('77.50'))
        response = self.client.post(reverse('checkout'), self.checkout_data())
        self.assertRedirects(response, create.return_value.url, fetch_redirect_response=False)
        order = Order.objects.get()
        self.assertEqual(order.subtotal, Decimal('77.50'))
        self.assertEqual(order.total, Decimal('81.49'))
        item = order.items.get()
        self.assertEqual(item.unit_price, Decimal('38.75'))
        self.assertEqual(item.options_summary, 'Chain length: 20 inches, Size: Large')
        line_item = create.call_args.kwargs['line_items'][0]
        self.assertEqual(line_item['price_data']['unit_amount'], 3875)
        self.assertEqual(line_item['price_data']['product_data']['description'], 'Chain length: 20 inches, Size: Large')
        self.long.label = 'Updated'
        self.long.additional_cost = Decimal('99.00')
        self.long.save()
        self.large.delete()
        item.refresh_from_db()
        self.assertEqual(item.selection_description, 'Chain length: 20 inches, Size: Large')
        for template in ['shop/order_confirmation_email.html', 'shop/order_confirmation_email.txt']:
            self.assertIn(item.selection_description, render_to_string(template, {'order': order}))
        self.assertTrue(build_order_pdf(order).startswith(b'%PDF'))

    @patch('shop.views.create_stripe_checkout')
    def test_checkout_checks_shared_stock_across_selections(self, create):
        self.add(quantity=6)
        self.add(self.short.pk, self.small.pk, quantity=5)
        response = self.client.post(reverse('checkout'), self.checkout_data())
        self.assertContains(response, 'only has 10 available')
        self.assertEqual(response.context['checkout_subtotal'], Decimal('382.50'))
        self.assertFalse(Order.objects.exists())
        create.assert_not_called()

    @staticmethod
    def checkout_data():
        return {
            'email': 'customer@example.com', 'shipping_method': 'standard_uk',
            'phone_country': 'GB', 'phone': '7123456789',
            'billing_name': 'Customer', 'billing_house_flat': '10', 'billing_address1': 'High Street',
            'billing_city': 'London', 'billing_postcode': 'SW1A 1AA', 'billing_country': 'United Kingdom',
        }


@override_settings(SECURE_SSL_REDIRECT=False, STAFF_MFA_REQUIRED=False)
class ProductOptionManagementTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user(username='option-staff', is_staff=True))
        self.data = {
            'name': 'Managed pendant', 'slug': 'managed-pendant', 'sku': 'MANAGED-001',
            'category': Product._meta.get_field('category').get_default(),
            'description': 'A managed pendant.', 'price': '40.00', 'cost_price': '10.00',
            'reorder_level': 5, 'restock_target': 20, 'stock_quantity': 10,
            'chain_style': 'Rope', 'colour': 'Silver', 'dimensions': '25 x 20 mm', 'active': 'on',
            'options-TOTAL_FORMS': 4, 'options-INITIAL_FORMS': 0,
            'options-0-kind': 'Chain length', 'options-0-label': '16 inches',
            'options-0-additional_cost': '0.00', 'options-0-is_default': 'on',
            'options-1-kind': 'chain length', 'options-1-label': '18 inches',
            'options-1-additional_cost': '2.00',
            'options-2-kind': 'Size', 'options-2-label': 'Small',
            'options-2-additional_cost': '0.00', 'options-2-is_default': 'on',
            'options-3-kind': 'Size', 'options-3-label': 'Medium',
            'options-3-additional_cost': '1.50',
        }

    def edit_data(self, product, **changes):
        options = list(product.options.order_by('kind', 'sort_order'))
        by_label = {option.label: option for option in options}
        data = {**self.data, 'options-INITIAL_FORMS': 4}
        for index, label in enumerate(['16 inches', '18 inches', 'Small', 'Medium']):
            data[f'options-{index}-id'] = by_label[label].pk
            data[f'options-{index}-product'] = product.pk
        data.update(changes)
        return data

    def test_control_panel_creates_edits_and_removes_options(self):
        response = self.client.post(reverse('control_products'), self.data)
        self.assertRedirects(response, reverse('control_products'))
        product = Product.objects.get()
        self.assertEqual(product.chain_style, 'Rope')
        self.assertEqual(product.colour, 'Silver')
        self.assertEqual(product.dimensions, '25 x 20 mm')
        self.assertEqual(product.options.count(), 4)
        self.assertEqual(product.options.get(is_default=True, kind='Chain length').label, '16 inches')
        self.assertEqual(product.options.get(is_default=True, kind='Size').label, 'Small')
        self.assertEqual(list(product.options.filter(kind='Chain length').values_list('label', 'sort_order')), [('16 inches', 0), ('18 inches', 1)])
        response = self.client.get(reverse('control_products'), {'edit': product.pk})
        self.assertContains(response, 'Product options')
        self.assertContains(response, '+ Add option')
        self.assertContains(response, '<option value="Colour">', html=False)
        self.assertContains(response, 'Extra price (£)')
        self.assertContains(response, 'Pendant dimensions')
        self.assertNotContains(response, 'compare_at_price')
        response = self.client.post(f'{reverse("control_products")}?edit={product.pk}', self.edit_data(
            product, **{'options-1-additional_cost': '4.75', 'options-3-DELETE': 'on'},
        ))
        self.assertRedirects(response, reverse('control_products'))
        self.assertEqual(product.options.count(), 3)
        self.assertEqual(product.options.get(label='18 inches').additional_cost, Decimal('4.75'))

    def test_control_panel_switches_default_option(self):
        self.client.post(reverse('control_products'), self.data)
        product = Product.objects.get()
        data = self.edit_data(product, **{'options-1-additional_cost': '0.00', 'options-1-is_default': 'on'})
        del data['options-0-is_default']
        response = self.client.post(f'{reverse("control_products")}?edit={product.pk}', data)
        self.assertRedirects(response, reverse('control_products'))
        self.assertEqual(product.options.get(kind='Chain length', is_default=True).label, '18 inches')
        self.assertFalse(product.options.get(label='16 inches').is_default)

    def test_control_panel_accepts_any_option_type(self):
        data = {
            **self.data, 'options-TOTAL_FORMS': 6,
            'options-4-kind': '  colour ', 'options-4-label': 'Gold', 'options-4-additional_cost': '0.00', 'options-4-is_default': 'on',
            'options-5-kind': 'Colour', 'options-5-label': 'Rose gold', 'options-5-additional_cost': '2.50',
        }
        response = self.client.post(reverse('control_products'), data)
        self.assertRedirects(response, reverse('control_products'))
        product = Product.objects.get()
        self.assertEqual(
            list(product.options.values_list('kind', 'label', 'additional_cost', 'is_default')),
            [
                ('Chain length', '16 inches', Decimal('0.00'), True), ('Chain length', '18 inches', Decimal('2.00'), False),
                ('Size', 'Small', Decimal('0.00'), True), ('Size', 'Medium', Decimal('1.50'), False),
                ('Colour', 'Gold', Decimal('0.00'), True), ('Colour', 'Rose gold', Decimal('2.50'), False),
            ],
        )

    def test_first_included_option_becomes_default_when_none_ticked(self):
        data = {key: value for key, value in self.data.items() if not key.endswith('is_default')}
        response = self.client.post(reverse('control_products'), data)
        self.assertRedirects(response, reverse('control_products'))
        product = Product.objects.get()
        self.assertEqual(set(product.options.filter(is_default=True).values_list('label', flat=True)), {'16 inches', 'Small'})

    def test_invalid_options_do_not_partially_save_product(self):
        for changes, message in [
            ({'options-1-additional_cost': '-1.00'}, None),
            ({'options-1-label': '16 inches'}, None),
            ({'options-1-additional_cost': 'not-money'}, None),
            ({'options-TOTAL_FORMS': ''}, None),
            ({'options-1-is_default': 'on', 'options-1-additional_cost': '0.00'}, 'Choose only one default chain length.'),
            ({'options-1-is_default': 'on', 'options-0-is_default': ''}, 'its extra price must be £0.00'),
            ({'options-0-additional_cost': '1.00', 'options-0-is_default': ''}, 'Add a chain length option with £0.00 extra price'),
        ]:
            with self.subTest(changes=changes):
                data = {key: value for key, value in {**self.data, **changes}.items() if value != ''}
                if changes == {'options-TOTAL_FORMS': ''}:
                    data['options-TOTAL_FORMS'] = ''
                response = self.client.post(reverse('control_products'), data)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Product.objects.exists())
                self.assertContains(response, 'data-open="true"', html=False)
                if message:
                    self.assertContains(response, message)

    def test_product_without_options_needs_no_rows(self):
        data = {key: value for key, value in self.data.items() if not key.startswith('options-')}
        data.update({'options-TOTAL_FORMS': 0, 'options-INITIAL_FORMS': 0})
        response = self.client.post(reverse('control_products'), data)
        self.assertRedirects(response, reverse('control_products'))
        self.assertFalse(ProductOption.objects.exists())
        response = self.client.get(Product.objects.get().get_absolute_url())
        self.assertNotContains(response, 'data-product-option')


class ProductOptionMigrationTests(TransactionTestCase):
    def test_legacy_values_and_signed_in_bag_are_preserved(self):
        executor = MigrationExecutor(connection)
        before = [('shop', '0043_order_refund_notes_stripetransaction_refund_notes')]
        after = [('shop', '0044_productoption_and_more')]
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            product = apps.get_model('shop', 'Product').objects.create(
                name='Legacy pendant', slug='legacy-pendant', sku='LEGACY-001',
                price='20.00', length='18 inches', size='Standard', dimensions='10 x 15 mm',
            )
            user = apps.get_model('auth', 'User').objects.create(username='legacy-bag-customer')
            apps.get_model('shop', 'CartItem').objects.create(user_id=user.pk, product_id=product.pk, quantity=2)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            options = apps.get_model('shop', 'ProductOption').objects.filter(product_id=product.pk)
            chain = options.get(kind='chain_length')
            size = options.get(kind='size')
            self.assertEqual(chain.label, '18 inches')
            self.assertEqual(size.label, 'Standard')
            self.assertEqual(chain.additional_cost, Decimal('0.00'))
            self.assertEqual(size.additional_cost, Decimal('0.00'))
            row = apps.get_model('shop', 'CartItem').objects.get(product_id=product.pk)
            self.assertEqual(row.selection_key, f'{chain.pk}:{size.pk}')
            self.assertEqual(row.quantity, 2)
            self.assertEqual(apps.get_model('shop', 'Product').objects.get(pk=product.pk).dimensions, '10 x 15 mm')
            ProductOptionModel = apps.get_model('shop', 'ProductOption')
            ProductOptionModel.objects.create(product_id=product.pk, kind='chain_length', label='20 inches', additional_cost='4.00', sort_order=1)
            ProductOptionModel.objects.create(product_id=product.pk, kind='chain_length', label='16 inches', additional_cost='0.00', sort_order=2)
            paid_only = apps.get_model('shop', 'Product').objects.create(name='Paid only', slug='paid-only', sku='PAID-001', price='20.00')
            ProductOptionModel.objects.create(product_id=paid_only.pk, kind='size', label='Large', additional_cost='2.00')
            defaults = [('shop', '0045_productoption_is_default')]
            executor = MigrationExecutor(connection)
            executor.migrate(defaults)
            options = executor.loader.project_state(defaults).apps.get_model('shop', 'ProductOption').objects
            self.assertEqual(
                set(options.filter(is_default=True).values_list('product_id', 'kind', 'label')),
                {(product.pk, 'Chain length', '18 inches'), (product.pk, 'Size', 'Standard')},
            )
            self.assertEqual(set(options.filter(product_id=product.pk).values_list('kind', flat=True)), {'Chain length', 'Size'})
            migrated_row = executor.loader.project_state(defaults).apps.get_model('shop', 'CartItem').objects.get(product_id=product.pk)
            self.assertEqual(migrated_row.selection_key, '-'.join(str(pk) for pk in sorted([chain.pk, size.pk])))
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


@override_settings(SECURE_SSL_REDIRECT=False, STAFF_MFA_REQUIRED=False)
class ProductShippingFieldLocationTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user(username='shipping-staff', is_staff=True))

    def test_product_editor_does_not_ask_for_jewellery_box_measurements(self):
        response = self.client.get(reverse('control_products'))

        for field in ('shipping_box_weight_grams', 'shipping_box_length_cm', 'shipping_box_width_cm', 'shipping_box_height_cm'):
            self.assertNotContains(response, f'name="{field}"')
        self.assertContains(response, reverse('control_delivery') + '#shipping-products')

    def test_delivery_page_manages_jewellery_box_measurements(self):
        Product.objects.create(name='Boxed pendant', slug='boxed-pendant', sku='BOXED-001', price=Decimal('25.00'))

        response = self.client.get(reverse('control_delivery'))

        for field in ('shipping_box_weight_grams', 'shipping_box_length_cm', 'shipping_box_width_cm', 'shipping_box_height_cm'):
            self.assertContains(response, f'name="{field}"')

    def test_product_measurements_can_still_be_saved_from_the_delivery_page(self):
        product = Product.objects.create(name='Boxed pendant', slug='boxed-pendant', sku='BOXED-001', price=Decimal('25.00'))

        response = self.client.post(reverse('control_delivery'), {
            'action': 'save_product_shipping', 'product_id': product.pk,
            'shipping_box_weight_grams': 150, 'shipping_box_length_cm': '10.00',
            'shipping_box_width_cm': '5.00', 'shipping_box_height_cm': '2.00',
        })

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.shipping_box_weight_grams, 150)
        self.assertEqual(product.shipping_box_length_cm, Decimal('10.00'))

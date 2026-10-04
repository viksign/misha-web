from django.core import mail
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Collection, InterestSignup, Product


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_RESET_FROM_EMAIL='no-reply@mishaislandheritage.com',
    SECURE_SSL_REDIRECT=False,
)
class PasswordResetEmailTests(TestCase):
    def test_password_reset_email_uses_dedicated_sender(self):
        User.objects.create_user(
            username='reset-customer',
            email='reset-customer@example.com',
            password='test-password',
        )

        response = self.client.post(
            reverse('password_reset'),
            {'email': 'reset-customer@example.com'},
            secure=True,
        )

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, 'no-reply@mishaislandheritage.com')


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PREORDER_NOTIFICATION_EMAIL='staff@example.com',
)
class InterestSignupTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name='Out of stock necklace',
            slug='out-of-stock-necklace',
            sku='OOS-001',
            description='A test product.',
            price='95.00',
            stock_quantity=0,
            active=True,
        )

    def test_preorder_interest_is_linked_to_enabled_product(self):
        self.product.preorder_enabled = True
        self.product.save(update_fields=['preorder_enabled'])
        user = User.objects.create_user(username='customer', email=' CUSTOMER@example.com ')
        self.client.force_login(user)

        response = self.client.post(
            reverse('preorder_interest_product', args=[self.product.slug]),
            {'quantity': '2'},
        )

        self.assertRedirects(response, f'{self.product.get_absolute_url()}#preorder')
        signup = InterestSignup.objects.get(interest_type='preorder')
        self.assertEqual(signup.product, self.product)
        self.assertEqual(signup.email, 'customer@example.com')
        self.assertEqual(signup.desired_quantity, 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ['customer@example.com'])
        self.assertIn('2 unit(s)', mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].to, ['staff@example.com'])
        self.assertIn('customer@example.com', mail.outbox[1].body)
        self.assertIn('Quantity requested: 2', mail.outbox[1].body)

    def test_preorder_setting_is_independent_of_collection(self):
        collection = Collection.objects.create(name='Test collection', slug='test-collection')
        self.product.collection = collection
        self.product.preorder_enabled = True
        self.product.save(update_fields=['collection', 'preorder_enabled'])
        another_product = Product.objects.create(
            name='Not enabled',
            slug='not-enabled',
            sku='OOS-002',
            collection=collection,
            description='A test product.',
            price='45.00',
            stock_quantity=0,
        )

        enabled_response = self.client.get(self.product.get_absolute_url())
        disabled_response = self.client.get(another_product.get_absolute_url())

        self.assertContains(enabled_response, 'Pre-order this piece.')
        self.assertContains(disabled_response, 'Sold out')
        self.assertNotContains(disabled_response, 'Join pre-order list')

    def test_restock_interest_is_linked_to_product(self):
        response = self.client.post(
            reverse('restock_notification', args=[self.product.slug]),
            {'email': 'customer@example.com'},
        )

        self.assertRedirects(response, f'{self.product.get_absolute_url()}#stock-notification')
        signup = InterestSignup.objects.get()
        self.assertEqual(signup.product, self.product)
        self.assertEqual(signup.interest_type, 'restock')

    def test_available_product_still_has_add_to_bag_path(self):
        self.product.stock_quantity = 1
        self.product.technical_details = 'Pendant specification.'
        self.product.save(update_fields=['stock_quantity', 'technical_details'])

        response = self.client.get(self.product.get_absolute_url())

        self.assertContains(response, 'Add to bag')
        self.assertNotContains(response, 'Notify me')
        self.assertContains(response, 'Product Overview')
        self.assertContains(response, 'Product Details')
        self.assertContains(response, 'Delivery options')
        self.assertContains(response, 'Specifications')
        self.assertContains(response, 'Shipment and Delivery')
        self.assertContains(response, 'UK Standard Delivery:')
        self.assertContains(response, '£3.99')

    def test_add_to_bag_offers_checkout_and_continue_shopping(self):
        self.product.stock_quantity = 1
        self.product.save(update_fields=['stock_quantity'])

        response = self.client.post(
            reverse('add_to_cart', args=[self.product.pk]),
            {'quantity': 1},
            follow=True,
            secure=True,
        )

        self.assertRedirects(response, reverse('checkout'))
        self.assertContains(response, 'Proceed to checkout')
        self.assertContains(response, 'Continue shopping')


class HomeFeatureTests(TestCase):
    def test_home_shows_selected_products_collections_and_preorders(self):
        featured_collection = Collection.objects.create(
            name='Featured collection',
            slug='featured-collection',
            featured=True,
        )
        unfeatured_collection = Collection.objects.create(
            name='Hidden collection',
            slug='hidden-collection',
        )
        featured_product = Product.objects.create(
            name='Featured pendant',
            slug='featured-pendant',
            sku='FEAT-001',
            collection=featured_collection,
            description='A featured product.',
            price='50.00',
            featured=True,
        )
        preorder_product = Product.objects.create(
            name='Preorder bracelet',
            slug='preorder-bracelet',
            sku='PRE-001',
            collection=unfeatured_collection,
            description='A preorder product.',
            price='65.00',
            preorder_enabled=True,
        )
        other_collection_preorder = Product.objects.create(
            name='Signature preorder piece',
            slug='signature-preorder-piece',
            sku='PRE-002',
            collection=featured_collection,
            description='A preorder product from another collection.',
            price='70.00',
            preorder_enabled=True,
        )
        other_featured_product = Product.objects.create(
            name='Other featured piece',
            slug='other-featured-piece',
            sku='FEAT-002',
            collection=unfeatured_collection,
            description='A featured product in an unfeatured collection.',
            price='35.00',
            featured=True,
        )

        response = self.client.get(reverse('home'))

        self.assertContains(response, featured_product.name)
        self.assertContains(response, other_featured_product.name)
        self.assertContains(response, featured_collection.name)
        self.assertContains(response, preorder_product.name)
        self.assertNotContains(response, 'Filter by collection')
        self.assertContains(response, 'Choose a piece to reserve from any collection.')

        filtered_response = self.client.get(reverse('home'), {'collection': unfeatured_collection.slug})

        self.assertContains(filtered_response, featured_product.name)
        self.assertContains(filtered_response, other_featured_product.name)
        self.assertNotContains(filtered_response, 'Filter by collection')
        self.assertSetEqual(
            {product.pk for product in filtered_response.context['featured_products']},
            {featured_product.pk, other_featured_product.pk},
        )
        self.assertSetEqual(
            {product.pk for product in filtered_response.context['preorder_products']},
            {preorder_product.pk, other_collection_preorder.pk},
        )
        self.assertEqual(list(filtered_response.context['featured_collections']), [featured_collection])


class CatalogueSortTests(TestCase):
    def setUp(self):
        self.collection = Collection.objects.create(name='Sort collection', slug='sort-collection')
        other_collection = Collection.objects.create(name='Other collection', slug='other-sort-collection')
        self.low_price = Product.objects.create(
            name='Piece low price',
            slug='piece-low-price',
            sku='SORT-001',
            collection=self.collection,
            description='Low priced test item.',
            price='10.00',
        )
        self.high_price = Product.objects.create(
            name='Piece high price',
            slug='piece-high-price',
            sku='SORT-002',
            collection=self.collection,
            description='High priced test item.',
            price='30.00',
        )
        self.preorder_product = Product.objects.create(
            name='Piece preorder',
            slug='piece-preorder',
            sku='SORT-003',
            collection=self.collection,
            description='Pre-order test item.',
            price='20.00',
            preorder_enabled=True,
        )
        Product.objects.create(
            name='Piece outside collection',
            slug='piece-outside-collection',
            sku='SORT-004',
            collection=other_collection,
            description='Item in a different collection.',
            price='1.00',
        )

    def test_price_and_preorder_sorting_preserve_collection_and_search(self):
        common_filters = {'collection': self.collection.slug, 'q': 'Piece'}

        low_to_high = self.client.get(reverse('catalogue'), {**common_filters, 'sort': 'price_asc'})
        high_to_low = self.client.get(reverse('catalogue'), {**common_filters, 'sort': 'price_desc'})
        preorder_first = self.client.get(reverse('catalogue'), {**common_filters, 'sort': 'preorder_first'})

        self.assertEqual(
            list(low_to_high.context['products'].values_list('name', flat=True)),
            ['Piece low price', 'Piece preorder', 'Piece high price'],
        )
        self.assertEqual(
            list(high_to_low.context['products'].values_list('name', flat=True)),
            ['Piece high price', 'Piece preorder', 'Piece low price'],
        )
        self.assertEqual(
            list(preorder_first.context['products'].values_list('name', flat=True)),
            ['Piece preorder', 'Piece high price', 'Piece low price'],
        )
        self.assertNotContains(preorder_first, 'Piece outside collection')
        self.assertContains(preorder_first, 'Price: low to high')
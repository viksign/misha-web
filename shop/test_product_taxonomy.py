from django.contrib.auth.models import User
from django.db import connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .forms import ProductManagementForm
from .models import Product, ProductCategory, ProductTag


@override_settings(SECURE_SSL_REDIRECT=False, STAFF_MFA_REQUIRED=False)
class ProductTaxonomyTests(TestCase):
    def setUp(self):
        self.necklaces, _ = ProductCategory.objects.get_or_create(slug='necklaces', defaults={'name': 'Necklaces'})
        self.rings, _ = ProductCategory.objects.get_or_create(slug='rings', defaults={'name': 'Rings'})
        self.best, _ = ProductTag.objects.get_or_create(slug='best-seller', defaults={'name': 'Best seller'})
        self.gifts, _ = ProductTag.objects.get_or_create(slug='gifts-for-parents', defaults={'name': 'Gifts for parents'})
        self.first = self.product('First', self.necklaces, '10.00')
        self.first.tags.set([self.best, self.gifts])
        self.second = self.product('Second', self.necklaces, '20.00')
        self.second.tags.add(self.gifts)
        self.ring = self.product('Ring', self.rings, '30.00')
        self.ring.tags.add(self.best)
        self.untagged = self.product('Untagged', self.necklaces, '40.00')
        self.hidden = self.product('Hidden', self.necklaces, '5.00', active=False)
        self.hidden.tags.add(self.best)
        self.url = reverse('catalogue')
        self.control_url = reverse('control_product_taxonomy')

    def product(self, name, category, price, **kwargs):
        return Product.objects.create(
            name=name, slug=name.lower(), sku=name.upper(), category=category,
            description='A test piece.', price=price, **kwargs,
        )

    def results(self, response):
        self.assertEqual(response.status_code, 200)
        return list(response.context['products'].values_list('pk', flat=True))

    def staff_login(self):
        self.client.force_login(User.objects.create_user(username='taxonomy-staff', is_staff=True))

    def test_category_and_any_tags_are_combined_without_duplicates(self):
        response = self.client.get(self.url, {
            'category': 'necklaces', 'tags': [self.best.slug, self.gifts.slug],
            'sort': 'price_asc',
        })
        self.assertEqual(self.results(response), [self.first.pk, self.second.pk])
        self.assertContains(response, '2 pieces found')
        self.assertContains(response, 'value="necklaces" selected')
        self.assertContains(response, f'<option value="{self.best.slug}" selected>{self.best.name}</option>', html=True)
        self.assertContains(response, f'<option value="{self.gifts.slug}" selected>{self.gifts.name}</option>', html=True)
        self.assertContains(response, 'name="tags"')
        self.assertContains(response, 'multiple')
        self.assertNotContains(response, 'type="checkbox" name="tags"')
        self.assertNotContains(response, 'name="q"')
        self.assertNotContains(response, 'name="collection"')

    def test_tags_only_include_all_categories_but_not_inactive_products(self):
        response = self.client.get(self.url, {'tags': self.best.slug, 'sort': 'price_asc'})
        self.assertEqual(self.results(response), [self.first.pk, self.ring.pk])

    def test_category_only_includes_untagged_products(self):
        response = self.client.get(self.url, {'category': 'necklaces', 'sort': 'price_asc'})
        self.assertEqual(self.results(response), [self.first.pk, self.second.pk, self.untagged.pk])

    def test_no_filters_and_legacy_name_query_do_not_search_product_names(self):
        expected = {self.first.pk, self.second.pk, self.ring.pk, self.untagged.pk}
        self.assertEqual(set(self.results(self.client.get(self.url))), expected)
        self.assertEqual(set(self.results(self.client.get(self.url, {'q': 'does-not-exist'}))), expected)

    def test_invalid_filter_values_show_explicit_errors(self):
        for params in ({'category': 'missing'}, {'tags': ['missing']}, {'tags': [self.best.slug, 'missing']}, {'sort': 'missing'}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context['filter_form'].errors)
                self.assertFalse(response.context['products'].exists())
                self.assertContains(response, 'Please choose an available', status_code=400)

    def test_price_and_preorder_sorting_keep_filters(self):
        self.second.preorder_enabled = True
        self.second.save()
        params = {'category': 'necklaces', 'tags': [self.best.slug, self.gifts.slug]}
        self.assertEqual(self.results(self.client.get(self.url, {**params, 'sort': 'price_desc'})), [self.second.pk, self.first.pk])
        self.assertEqual(self.results(self.client.get(self.url, {**params, 'sort': 'preorder_first'})), [self.second.pk, self.first.pk])

    def test_programmatic_default_and_required_category(self):
        piece = Product.objects.create(name='Default', slug='default', sku='DEFAULT', price='1.00')
        self.assertEqual(piece.category.slug, 'other')
        with self.assertRaises(ProtectedError):
            piece.category.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=piece.pk).update(category=None)
        form = ProductManagementForm(data={'name': 'Missing category'})
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_category_tag_pages_require_staff(self):
        response = self.client.post(self.control_url, {'action': 'delete_tag', 'tag_id': self.best.pk})
        self.assertEqual(response.status_code, 302)
        self.client.force_login(User.objects.create_user(username='customer'))
        self.assertEqual(self.client.get(self.control_url).status_code, 302)
        self.assertEqual(self.client.post(self.control_url, {'action': 'delete_tag', 'tag_id': self.best.pk}).status_code, 302)
        self.assertTrue(ProductTag.objects.filter(pk=self.best.pk).exists())

    def test_taxonomy_create_edit_delete_and_duplicate_validation(self):
        self.staff_login()
        for kind, Model in [('category', ProductCategory), ('tag', ProductTag)]:
            with self.subTest(kind=kind):
                params = {'action': f'save_{kind}', f'{kind}-name': 'New item', f'{kind}-slug': 'new-item'}
                self.assertRedirects(self.client.post(self.control_url, params), self.control_url)
                item = Model.objects.get(slug='new-item')
                duplicate = self.client.post(self.control_url, params)
                self.assertTrue(duplicate.context[f'{kind}_form'].errors)
                edit_url = f'{self.control_url}?edit_{kind}={item.pk}'
                self.assertContains(self.client.get(edit_url), 'New item')
                self.assertRedirects(self.client.post(edit_url, {
                    **params, f'{kind}-name': 'Updated item', f'{kind}-slug': 'updated-item',
                }), self.control_url)
                item.refresh_from_db()
                self.assertEqual(item.name, 'Updated item')
                self.assertRedirects(self.client.post(self.control_url, {
                    'action': f'delete_{kind}', f'{kind}_id': item.pk,
                }), self.control_url)
                self.assertFalse(Model.objects.filter(pk=item.pk).exists())

    def test_category_assigned_to_product_cannot_be_deleted(self):
        self.staff_login()
        response = self.client.post(self.control_url, {'action': 'delete_category', 'category_id': self.necklaces.pk}, follow=True)
        self.assertContains(response, 'Reassign those products')
        self.assertTrue(ProductCategory.objects.filter(pk=self.necklaces.pk).exists())
        self.first.refresh_from_db()
        self.assertEqual(self.first.category_id, self.necklaces.pk)

    def test_tag_deletion_removes_assignments_not_products_and_get_does_not_delete(self):
        self.staff_login()
        self.client.get(self.control_url, {'action': 'delete_tag', 'tag_id': self.best.pk})
        self.assertTrue(ProductTag.objects.filter(pk=self.best.pk).exists())
        self.assertRedirects(self.client.post(self.control_url, {'action': 'delete_tag', 'tag_id': self.best.pk}), self.control_url)
        self.assertTrue(Product.objects.filter(pk=self.first.pk).exists())
        self.assertEqual(list(self.first.tags.all()), [self.gifts])

    def test_csrf_is_required_for_management(self):
        self.staff_login()
        client = Client(enforce_csrf_checks=True)
        client.force_login(User.objects.get(username='taxonomy-staff'))
        response = client.post(self.control_url, {'action': 'delete_tag', 'tag_id': self.best.pk})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProductTag.objects.filter(pk=self.best.pk).exists())

    def test_product_control_saves_and_replaces_tags_and_category(self):
        self.staff_login()
        params = {
            'name': 'Managed', 'slug': 'managed', 'sku': 'MANAGED',
            'category': self.necklaces.pk, 'tags': [self.best.pk, self.gifts.pk],
            'description': 'Managed piece.', 'price': '50.00', 'cost_price': '5.00',
            'reorder_level': 5, 'restock_target': 20, 'stock_quantity': 5, 'active': 'on',
            'options-TOTAL_FORMS': 0, 'options-INITIAL_FORMS': 0,
        }
        url = reverse('control_products')
        self.assertRedirects(self.client.post(url, params), url)
        piece = Product.objects.get(sku='MANAGED')
        self.assertEqual(set(piece.tags.all()), {self.best, self.gifts})
        edit_url = f'{url}?edit={piece.pk}'
        response = self.client.get(edit_url)
        self.assertEqual(set(response.context['form']['tags'].value()), {self.best.pk, self.gifts.pk})
        self.assertContains(response, 'data-tag-picker')
        self.assertContains(response, f'<option value="{self.best.pk}" selected>{self.best.name}</option>', html=True)
        self.assertContains(response, f'<option value="{self.gifts.pk}" selected>{self.gifts.name}</option>', html=True)
        self.assertNotContains(response, 'type="checkbox" name="tags"')
        self.assertRedirects(self.client.post(edit_url, {
            **params, 'category': self.rings.pk, 'tags': [self.gifts.pk],
        }), url)
        piece.refresh_from_db()
        self.assertEqual(piece.category, self.rings)
        self.assertEqual(list(piece.tags.all()), [self.gifts])
        self.assertRedirects(self.client.post(edit_url, {**params, 'tags': []}), url)
        self.assertFalse(piece.tags.exists())

    def test_control_catalogue_filters_category_and_any_tags_with_distinct_results(self):
        self.staff_login()
        response = self.client.get(reverse('control_products'), {
            'category': self.necklaces.slug, 'tags': [self.best.slug, self.gifts.slug],
        })
        self.assertEqual(set(self.results(response)), {self.first.pk, self.second.pk, self.hidden.pk})
        self.assertContains(response, '<th>Category</th>', html=True)
        self.assertContains(response, '<th>Tags</th>', html=True)
        self.assertContains(response, 'product_filter_tags-search')
        self.assertContains(response, 'id_tags-search')
        self.assertContains(response, 'tags=best-seller&amp;tags=gifts-for-parents')
        self.assertNotContains(response, 'value="hide"')
        self.assertNotContains(response, 'value="delete"')

    def test_control_catalogue_combines_name_search_with_category_tags_and_collection(self):
        self.staff_login()
        response = self.client.get(reverse('control_products'), {
            'q': self.first.name, 'category': self.necklaces.slug,
            'tags': self.best.slug,
        })
        self.assertEqual(self.results(response), [self.first.pk])
        response = self.client.get(reverse('control_products'), {
            'category': 'no-such-category',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Please choose an available category and tags.', status_code=400)

    def test_control_catalogue_tag_filter_does_not_duplicate_pending_order_counts(self):
        from .models import Order, OrderItem

        self.staff_login()
        order = Order.objects.create(email='customer@example.com', shipping_name='Customer')
        OrderItem.objects.create(
            order=order, product=self.first, product_name=self.first.name,
            sku=self.first.sku, unit_price=self.first.price, quantity=1,
        )
        response = self.client.get(reverse('control_products'), {'tags': [self.best.slug, self.gifts.slug]})
        self.assertEqual(response.context['products'].get(pk=self.first.pk).pending_orders, 1)

    def test_filtered_pages_are_noindex_with_clean_canonical(self):
        for params in ({'category': 'necklaces'}, {'tags': self.best.slug}):
            response = self.client.get(self.url, params)
            self.assertIn('noindex', response['X-Robots-Tag'])
            self.assertContains(response, 'https://www.mishaislandheritage.com/jewellery/')


@override_settings(SECURE_SSL_REDIRECT=False)
class ProductTaxonomyMigrationTests(TransactionTestCase):
    def test_existing_products_are_backfilled_without_changing_product_data(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [('shop', '0048_customeraddress_address2')]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        ProductBefore = apps.get_model('shop', 'Product')
        product = ProductBefore.objects.create(
            name='Legacy piece', slug='legacy-piece', sku='LEGACY',
            description='Keep this description.', price='25.00', stock_quantity=7,
        )
        after = [('shop', '0049_product_categories_and_tags')]
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        piece = apps.get_model('shop', 'Product').objects.get(pk=product.pk)
        self.assertEqual(piece.category.slug, 'other')
        self.assertEqual(piece.description, 'Keep this description.')
        self.assertEqual(piece.stock_quantity, 7)
        self.assertFalse(piece.tags.exists())
        self.assertEqual(apps.get_model('shop', 'ProductCategory').objects.count(), 6)

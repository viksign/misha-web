from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AnalyticsEvent, CustomDesignRequest, CustomerReview, Enquiry, InterestSignup, Order, Product


@override_settings(SECURE_SSL_REDIRECT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class UserDashboardTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='manager', email='manager@example.com', first_name='Admin', last_name='Team', is_staff=True)
        self.customer = User.objects.create_user(username='customer', email='customer@example.com', first_name='Asha', last_name='Patel')
        self.disabled = User.objects.create_user(username='disabled', email='disabled@example.com', is_active=False)
        self.client.force_login(self.staff)

    def add_customer_records(self):
        products = [Product.objects.create(name=f'User pendant {index}', slug=f'user-pendant-{index}', sku=f'USER-{index}', price='29.99', stock_quantity=20) for index in range(2)]
        for index in range(2):
            Order.objects.create(user=self.customer, email=self.customer.email, shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA', status='completed' if index == 0 else 'pending')
            InterestSignup.objects.create(user=self.customer, email=self.customer.email, product=products[index], interest_type='preorder', desired_quantity=3)
            CustomDesignRequest.objects.create(user=self.customer, title=f'Customer idea {index}', product_details='A pendant design')
            Enquiry.objects.create(email=self.customer.email.upper() if index else self.customer.email, name='Customer', message='Customer enquiry')
            CustomerReview.objects.create(email=self.customer.email.upper() if index else self.customer.email, name='Customer', body='Customer feedback')
        InterestSignup.objects.create(user=self.customer, email=self.customer.email, product=products[0], interest_type='restock')
        for index in range(4):
            AnalyticsEvent.objects.create(user=self.customer, event_type='click', path='/jewellery/')

    def test_counts_are_not_multiplied_by_related_record_joins(self):
        self.add_customer_records()
        response = self.client.get(reverse('control_users'), {'q': 'customer@example.com'})
        account = response.context['users'][0]
        self.assertEqual((account.order_count, account.completed_order_count, account.preorder_count), (2, 1, 2))
        self.assertEqual((account.custom_request_count, account.enquiry_count, account.feedback_count, account.activity_count), (2, 2, 2, 4))
        self.assertEqual(response.context['summary']['orders'], 2)
        self.assertEqual(response.context['summary']['feedback'], 2)
        self.assertContains(response, 'Customer requests')

    def test_role_status_search_and_charts_share_the_same_account_scope(self):
        params = {'role': 'staff', 'state': 'active', 'q': 'Admin'}
        response = self.client.get(reverse('control_users'), params)
        self.assertEqual([user.pk for user in response.context['users']], [self.staff.pk])
        self.assertEqual(response.context['summary']['accounts'], 1)
        chart = self.client.get(reverse('users_role_chart'), params).json()
        self.assertEqual(chart['datasets'][0]['data'], [1, 0])
        response = self.client.get(reverse('control_users'), {'role': 'customer', 'state': 'disabled'})
        self.assertEqual([user.pk for user in response.context['users']], [self.disabled.pk])

    def test_engagement_chart_reports_account_records_not_requested_units(self):
        self.add_customer_records()
        data = self.client.get(reverse('users_engagement_chart'), {'q': self.customer.email}).json()
        self.assertEqual(data['datasets'][0]['data'], [2, 2, 2, 2, 2])

    def test_page_and_charts_require_staff(self):
        self.client.force_login(self.customer)
        for name in ['control_users', 'users_role_chart', 'users_engagement_chart']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_101_accounts_are_paginated_and_filters_are_preserved(self):
        User.objects.filter(pk__in=[self.customer.pk, self.disabled.pk]).delete()
        User.objects.bulk_create([User(username=f'new-customer-{index:03}', email=f'customer-{index}@example.com') for index in range(101)])
        params = {'role': 'customer', 'state': 'active', 'sort': 'oldest', 'q': 'new-customer'}
        response = self.client.get(reverse('control_users'), params)
        self.assertEqual(response.context['user_page'].paginator.count, 101)
        self.assertEqual(len(response.context['users']), 50)
        self.assertEqual(parse_qs(response.context['user_query']), {key: [value] for key, value in params.items()})
        last = self.client.get(reverse('control_users'), {**params, 'page': 3})
        self.assertEqual(len(last.context['users']), 1)

    def test_self_disable_and_self_staff_removal_are_prevented(self):
        self.client.post(reverse('control_users'), {'action': 'toggle_active', 'user_id': self.staff.pk})
        self.client.post(reverse('control_users'), {'action': 'edit_details', 'user_id': self.staff.pk, 'email': self.staff.email, 'first_name': 'Admin', 'last_name': 'Team'})
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertTrue(self.staff.is_staff)

    def test_account_action_redirect_keeps_active_filters(self):
        response = self.client.post(reverse('control_users') + '?role=customer&state=active&sort=name&q=customer', {'action': 'toggle_active', 'user_id': self.customer.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlparse(response['Location']).query), {'role': ['customer'], 'state': ['active'], 'sort': ['name'], 'q': ['customer']})
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)

    def test_password_reset_action_is_retained_without_sending_real_email(self):
        with patch('django.contrib.auth.forms.PasswordResetForm.save') as save:
            response = self.client.post(reverse('control_users'), {'action': 'reset_password', 'user_id': self.customer.pk})
        self.assertEqual(response.status_code, 302)
        save.assert_called_once()

    def test_anonymous_feedback_is_not_assigned_to_blank_email_accounts(self):
        self.staff.email = ''
        self.staff.save(update_fields=['email'])
        CustomerReview.objects.create(email='', name='Guest', body='Guest review')
        Enquiry.objects.create(email='', name='Guest', message='Guest message')
        response = self.client.get(reverse('control_users'), {'role': 'staff'})
        self.assertEqual(response.context['users'][0].feedback_count, 0)
        self.assertEqual(response.context['users'][0].enquiry_count, 0)
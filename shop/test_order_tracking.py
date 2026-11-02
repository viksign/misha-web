from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.conf import settings
from django.core import mail, signing
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Order
from .order_tracking import (
    TRACKING_LINK_MAX_AGE, TRACKING_LINK_SALT,
    order_tracking_data, private_order_tracking_path, royalmail_tracking_url,
)
from .services import send_order_confirmation, send_tracking_email


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class OrderTrackingPageTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            order_reference='123456',
            email='guest@example.com',
            shipping_name='Guest Customer',
            shipping_address1='10 High Street',
            shipping_city='London',
            shipping_postcode='SW1A 1AA',
            shipping_country='United Kingdom',
            status='shipped',
            tracking_number='AB123456789GB',
            subtotal=Decimal('25.00'),
            total=Decimal('28.99'),
        )

    def test_guest_lookup_requires_all_three_identity_details(self):
        response = self.client.get(reverse('order_tracking'))

        self.assertContains(response, 'Guest order lookup')
        self.assertContains(response, 'Email address')
        self.assertContains(response, 'Order number')
        self.assertContains(response, 'Delivery postcode')
        self.assertContains(response, 'create an account')

    def test_guest_lookup_accepts_csrf_protected_https_form_on_both_hosts(self):
        for host in ('mishaislandheritage.com', 'www.mishaislandheritage.com'):
            with self.subTest(host=host):
                client = Client(enforce_csrf_checks=True)
                response = client.get(reverse('order_tracking'), secure=True, HTTP_HOST=host)
                self.assertEqual(response['Referrer-Policy'], 'strict-origin')
                data = {
                    'email': self.order.email,
                    'order_number': self.order.order_reference,
                    'postcode': self.order.shipping_postcode,
                    'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
                }
                response = client.post(
                    reverse('order_tracking'), data, secure=True,
                    HTTP_HOST=host, HTTP_ORIGIN=f'https://{host}',
                    HTTP_REFERER=f'https://{host}/',
                )
                self.assertContains(response, 'AB123456789GB')
                response = client.post(
                    reverse('order_tracking'), {**data, 'csrfmiddlewaretoken': 'invalid'},
                    secure=True, HTTP_HOST=host, HTTP_ORIGIN=f'https://{host}',
                )
                self.assertEqual(response.status_code, 403)

    def test_guest_lookup_matches_email_order_number_and_normalized_postcode(self):
        response = self.client.post(reverse('order_tracking'), {
            'email': 'GUEST@example.com',
            'order_number': '#123456',
            'postcode': 'sw1a1aa',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ORDER 123456')
        self.assertContains(response, '75%')
        self.assertContains(response, 'Tracking number:')
        self.assertContains(response, 'Dispatched:')
        self.assertContains(response, 'Open carrier tracking')
        self.assertContains(response, 'Progress is updated by our team')
        self.assertNotContains(response, 'Guest order lookup')
        self.assertNotContains(response, 'class="order-tracking-form"')
        self.assertContains(response, f'href="{reverse("order_tracking")}">Track another order</a>')
        self.assertNotContains(response, 'Latest Royal Mail updates')

        fresh_lookup = self.client.get(reverse('order_tracking'))
        self.assertContains(fresh_lookup, 'Guest order lookup')
        self.assertContains(fresh_lookup, 'class="order-tracking-form"')
        self.assertNotContains(fresh_lookup, 'AB123456789GB')
        self.assertEqual(fresh_lookup.context['lookup_form']['email'].value(), None)

    def test_guest_lookup_does_not_disclose_unmatched_orders(self):
        for field, value in (
            ('email', 'wrong@example.com'),
            ('order_number', '654321'),
            ('postcode', 'N1 1AA'),
        ):
            data = {
                'email': 'guest@example.com',
                'order_number': '123456',
                'postcode': 'SW1A 1AA',
            }
            data[field] = value
            with self.subTest(field=field):
                response = self.client.post(reverse('order_tracking'), data)
                self.assertContains(response, 'We could not find an order matching those details.')
                self.assertContains(response, 'class="order-tracking-form"')
                self.assertNotContains(response, 'AB123456789GB')

    def test_guest_lookup_attempts_are_rate_limited(self):
        data = {
            'email': 'wrong@example.com',
            'order_number': '654321',
            'postcode': 'N1 1AA',
        }
        for _ in range(8):
            response = self.client.post(
                reverse('order_tracking'),
                data,
                HTTP_X_REAL_IP='203.0.113.10',
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse('order_tracking'),
            data,
            HTTP_X_REAL_IP='203.0.113.10',
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Too many lookup attempts', status_code=429)

    def test_registered_customer_sees_only_their_orders(self):
        own_order = Order.objects.create(
            user=User.objects.create_user(username='tracking-customer'),
            order_reference='234567',
            email='customer@example.com',
            shipping_name='Account Customer',
            shipping_address1='1 Example Street',
            shipping_city='London',
            shipping_postcode='N1 1AA',
        )
        other_user = User.objects.create_user(username='another-customer')
        Order.objects.create(
            user=other_user,
            order_reference='345678',
            email='other@example.com',
            shipping_name='Other Customer',
            shipping_address1='2 Example Street',
            shipping_city='London',
            shipping_postcode='N2 2BB',
        )
        self.client.force_login(own_order.user)

        response = self.client.get(reverse('order_tracking'))

        self.assertContains(response, 'ORDER 234567')
        self.assertNotContains(response, 'ORDER 345678')
        self.assertNotContains(response, 'Guest order lookup')

    @override_settings(STAFF_MFA_REQUIRED=False)
    def test_staff_queue_includes_all_active_paid_orders_and_customer_journey(self):
        staff = User.objects.create_user(username='tracking-staff', is_staff=True)
        other_staff = User.objects.create_user(username='other-tracking-staff', is_staff=True)
        self.client.force_login(staff)
        self.order.payment_status = 'paid'
        self.order.assigned_to = other_staff
        self.order.save()
        for index, status in enumerate(['pending', 'processing', 'shipped', 'completed', 'cancelled', 'return']):
            Order.objects.create(
                order_reference=f'88000{index}', email=f'queue{index}@example.com',
                shipping_name='Queue Customer', shipping_address1='1 Queue Street',
                shipping_city='London', shipping_postcode='N1 1AA',
                status=status, payment_status='paid',
            )
        unpaid = Order.objects.create(
            order_reference='990001', email='unpaid@example.com',
            shipping_name='Unpaid', shipping_address1='1 Street',
            shipping_city='London', shipping_postcode='N1 1AA', status='processing',
        )
        response = self.client.get(reverse('order_tracking'))
        self.assertTrue(response.context['staff_tracking'])
        self.assertContains(response, 'Staff fulfilment queue')
        for reference in ['123456', '880000', '880001', '880002']:
            self.assertContains(response, f'ORDER {reference}')
        for reference in ['880003', '880004', '880005', unpaid.order_reference]:
            self.assertNotContains(response, f'ORDER {reference}')
        self.assertContains(response, self.order.email)
        self.assertContains(response, 'Open carrier tracking')
        self.assertContains(response, royalmail_tracking_url(self.order.tracking_number))
        self.assertContains(response, 'View as customer')
        self.assertContains(response, f'{reverse("control_orders")}?edit={self.order.pk}')
        self.assertNotContains(response, 'Latest Royal Mail updates')
        self.assertIn('no-store', response['Cache-Control'])
        card = response.context['orders'][0]
        self.assertEqual(card.pk, self.order.pk)
        self.assertEqual(card.tracking_view, order_tracking_data(self.order))
        direct = self.client.get(card.customer_tracking_path)
        self.assertContains(direct, 'ORDER 123456')
        self.assertNotContains(direct, 'ORDER 880000')
        self.assertNotContains(direct, 'Staff fulfilment queue')
        self.assertNotContains(direct, 'Manage order')

    @override_settings(STAFF_MFA_REQUIRED=False)
    def test_staff_queue_is_paginated_and_empty_state_is_staff_specific(self):
        staff = User.objects.create_user(username='queue-pagination-staff', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse('order_tracking'))
        self.assertContains(response, 'No active paid orders')
        for index in range(11):
            Order.objects.create(
                order_reference=f'7700{index:02d}', email='queue@example.com',
                shipping_name='Queue', shipping_address1='1 Street',
                shipping_city='London', shipping_postcode='N1 1AA', payment_status='paid',
            )
        first = self.client.get(reverse('order_tracking'))
        second = self.client.get(reverse('order_tracking'), {'page': 2})
        self.assertEqual(first.context['order_page'].paginator.count, 11)
        self.assertEqual(len(first.context['orders']), 10)
        self.assertEqual(len(second.context['orders']), 1)
        self.assertContains(second, 'ORDER 770010')

    def test_numeric_order_id_is_accepted_for_legacy_orders(self):
        self.order.order_reference = None
        self.order.save(update_fields=['order_reference'])

        response = self.client.post(reverse('order_tracking'), {
            'email': 'guest@example.com',
            'order_number': str(self.order.pk),
            'postcode': 'SW1A 1AA',
        })

        self.assertContains(response, f'ORDER {self.order.pk}')

    def test_private_link_opens_single_order_without_lookup_or_login(self):
        Order.objects.create(
            order_reference='654321', email='other@example.com',
            shipping_name='Other Customer', shipping_address1='1 Other Street',
            shipping_city='London', shipping_postcode='N1 1AA',
            tracking_number='OTHERTRACKING123',
        )
        path = private_order_tracking_path(self.order)
        for logged_in in (False, True):
            with self.subTest(logged_in=logged_in):
                if logged_in:
                    self.client.force_login(User.objects.create_user(username='link-customer'))
                response = self.client.get(path)
                self.assertContains(response, 'ORDER 123456')
                self.assertContains(response, 'AB123456789GB')
                self.assertNotContains(response, 'OTHERTRACKING123')
                self.assertNotContains(response, 'Guest order lookup')
                self.assertNotContains(response, 'Find my order')
                self.assertContains(response, 'Keep this private link safe')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response['Referrer-Policy'], 'strict-origin')
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_invalid_or_expired_links_do_not_reveal_order(self):
        path = private_order_tracking_path(self.order)
        invalid_paths = [
            path + 'tampered',
            reverse('order_tracking') + '?token=',
            reverse('order_tracking') + '?token=' + signing.dumps(
                {'order_id': self.order.pk}, salt='other-purpose',
            ),
            reverse('order_tracking') + '?token=' + signing.dumps(
                {'order_id': 'not-an-order-id'}, salt=TRACKING_LINK_SALT,
            ),
        ]
        for invalid_path in invalid_paths:
            response = self.client.get(invalid_path)
            self.assertContains(response, 'invalid or has expired', status_code=400)
            self.assertNotContains(response, 'AB123456789GB', status_code=400)

        with patch('django.core.signing.time.time', return_value=1000000000):
            expired_path = private_order_tracking_path(self.order)
        with patch('django.core.signing.time.time', return_value=1000000000 + TRACKING_LINK_MAX_AGE + 1):
            response = self.client.get(expired_path)
        self.assertContains(response, 'invalid or has expired', status_code=400)
        self.assertNotContains(response, 'AB123456789GB', status_code=400)

    @override_settings(GOOGLE_ANALYTICS_ID='G-TRACKING-TEST', GOOGLE_ADS_ID='AW-TRACKING-TEST')
    def test_private_tracking_page_does_not_load_google_analytics(self):
        response = self.client.get(private_order_tracking_path(self.order))
        self.assertContains(response, "analytics: ''")
        self.assertContains(response, "advertising: ''")

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    @patch('shop.services.build_order_pdf', return_value=b'%PDF-test')
    def test_confirmation_and_dispatch_emails_contain_working_private_links(self, pdf):
        self.order.payment_status = 'paid'
        self.order.save()
        for send in (send_order_confirmation, send_tracking_email):
            self.assertTrue(send(self.order.pk))
            message = mail.outbox[-1]
            url = next(
                word for word in message.body.split()
                if word.startswith(settings.ORDER_TRACKING_SITE_URL + '/order-tracking/?token=')
            )
            self.assertEqual(urlsplit(url).path, '/order-tracking/')
            self.assertTrue(url.startswith(settings.ORDER_TRACKING_SITE_URL + '/order-tracking/?token='))
            token = parse_qs(urlsplit(url).query)['token'][0]
            self.assertContains(self.client.get(reverse('order_tracking'), {'token': token}), 'ORDER 123456')
            self.assertNotIn('This private link works without signing in', message.body)
            self.assertIn(url.replace('&', '&amp;'), message.alternatives[0].content)
            self.assertNotIn('This private link works without signing in', message.alternatives[0].content)
            self.assertIn(f'src="{settings.SITE_URL}/static/images/mih_logo.png"', message.alternatives[0].content)
            self.assertNotIn('cid:', message.alternatives[0].content)
            self.assertEqual(len(message.attachments), 1 if send is send_order_confirmation else 0)
            if send is send_order_confirmation:
                self.assertEqual(message.attachments[0].mimetype, 'application/pdf')

    @override_settings(STRIPE_SECRET_KEY='')
    def test_confirmation_display_has_private_tracking_link(self):
        self.order.stripe_session_id = 'cs_test_private_tracking'
        self.order.save()
        response = self.client.get(reverse('checkout_success'), {'session_id': self.order.stripe_session_id})
        self.assertContains(response, 'Track your order')
        self.assertContains(self.client.get(response.context['tracking_page_url']), 'ORDER 123456')

    def test_order_id_alone_cannot_issue_a_private_link(self):
        response = self.client.get(reverse('checkout_success'), {'order_id': self.order.pk})
        self.assertEqual(response.status_code, 404)
        self.client.force_login(User.objects.create_user(username='not-order-owner'))
        response = self.client.get(reverse('checkout_success'), {'order_id': self.order.pk})
        self.assertEqual(response.status_code, 404)

    def test_account_order_display_contains_private_link(self):
        user = User.objects.create_user(username='order-display-customer')
        self.order.user = user
        self.order.save()
        self.client.force_login(user)
        response = self.client.get(reverse('account'))
        order = response.context['orders'][0]
        self.assertContains(response, 'Track this order')
        self.assertContains(response, order.private_tracking_path)
        self.client.logout()
        self.assertContains(self.client.get(order.private_tracking_path), 'ORDER 123456')


class OrderJourneyTests(TestCase):
    def test_carrier_link_encodes_tracking_number(self):
        self.assertEqual(
            royalmail_tracking_url('AA 123'),
            'https://www.royalmail.com/track-your-item#/tracking-results/AA%20123',
        )

    def test_progress_uses_admin_status_and_milestone_dates(self):
        order = Order.objects.create(
            email='journey@example.com', shipping_name='Customer',
            shipping_address1='10 High Street', shipping_city='London',
            shipping_postcode='SW1A 1AA',
        )
        for status, progress, milestone in (
            ('pending', 25, 'created_at'),
            ('processing', 50, 'processing_started_at'),
            ('shipped', 75, 'shipped_at'),
            ('completed', 100, 'completed_at'),
        ):
            with self.subTest(status=status):
                order.status = status
                order.save()
                journey = order_tracking_data(order)
                self.assertEqual(journey['progress_percent'], progress)
                self.assertEqual(journey['steps'][progress // 25 - 1]['timestamp'], getattr(order, milestone))
                self.assertNotIn('events', journey)

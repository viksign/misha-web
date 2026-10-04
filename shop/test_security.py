from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .models import BlockedIP, SecurityEvent
from . import errors


@override_settings(
    DEBUG=False,
    SECURE_SSL_REDIRECT=False,
    MIDDLEWARE=['django.middleware.csrf.CsrfViewMiddleware'],
)
class ErrorPageTests(SimpleTestCase):
    def test_error_handlers_show_support_without_exception_details(self):
        request = RequestFactory().get('/')
        exception = RuntimeError('private-exception-details')
        for status, handler in [(400, errors.bad_request), (403, errors.permission_denied), (404, errors.page_not_found)]:
            with self.subTest(status=status):
                response = handler(request, exception)
                self.assertContains(response, 'customercare@mishaislandheritage.com', status_code=status)
                self.assertNotContains(response, str(exception), status_code=status)
                self.assertEqual(response['Cache-Control'], 'no-store')

    def test_missing_csrf_token_shows_friendly_page_and_remains_forbidden(self):
        response = Client(enforce_csrf_checks=True).post('/login/', {'username': 'customer@example.com'})
        self.assertContains(response, 'Please refresh and try again', status_code=403)
        self.assertContains(response, 'customercare@mishaislandheritage.com', status_code=403)
        self.assertNotContains(response, 'DEBUG=True', status_code=403)

    def test_unhandled_exception_shows_safe_server_error(self):
        with patch('django.contrib.auth.views.LoginView.dispatch', side_effect=RuntimeError('private-exception-details')):
            response = Client(raise_request_exception=False).get('/login/')
        self.assertContains(response, 'Something went wrong', status_code=500)
        self.assertContains(response, 'customercare@mishaislandheritage.com', status_code=500)
        self.assertNotContains(response, 'private-exception-details', status_code=500)

    def test_unknown_page_uses_custom_not_found_handler(self):
        response = self.client.get('/missing-error-page-test/')
        self.assertContains(response, 'Page not found', status_code=404)
        self.assertContains(response, 'customercare@mishaislandheritage.com', status_code=404)


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    SECURITY_REQUEST_LIMIT=1,
    SECURITY_REQUEST_WINDOW_SECONDS=60,
    SECURITY_REQUEST_BLOCK_SECONDS=300,
    SECURITY_LOGIN_LIMIT=12,
    SECURITY_LOGIN_WINDOW_SECONDS=600,
    SECURITY_LOGIN_BLOCK_SECONDS=1800,
)
class SuspiciousActivityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_repeated_requests_temporarily_block_and_log_client_ip(self):
        client_ip = '8.8.8.8'

        first_response = self.client.get('/', HTTP_X_REAL_IP=client_ip)
        limited_response = self.client.get('/', HTTP_X_REAL_IP=client_ip)
        blocked_response = self.client.get('/', HTTP_X_REAL_IP=client_ip)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(limited_response.status_code, 429)
        self.assertEqual(blocked_response.status_code, 403)
        self.assertTrue(BlockedIP.objects.filter(ip_address=client_ip).exists())
        event = SecurityEvent.objects.get(ip_address=client_ip)
        self.assertEqual(event.event_type, 'auto_block')
        self.assertEqual(event.path, '/')

    def test_private_ip_is_not_rate_limited(self):
        for _ in range(3):
            response = self.client.get('/', HTTP_X_REAL_IP='127.0.0.1')
            self.assertEqual(response.status_code, 200)
        self.assertFalse(BlockedIP.objects.exists())

    @override_settings(SECURITY_REQUEST_LIMIT=100, SECURITY_LOGIN_LIMIT=2)
    def test_repeated_login_posts_create_a_security_block(self):
        client_ip = '1.1.1.1'
        for _ in range(2):
            response = self.client.post('/login/', {'username': 'customer@example.com', 'password': 'invalid'}, HTTP_X_REAL_IP=client_ip)
            self.assertEqual(response.status_code, 200)

        response = self.client.post('/login/', {'username': 'customer@example.com', 'password': 'invalid'}, HTTP_X_REAL_IP=client_ip)

        self.assertEqual(response.status_code, 429)
        self.assertTrue(BlockedIP.objects.filter(ip_address=client_ip).exists())
        self.assertIn('login submissions', SecurityEvent.objects.get(ip_address=client_ip).reason)

    def test_staff_can_unblock_an_address_from_activity_page(self):
        client_ip = '8.8.4.4'
        blocked_ip = BlockedIP.objects.create(
            ip_address=client_ip,
            reason='Test rate limit',
            blocked_until=timezone.now() + timedelta(minutes=5),
        )
        staff = User.objects.create_user(username='security-staff', password='test-password', is_staff=True)
        self.client.force_login(staff)

        dashboard = self.client.get(reverse('control_analytics'))
        self.assertContains(dashboard, client_ip)
        self.assertContains(dashboard, 'Active IP blocks')

        response = self.client.post(reverse('control_analytics'), {
            'action': 'unblock_ip',
            'block_id': blocked_ip.pk,
        })

        self.assertRedirects(response, reverse('control_analytics'))
        blocked_ip.refresh_from_db()
        self.assertLessEqual(blocked_ip.blocked_until, timezone.now())
        self.assertTrue(SecurityEvent.objects.filter(ip_address=client_ip, event_type='unblocked').exists())
from types import SimpleNamespace
from base64 import b32decode

from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from .admin_site import StaffAdminSite


@override_settings(
    STAFF_MFA_REQUIRED=True, SECURE_SSL_REDIRECT=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class StaffMFATests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='mfa-staff', email='staff@example.com', password='test-password', is_staff=True, is_superuser=True)
        self.customer = User.objects.create_user(username='mfa-customer', email='customer@example.com', password='test-password')

    def device(self):
        return TOTPDevice.objects.create(user=self.staff, name='default', confirmed=True)

    def verify_session(self, device):
        self.client.force_login(self.staff)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def test_all_staff_surfaces_require_mfa_even_after_password_only_login(self):
        self.client.force_login(self.staff)
        for url in [
            reverse('control_panel'), reverse('control_orders'), reverse('control_sales'),
            reverse('control_analytics'), reverse('analytics_locations_chart'),
            reverse('control_users'), reverse('control_order_pdf', args=[1]), '/admin/', '/admin/auth/user/',
        ]:
            response = self.client.get(url)
            self.assertRedirects(response, reverse('two_factor:setup'), fetch_redirect_response=False)
        self.device()
        response = self.client.get(reverse('control_sales'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('two_factor:login') + '?next='))
        response = self.client.post(reverse('control_orders'), {'action': 'batch_update'})
        self.assertEqual(response.status_code, 302)

    def test_anonymous_admin_and_staff_requests_use_mfa_login(self):
        for url in ['/admin/', '/admin/login/', reverse('control_panel')]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('two_factor:login')))

    def test_verified_staff_can_access_admin_controlpanel_and_recovery_codes(self):
        self.verify_session(self.device())
        for url in ['/admin/', reverse('control_panel'), reverse('two_factor:profile'), reverse('two_factor:backup_tokens')]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.post(reverse('two_factor:backup_tokens')).status_code, 302)
        self.assertEqual(StaticToken.objects.filter(device__user=self.staff).count(), 10)

    def test_account_security_rejects_anonymous_customer_and_password_only_staff(self):
        urls = [reverse('two_factor:profile'), reverse('two_factor:backup_tokens')]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('two_factor:login')))
        customer_device = TOTPDevice.objects.create(user=self.customer, name='default', confirmed=True)
        self.client.force_login(self.customer)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = customer_device.persistent_id
        session.save()
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertNotContains(response, 'Staff access verified.', status_code=302)
        self.client.force_login(self.staff)
        for url in urls:
            self.assertRedirects(self.client.get(url), reverse('two_factor:setup'), fetch_redirect_response=False)
        self.device()
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('two_factor:login')))

    def test_verified_staff_overview_confirms_identity_and_recovery_link_opens_codes_page(self):
        self.verify_session(self.device())
        response = self.client.get(reverse('two_factor:profile'))
        self.assertContains(response, 'Staff access verified.')
        self.assertContains(response, self.staff.username)
        self.assertContains(response, f'href="{reverse("two_factor:backup_tokens")}"')
        codes = self.client.get(reverse('two_factor:backup_tokens'))
        self.assertContains(codes, 'Generate Tokens')
        self.assertNotContains(codes, 'Staff access verified.')
        response = self.client.post(reverse('two_factor:backup_tokens'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain, [(reverse('two_factor:backup_tokens'), 302)])
        self.assertEqual(response.context['device'].token_set.count(), 10)
        self.assertEqual(response['Cache-Control'].split(',')[0], 'max-age=0')

    def test_customer_login_is_unchanged_and_cannot_access_staff_enrollment(self):
        self.customer.username = self.customer.email
        self.customer.save(update_fields=['username'])
        response = self.client.post(reverse('login'), {'email': self.customer.email, 'password': 'test-password'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse('account')).status_code, 200)
        self.assertEqual(self.client.get(reverse('control_panel')).status_code, 302)
        self.assertEqual(self.client.get(reverse('two_factor:setup')).status_code, 302)
        self.client.get(reverse('two_factor:login'))
        page = self.client.get(reverse('two_factor:login'))
        prefix = page.context['wizard']['management_form'].prefix
        response = self.client.post(reverse('two_factor:login'), {
            f'{prefix}-current_step': 'auth', 'auth-username': self.customer.email, 'auth-password': 'test-password',
        })
        self.assertContains(response, 'staff accounts only')

    def test_totp_login_requires_valid_code_and_prevents_replay(self):
        device = self.device()
        url = reverse('two_factor:login') + '?next=' + reverse('control_panel')
        response = self.client.get(url)
        prefix = response.context['wizard']['management_form'].prefix
        response = self.client.post(url, {
            f'{prefix}-current_step': 'auth', 'auth-username': self.staff.email, 'auth-password': 'test-password',
        })
        self.assertEqual(response.context['wizard']['steps'].current, 'token')
        self.assertNotIn(DEVICE_ID_SESSION_KEY, self.client.session)
        response = self.client.post(url, {f'{prefix}-current_step': 'token', 'token-otp_token': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(DEVICE_ID_SESSION_KEY, self.client.session)
        token = str(totp(device.bin_key)).zfill(6)
        response = self.client.post(url, {f'{prefix}-current_step': 'token', 'token-otp_token': token})
        self.assertRedirects(response, reverse('control_panel'), fetch_redirect_response=False)
        self.assertEqual(self.client.session[DEVICE_ID_SESSION_KEY], device.persistent_id)
        device.refresh_from_db()
        self.assertFalse(device.verify_token(token))

    def test_backup_code_is_single_use_and_verifies_staff_session(self):
        self.device()
        backup = StaticDevice.objects.create(user=self.staff, name='backup')
        StaticToken.objects.create(device=backup, token='recovery123')
        url = reverse('two_factor:login')
        response = self.client.get(url)
        prefix = response.context['wizard']['management_form'].prefix
        self.client.post(url, {f'{prefix}-current_step': 'auth', 'auth-username': self.staff.email, 'auth-password': 'test-password'})
        self.client.post(url, {'wizard_goto_step': 'backup', f'{prefix}-current_step': 'token'})
        response = self.client.post(url, {f'{prefix}-current_step': 'backup', 'backup-otp_token': 'recovery123'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[DEVICE_ID_SESSION_KEY], backup.persistent_id)
        self.assertFalse(backup.token_set.exists())

    def test_setup_requires_confirmation_and_verifies_session(self):
        self.client.force_login(self.staff)
        url = reverse('two_factor:setup')
        response = self.client.get(url)
        prefix = response.context['wizard']['management_form'].prefix
        response = self.client.post(url, {f'{prefix}-current_step': 'welcome'})
        self.assertEqual(response.context['wizard']['steps'].current, 'generator')
        self.assertFalse(TOTPDevice.objects.filter(user=self.staff, confirmed=True).exists())
        invalid = self.client.post(url, {f'{prefix}-current_step': 'generator', 'generator-token': 'wrong'})
        self.assertEqual(invalid.status_code, 200)
        self.assertNotIn(DEVICE_ID_SESSION_KEY, self.client.session)
        self.assertFalse(TOTPDevice.objects.filter(user=self.staff, confirmed=True).exists())
        key = self.client.session['django_two_factor-qr_secret_key']
        token = str(totp(b32decode(key))).zfill(6)
        response = self.client.post(url, {f'{prefix}-current_step': 'generator', 'generator-token': token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TOTPDevice.objects.filter(user=self.staff, confirmed=True).exists())
        self.assertIn(DEVICE_ID_SESSION_KEY, self.client.session)
        self.assertContains(self.client.get(reverse('two_factor:setup_complete')), 'Generate recovery codes')

    def test_mfa_post_requires_csrf_and_enrolled_staff_cannot_reenroll_without_verification(self):
        self.device()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('two_factor:setup')).status_code, 302)
        self.assertEqual(self.client.get(reverse('two_factor:qr')).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('two_factor:login'), {}).status_code, 403)
        self.assertEqual(self.client.get('/staff-auth/disable/').status_code, 302)
        self.verify_session(TOTPDevice.objects.get(user=self.staff))
        self.assertEqual(self.client.get('/staff-auth/disable/').status_code, 404)

    def test_admin_has_permission_independently_requires_verified_session(self):
        site = StaffAdminSite()
        request = RequestFactory().get('/admin/')
        request.user = SimpleNamespace(is_active=True, is_staff=True, is_verified=lambda: False)
        self.assertFalse(site.has_permission(request))
        request.user.is_verified = lambda: True
        self.assertTrue(site.has_permission(request))

    def test_middleware_does_not_block_storefront_or_customer_auth(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_staff_login_is_rate_limited(self):
        with override_settings(SECURITY_LOGIN_LIMIT=1):
            self.client.post(reverse('two_factor:login'), {}, HTTP_X_REAL_IP='8.8.8.8')
            self.assertEqual(self.client.post(reverse('two_factor:login'), {}, HTTP_X_REAL_IP='8.8.8.8').status_code, 429)

    def test_deleted_device_revokes_staff_verification(self):
        device = self.device()
        self.verify_session(device)
        device.delete()
        self.assertRedirects(self.client.get(reverse('control_panel')), reverse('two_factor:setup'), fetch_redirect_response=False)

    def test_staff_login_rejects_external_next_url(self):
        self.verify_session(self.device())
        response = self.client.get(reverse('two_factor:login') + '?next=https://example.com/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'cdnjs.cloudflare.com')

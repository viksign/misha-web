from datetime import timedelta
from io import StringIO
from smtplib import SMTPException
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import OrderReminderRuleForm
from .models import Order, OrderReminderDelivery, OrderReminderRule
from .order_reminders import send_order_reminders


@override_settings(SECURE_SSL_REDIRECT=False, STAFF_MFA_REQUIRED=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrderReminderTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.staff = User.objects.create_user(username='reminder-staff', email='staff@example.com', is_staff=True)
        self.admin = User.objects.create_superuser(username='reminder-admin', email='admin@example.com', password='test-password')
        self.rule = OrderReminderRule.objects.create(status='pending', after_hours=24, enabled=True, distribution_emails='team@example.com')
        self.rule.staff.add(self.staff)
        self.order = Order.objects.create(
            email='customer@example.com', shipping_name='Customer',
            status_entered_at=self.now - timedelta(hours=24),
        )
        self.url = reverse('control_notifications')

    def test_exact_threshold_all_orders_and_once_per_status(self):
        self.assertEqual(self.order.payment_status, 'pending')
        self.assertEqual(send_order_reminders(now=self.now - timedelta(microseconds=1)), 0)
        self.assertEqual(send_order_reminders(now=self.now), 1)
        self.assertEqual(send_order_reminders(now=self.now + timedelta(days=3)), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].bcc), {'staff@example.com', 'team@example.com'})
        self.assertEqual(mail.outbox[0].to, [])
        self.assertNotIn(self.order.email, mail.outbox[0].body)
        self.assertIn('/controlpanel/orders/?edit=', mail.outbox[0].body)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'pending')
        self.assertIsNone(self.order.payment_confirmation_sent_at)
        self.assertIsNone(self.order.tracking_email_sent_at)
        self.assertEqual(OrderReminderDelivery.objects.count(), 1)

    def test_reentering_status_and_rule_edits_do_not_resend(self):
        send_order_reminders(now=self.now)
        self.order.status = 'processing'
        self.order.save(update_fields=['status'])
        self.order.status = 'pending'
        self.order.save(update_fields=['status'])
        Order.objects.filter(pk=self.order.pk).update(status_entered_at=self.now - timedelta(days=4))
        self.rule.after_hours = 1
        self.rule.save()
        self.assertEqual(send_order_reminders(now=self.now), 0)

    def test_reminder_link_targets_old_order_and_opens_management_dialog(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=self.now - timedelta(days=120))
        other = Order.objects.create(email='other@example.com', shipping_name='Other')
        self.client.force_login(self.staff)
        response = self.client.get(reverse('control_orders'), {
            'edit': self.order.pk, 'page': '99', 'q': 'no-match', 'payment_scope': 'paid',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([order.pk for order in response.context['orders']], [self.order.pk])
        self.assertContains(response, f'data-order-modal="manage-order-{self.order.pk}" data-order-auto-open')
        self.assertNotContains(response, f'data-order-modal="manage-order-{other.pk}"')
        self.assertContains(response, 'View all orders')

    def test_reminder_link_requires_staff_sign_in(self):
        url = reverse('control_orders') + f'?edit={self.order.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])
        customer = User.objects.create_user(username='reminder-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_other_status_gets_its_own_reminder(self):
        send_order_reminders(now=self.now)
        self.order.status = 'processing'
        self.order.save(update_fields=['status'])
        Order.objects.filter(pk=self.order.pk).update(status_entered_at=self.now - timedelta(hours=2))
        OrderReminderRule.objects.create(status='processing', after_hours=1, enabled=True, distribution_emails='team@example.com')
        self.assertEqual(send_order_reminders(now=self.now), 1)
        self.assertEqual(OrderReminderDelivery.objects.count(), 2)

    def test_status_timer_only_changes_on_real_status_transition(self):
        original = self.order.status_entered_at
        self.order.shipping_name = 'Changed name'
        self.order.save()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status_entered_at, original)
        self.order.status = 'processing'
        self.order.save(update_fields=['status'])
        self.order.refresh_from_db()
        entered = self.order.status_entered_at
        self.assertGreater(entered, original)
        self.assertIsNotNone(self.order.processing_started_at)
        self.order.save(update_fields=['status'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status_entered_at, entered)

    def test_disabled_rule_and_dry_run_send_nothing(self):
        self.assertEqual(send_order_reminders(now=self.now, dry_run=True), 1)
        self.assertFalse(OrderReminderDelivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        self.rule.enabled = False
        self.rule.save()
        self.assertEqual(send_order_reminders(now=self.now), 0)

    def test_recipients_are_deduplicated_and_inactive_staff_excluded(self):
        self.rule.distribution_emails = 'TEAM@example.com\nteam@example.com\nSTAFF@example.com'
        self.rule.save()
        inactive = User.objects.create_user(username='inactive-reminder', email='inactive@example.com', is_staff=True, is_active=False)
        self.rule.staff.add(inactive)
        send_order_reminders(now=self.now)
        self.assertEqual(mail.outbox[0].bcc, ['staff@example.com', 'team@example.com'])

    def test_no_eligible_recipients_is_an_explicit_error(self):
        self.rule.distribution_emails = ''
        self.rule.save()
        self.staff.is_active = False
        self.staff.save()
        with self.assertRaises(ValidationError):
            send_order_reminders(now=self.now)
        self.assertFalse(OrderReminderDelivery.objects.exists())

    @patch('shop.order_reminders.EmailMessage.send', side_effect=SMTPException('SMTP unavailable'))
    def test_send_failure_is_not_recorded_and_command_reports_failure(self, send):
        with self.assertRaises(SMTPException):
            send_order_reminders(now=self.now)
        self.assertFalse(OrderReminderDelivery.objects.exists())
        with self.assertLogs('shop.management.commands.send_order_reminders', level='ERROR'):
            with self.assertRaises(CommandError):
                call_command('send_order_reminders', stdout=StringIO())

    def test_form_validation(self):
        for fields in [
            {'after_hours': 0, 'distribution_emails': 'team@example.com'},
            {'after_hours': 1, 'distribution_emails': 'not-an-email'},
            {'after_hours': 1, 'distribution_emails': ''},
        ]:
            form = OrderReminderRuleForm(data={'status': 'processing', 'enabled': 'on', **fields})
            self.assertFalse(form.is_valid())
        form = OrderReminderRuleForm(data={'status': 'processing', 'after_hours': 1, 'enabled': 'on', 'distribution_emails': 'TEAM@example.com\nteam@example.com'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['distribution_emails'], 'team@example.com')

    def test_only_admin_can_configure_and_csrf_is_enforced(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'delete', 'rule_id': self.rule.pk}).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url, {'action': 'delete', 'rule_id': self.rule.pk}).status_code, 403)
        self.assertTrue(OrderReminderRule.objects.filter(pk=self.rule.pk).exists())

    def test_admin_create_edit_delete_and_delivery_history(self):
        self.client.force_login(self.admin)
        params = {'status': 'processing', 'after_hours': 2, 'staff': [self.staff.pk], 'enabled': 'on'}
        self.assertRedirects(self.client.post(self.url, params), self.url)
        rule = OrderReminderRule.objects.get(status='processing')
        self.assertEqual(list(rule.staff.all()), [self.staff])
        self.assertRedirects(self.client.post(f'{self.url}?edit={rule.pk}', {**params, 'after_hours': 6}), self.url)
        rule.refresh_from_db()
        self.assertEqual(rule.after_hours, 6)
        send_order_reminders(now=self.now)
        self.assertRedirects(self.client.post(self.url, {'action': 'delete', 'rule_id': self.rule.pk}), self.url)
        self.assertTrue(OrderReminderDelivery.objects.filter(order=self.order).exists())
        self.assertContains(self.client.get(self.url), 'Recent reminders sent')


class OrderReminderMigrationTests(TransactionTestCase):
    def test_existing_status_times_use_milestones_and_updated_time(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [('shop', '0049_product_categories_and_tags')]
        executor.migrate(before)
        OldOrder = executor.loader.project_state(before).apps.get_model('shop', 'Order')
        old = timezone.now() - timedelta(days=4)
        pending = OldOrder.objects.create(email='pending@example.com', shipping_name='Customer')
        processing = OldOrder.objects.create(email='processing@example.com', shipping_name='Customer', status='processing', processing_started_at=old)
        returned = OldOrder.objects.create(email='returned@example.com', shipping_name='Customer', status='return')
        OldOrder.objects.filter(pk=returned.pk).update(updated_at=old)
        after = [('shop', '0050_order_reminders')]
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        NewOrder = executor.loader.project_state(after).apps.get_model('shop', 'Order')
        self.assertEqual(NewOrder.objects.get(pk=pending.pk).status_entered_at, pending.created_at)
        self.assertEqual(NewOrder.objects.get(pk=processing.pk).status_entered_at, old)
        self.assertEqual(NewOrder.objects.get(pk=returned.pk).status_entered_at, old)

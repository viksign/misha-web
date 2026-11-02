from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class StripeReconciliationCommandTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @patch('shop.management.commands.sync_stripe_reconciliation.timezone.localdate', return_value=date(2026, 1, 1))
    @patch('shop.management.commands.sync_stripe_reconciliation.sync_stripe_transactions')
    def test_refreshes_previous_and_current_year(self, sync, localdate):
        sync.return_value = SimpleNamespace(status='complete', transaction_count=3, error_code='')
        output = StringIO()
        call_command('sync_stripe_reconciliation', stdout=output)
        self.assertEqual([call.args[0]['year'] for call in sync.call_args_list], [2025, 2026])
        self.assertTrue(all(call.args[1] is None for call in sync.call_args_list))
        self.assertIn('3 transactions', output.getvalue())
        self.assertIsNone(cache.get('sales:stripe-sync'))

    @patch('shop.management.commands.sync_stripe_reconciliation.sync_stripe_transactions')
    def test_does_not_overlap_manual_refresh(self, sync):
        cache.add('sales:stripe-sync', True, 60)
        with self.assertRaisesMessage(CommandError, 'Another Stripe refresh'):
            call_command('sync_stripe_reconciliation')
        sync.assert_not_called()
        self.assertTrue(cache.get('sales:stripe-sync'))

    @patch('shop.management.commands.sync_stripe_reconciliation.sync_stripe_transactions')
    def test_reports_incomplete_refresh_and_attempts_both_years(self, sync):
        sync.side_effect = [
            SimpleNamespace(status='partial', transaction_count=2, error_code=''),
            SimpleNamespace(status='failed', transaction_count=0, error_code='AuthenticationError'),
        ]
        with self.assertRaisesMessage(CommandError, 'Stripe reconciliation incomplete'):
            call_command('sync_stripe_reconciliation', stdout=StringIO())
        self.assertEqual(sync.call_count, 2)
        self.assertIsNone(cache.get('sales:stripe-sync'))

    @patch('shop.management.commands.sync_stripe_reconciliation.sync_stripe_transactions', side_effect=RuntimeError('Database unavailable'))
    def test_releases_lock_after_unexpected_error(self, sync):
        with self.assertRaisesMessage(RuntimeError, 'Database unavailable'):
            call_command('sync_stripe_reconciliation')
        self.assertIsNone(cache.get('sales:stripe-sync'))

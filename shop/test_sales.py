from datetime import datetime
import uuid
from decimal import Decimal
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .forms import OfflineSaleForm
from .models import Order, OrderItem, Product, StockMovement, StripeSyncRun, StripeTransaction
from .sales import completed_sales_report, record_offline_sale, reconciliation_report, replenishment_budget, sales_window, sync_stripe_transactions


@override_settings(SECURE_SSL_REDIRECT=False, STRIPE_SECRET_KEY='sk_live_test', STRIPE_CURRENCY='gbp', CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SalesAccountingTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='finance-staff', is_staff=True)
        self.product = Product.objects.create(name='Finance pendant', slug='finance-pendant', sku='FINANCE-001', price='30', cost_price='8', stock_quantity=75)
        self.year = timezone.localdate().year
        self.window = sales_window({'year': str(self.year)})
        self.date = timezone.make_aware(datetime(self.year, 2, 15, 12))

    def order(self, **values):
        values.setdefault('payment_status', 'paid')
        values.setdefault('payment_date', self.date)
        values.setdefault('total', Decimal('60'))
        values.setdefault('subtotal', Decimal('60'))
        return Order.objects.create(email='customer@example.com', shipping_name='Customer', shipping_address1='1 Test Street', shipping_city='London', shipping_postcode='N1 1AA', **values)

    def complete(self, order):
        Order.objects.filter(pk=order.pk).update(status='completed', completed_at=self.date)
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name, sku=self.product.sku, unit_price='30', unit_cost='8', quantity=2)

    def receipt(self, order=None, **values):
        return StripeTransaction.objects.create(stripe_id=values.pop('stripe_id', 'ch_finance_001'), order=order, currency='GBP', amount=values.pop('amount', Decimal('60')), occurred_at=values.pop('occurred_at', self.date), status='paid', livemode=True, **values)

    def test_completed_sales_use_completion_date_and_exclude_incomplete_orders(self):
        pending = self.order(status='pending')
        completed = self.order()
        self.complete(completed)
        report = completed_sales_report(self.window)
        self.assertEqual(report['totals']['revenue'], Decimal('60'))
        self.assertEqual(report['totals']['cogs'], Decimal('16'))
        self.assertEqual(report['totals']['gross_profit'], Decimal('44'))
        self.assertEqual(report['totals']['order_count'], 1)
        self.assertEqual(report['periods'][1]['revenue'], Decimal('60'))
        self.assertNotIn(pending.pk, [order.pk for order in report['orders']])

    def test_cash_gifts_redemptions_and_refunds_are_separate(self):
        for channel, amount, refund in [('cash', '60', '0'), ('gift_card', '60', '0'), ('gift', '0', '0'), ('stripe', '60', '20')]:
            order = self.order(payment_channel=channel, total=Decimal(amount), subtotal=Decimal(amount), refunded_amount=Decimal(refund))
            self.complete(order)
        totals = completed_sales_report(self.window)['totals']
        self.assertEqual(totals['cash'], Decimal('60'))
        self.assertEqual(totals['gift_card'], Decimal('60'))
        self.assertEqual(totals['stripe'], Decimal('40'))
        self.assertEqual(totals['revenue'], Decimal('160'))
        self.assertEqual(totals['gift_cost'], Decimal('16'))
        self.assertEqual(totals['gift_count'], 1)

    def test_missing_historical_completion_dates_are_not_invented(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(status='completed')
        totals = completed_sales_report(self.window)['totals']
        self.assertEqual(totals['revenue'], 0)
        self.assertEqual(totals['missing_completion_dates'], 1)

    def test_periods_are_calendar_months_quarters_and_years(self):
        for period, expected in [('month', 12), ('quarter', 4), ('year', 6)]:
            window = sales_window({'period': period, 'year': self.year})
            self.assertEqual(len(window['buckets']), expected)
            self.assertEqual(window['end'].year, self.year + 1)

    def test_reconciliation_distinguishes_matched_missing_and_amount_mismatch(self):
        matched = self.order(transaction_id='ch_finance_001')
        mismatch = self.order(transaction_id='ch_finance_002')
        missing = self.order(transaction_id='ch_missing')
        self.receipt(matched)
        self.receipt(mismatch, stripe_id='ch_finance_002', amount=Decimal('50'))
        report = reconciliation_report(self.window)
        self.assertEqual(report['matched_count'], 1)
        self.assertEqual(report['unsynced_count'], 1)
        self.assertIn('Amount mismatch', [entry['status'] for entry in report['entries']])
        StripeSyncRun.objects.create(start_at=self.window['start'], end_at=self.window['end'], currency='GBP', status='complete', actor=self.staff)
        report = reconciliation_report(self.window)
        self.assertIn('Missing Stripe receipt', [entry['status'] for entry in report['entries']])
        self.assertEqual(report['difference'], Decimal('-70'))

    def test_stripe_import_is_read_only_idempotent_and_currency_scoped(self):
        order = self.order(transaction_id='ch_import')
        charge = {'id': 'ch_import', 'payment_intent': 'pi_import', 'currency': 'gbp', 'amount': 6000, 'amount_refunded': 0, 'paid': True, 'captured': True, 'livemode': True, 'created': int(self.date.timestamp()), 'metadata': {}, 'balance_transaction': {'currency': 'gbp', 'fee': 150, 'net': 5850}}
        page = MagicMock()
        page.auto_paging_iter.return_value = [charge, {**charge, 'id': 'ch_euro', 'currency': 'eur'}]
        with patch('shop.sales.stripe.StripeClient') as client:
            client.return_value.v1.charges.list.return_value = page
            first = sync_stripe_transactions(self.window, self.staff)
            second = sync_stripe_transactions(self.window, self.staff)
        self.assertEqual(first.status, 'complete')
        self.assertEqual(second.transaction_count, 1)
        self.assertEqual(StripeTransaction.objects.count(), 1)
        transaction = StripeTransaction.objects.get()
        self.assertEqual(transaction.order, order)
        self.assertEqual(transaction.fee, Decimal('1.50'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 75)

    def test_failed_sync_does_not_claim_missing_payments(self):
        self.order()
        with patch('shop.sales.stripe.StripeClient', side_effect=RuntimeError('Secret error details')):
            run = sync_stripe_transactions(self.window, self.staff)
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.error_code, 'RuntimeError')
        report = reconciliation_report(self.window)
        self.assertEqual(report['unsynced_count'], 1)
        self.assertNotIn('Missing Stripe receipt', [entry['status'] for entry in report['entries']])

    def test_replenishment_budget_uses_targets_costs_and_flags_unknown_costs(self):
        self.product.stock_quantity = 3
        self.product.restock_target = 20
        self.product.save()
        budget = replenishment_budget()
        self.assertEqual(budget['total'], Decimal('136'))
        self.assertEqual(budget['items'][0]['qty_needed'], 17)

    def offline_data(self, **values):
        data = {'product': self.product, 'quantity': 3, 'payment_channel': 'cash', 'amount': Decimal('20'), 'customer_name': 'Customer', 'reference': '', 'entry_id': uuid.uuid4()}
        data.update(values)
        return data

    def test_cash_entry_deducts_stock_once_and_preserves_pennies(self):
        data = self.offline_data()
        order, created = record_offline_sale(data, self.staff)
        repeated, duplicate_created = record_offline_sale(data, self.staff)
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(repeated.pk, order.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 72)
        self.assertEqual(order.status, 'completed')
        self.assertIsNotNone(order.completed_at)
        self.assertEqual(sum(item.line_total for item in order.items.all()), Decimal('20'))
        self.assertEqual(StockMovement.objects.filter(order=order, reason='sale').count(), 1)
        self.assertEqual(StockMovement.objects.get(order=order).actor, self.staff)

    def test_free_gift_is_cost_not_revenue_and_has_gift_stock_movement(self):
        order, created = record_offline_sale(self.offline_data(payment_channel='gift', amount=Decimal('0')), self.staff)
        self.assertEqual(order.total, 0)
        self.assertEqual(StockMovement.objects.get(order=order).reason, 'gift')
        totals = completed_sales_report(self.window)['totals']
        self.assertEqual(totals['revenue'], 0)
        self.assertEqual(totals['gift_cost'], Decimal('24'))

    def test_gift_card_requires_verified_unique_redemption_reference(self):
        form_data = {'product': self.product.pk, 'quantity': 1, 'payment_channel': 'gift_card', 'amount': '30', 'customer_name': 'Customer', 'entry_id': str(uuid.uuid4())}
        self.assertFalse(OfflineSaleForm(form_data).is_valid())
        form = OfflineSaleForm({**form_data, 'reference': 'VERIFIED-REDEMPTION-001', 'redemption_verified': 'on'})
        self.assertTrue(form.is_valid(), form.errors)
        record_offline_sale(form.cleaned_data, self.staff)
        with self.assertRaises(ValueError):
            record_offline_sale(self.offline_data(payment_channel='gift_card', reference='VERIFIED-REDEMPTION-001'), self.staff)

    def test_insufficient_offline_stock_leaves_no_order_or_deduction(self):
        with self.assertRaises(ValueError):
            record_offline_sale(self.offline_data(quantity=100), self.staff)
        self.assertEqual(Order.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 75)

    def test_finance_views_charts_and_details_require_staff(self):
        endpoints = ['control_sales', 'control_sales_completed', 'control_sales_reconciliation', 'control_sales_replenishment', 'sales_revenue_chart', 'sales_receipts_chart', 'sales_replenishment_chart']
        for name in endpoints:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.client.force_login(self.staff)
        for name in endpoints:
            response = self.client.get(reverse(name), {'period': 'quarter', 'year': self.year})
            self.assertEqual(response.status_code, 200, name)

    def test_dashboard_renders_periods_and_three_finance_charts(self):
        self.client.force_login(self.staff)
        for period in ['month', 'quarter', 'year']:
            response = self.client.get(reverse('control_sales'), {'period': period, 'year': self.year})
            self.assertContains(response, 'data-dashboard-chart', count=3)
            self.assertContains(response, 'Refresh from Stripe')
            self.assertContains(response, 'Record completed entry')
            self.assertContains(response, 'No completed sales or gift costs in this period.')
            self.assertContains(response, 'No card refunds recorded for this payment period.')
            self.assertContains(response, 'No failed card payments recorded for this period.')

    def test_posting_cash_entry_through_staff_form_records_completed_stock_sale(self):
        self.client.force_login(self.staff)
        payload = {'action': 'record_offline', 'product': self.product.pk, 'quantity': 2, 'payment_channel': 'cash', 'amount': '60', 'customer_name': 'Customer', 'entry_id': str(uuid.uuid4())}
        response = self.client.post(reverse('control_sales'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.get().payment_channel, 'cash')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 73)

    def test_confirming_a_refunded_order_again_preserves_refund_state(self):
        from .services import mark_order_paid
        order = self.order(payment_status='refunded', refunded_amount=Decimal('60'), stock_deducted_at=timezone.now(), status='return')
        mark_order_paid(order)
        self.assertEqual(order.payment_status, 'refunded')
        self.assertEqual(order.status, 'return')

    def test_failed_card_attempt_does_not_match_a_paid_order(self):
        order = self.order()
        receipt = self.receipt(order)
        receipt.status = 'failed'
        receipt.save(update_fields=['status'])
        StripeSyncRun.objects.create(start_at=self.window['start'], end_at=self.window['end'], currency='GBP', status='complete')
        report = reconciliation_report(self.window)
        self.assertEqual(report['matched_count'], 0)
        self.assertIn('Missing Stripe receipt', [entry['status'] for entry in report['entries']])

    def failed_receipt(self, order=None, **values):
        receipt = self.receipt(order, **values)
        receipt.status = 'failed'
        receipt.save(update_fields=['status'])
        return receipt

    def test_refunds_reconcile_full_partial_mismatched_and_unmatched_payments(self):
        full = self.order(payment_status='refunded', refunded_amount=Decimal('60'))
        partial = self.order(payment_status='partially_refunded', refunded_amount=Decimal('20'))
        mismatch = self.order(payment_status='partially_refunded', refunded_amount=Decimal('15'))
        self.receipt(full, refunded_amount=Decimal('60'))
        self.receipt(partial, stripe_id='ch_partial', refunded_amount=Decimal('20'))
        self.receipt(mismatch, stripe_id='ch_mismatch', refunded_amount=Decimal('10'))
        self.receipt(stripe_id='ch_unmatched', refunded_amount=Decimal('5'))
        refunds = reconciliation_report(self.window)['refunds']
        self.assertEqual(refunds['stripe_total'], Decimal('95'))
        self.assertEqual(refunds['local_total'], Decimal('95'))
        self.assertEqual(refunds['difference'], 0)
        self.assertEqual(refunds['matched_count'], 2)
        self.assertEqual(refunds['review_count'], 2)
        self.assertEqual(len(refunds['entries']), 4)

    def test_unsynced_refund_is_provisional_and_not_a_match(self):
        self.order(payment_status='refunded', refunded_amount=Decimal('60'))
        refunds = reconciliation_report(self.window)['refunds']
        self.assertEqual(refunds['difference'], Decimal('-60'))
        self.assertEqual(refunds['unsynced_count'], 1)
        self.assertEqual(refunds['matched_count'], 0)
        self.assertEqual(refunds['review_count'], 0)

    def test_failure_matches_do_not_count_as_receipts_or_sales(self):
        order = self.order(payment_status='failed')
        self.failed_receipt(order)
        report = reconciliation_report(self.window)
        self.assertEqual(report['failures']['stripe_total'], Decimal('60'))
        self.assertEqual(report['failures']['local_total'], Decimal('60'))
        self.assertEqual(report['failures']['matched_count'], 1)
        self.assertEqual(report['failures']['review_count'], 0)
        self.assertEqual(report['review_count'], 0)
        self.assertEqual(report['stripe_total'], 0)
        self.assertEqual(report['local_total'], 0)
        self.assertEqual(report['entries'][0]['stripe_net'], 0)
        self.assertEqual(report['entries'][0]['local_net'], 0)
        self.assertEqual(completed_sales_report(self.window)['totals']['revenue'], 0)

    def test_multiple_failed_retries_on_a_refunded_order_are_not_extra_refunds(self):
        order = self.order(payment_status='partially_refunded', refunded_amount=Decimal('20'))
        self.failed_receipt(order, stripe_id='ch_retry1')
        self.failed_receipt(order, stripe_id='ch_retry2')
        self.receipt(order, stripe_id='ch_success', refunded_amount=Decimal('20'))
        report = reconciliation_report(self.window)
        self.assertEqual(report['failures']['stripe_count'], 2)
        self.assertEqual(report['failures']['local_count'], 0)
        self.assertEqual(report['failures']['recovered_count'], 2)
        self.assertEqual(report['failures']['review_count'], 0)
        self.assertEqual(len(report['refunds']['entries']), 1)
        self.assertEqual(report['refunds']['matched_count'], 1)
        self.assertEqual(report['stripe_total'], Decimal('40'))
        for entry in report['failures']['entries']:
            self.assertEqual(entry['stripe_net'], 0)
            self.assertEqual(entry['local_net'], 0)

    def test_failure_review_flags_unmatched_amount_and_status_mismatches(self):
        failed = self.order(payment_status='failed')
        pending = self.order(payment_status='pending')
        self.failed_receipt(stripe_id='ch_unmatched')
        self.failed_receipt(failed, stripe_id='ch_wrong_amount', amount=Decimal('50'))
        self.failed_receipt(pending, stripe_id='ch_wrong_status')
        failures = reconciliation_report(self.window)['failures']
        self.assertEqual(failures['review_count'], 3)
        self.assertEqual(failures['matched_count'], 0)
        self.assertEqual(len(failures['entries']), 3)

    def test_failed_orders_without_charges_use_sync_coverage_and_creation_date(self):
        order = self.order(payment_status='failed', payment_date=None)
        Order.objects.filter(pk=order.pk).update(created_at=self.date)
        report = reconciliation_report(self.window)
        self.assertEqual(report['failures']['unsynced_count'], 1)
        self.assertEqual(report['failures']['entries'][0]['occurred_at'], self.date)
        StripeSyncRun.objects.create(start_at=self.window['start'], end_at=self.window['end'], currency='GBP', status='complete')
        report = reconciliation_report(self.window)
        self.assertEqual(report['failures']['review_count'], 1)
        self.assertEqual(report['failures']['entries'][0]['status'], 'Missing Stripe failed attempt')

    def test_failure_scope_excludes_other_currency_test_mode_offline_and_outside_period(self):
        self.order(payment_status='failed', currency='EUR')
        self.order(payment_status='failed', stripe_session_id='cs_test_failure')
        self.order(payment_status='failed', payment_channel='cash')
        self.order(payment_status='failed', payment_date=self.window['end'])
        receipt = self.failed_receipt(stripe_id='ch_test')
        receipt.livemode = False
        receipt.save(update_fields=['livemode'])
        receipt = self.failed_receipt(stripe_id='ch_euro')
        receipt.currency = 'EUR'
        receipt.save(update_fields=['currency'])
        self.failed_receipt(stripe_id='ch_end', occurred_at=self.window['end'])
        failures = reconciliation_report(self.window)['failures']
        self.assertEqual(failures['entries'], [])
        self.assertEqual(failures['local_count'], 0)
        self.assertEqual(failures['stripe_count'], 0)

    def test_dashboard_and_filtered_details_show_refunds_and_failures(self):
        refunded = self.order(payment_status='partially_refunded', refunded_amount=Decimal('20'))
        failed = self.order(payment_status='failed')
        self.receipt(refunded, refunded_amount=Decimal('20'))
        self.failed_receipt(failed, stripe_id='ch_failure_details')
        self.client.force_login(self.staff)
        params = {'period': 'quarter', 'year': self.year}
        dashboard = self.client.get(reverse('control_sales'), params)
        self.assertContains(dashboard, 'Refund reconciliation')
        self.assertContains(dashboard, 'Payment failure reconciliation')
        for kind, transaction, excluded, column in [
            ('refunds', 'ch_finance_001', 'ch_failure_details', 'Stripe refund'),
            ('failures', 'ch_failure_details', 'ch_finance_001', 'Stripe failed amount'),
        ]:
            response = self.client.get(reverse('control_sales_reconciliation'), {**params, 'kind': kind})
            self.assertContains(response, transaction)
            self.assertNotContains(response, excluded)
            self.assertContains(response, column)
            self.assertEqual(response.context['detail_page'].paginator.count, 1)
            self.assertIn('kind=' + kind, response.context['finance_query'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('control_sales_reconciliation'), {**params, 'kind': 'failures'}).status_code, 302)

    def test_stripe_import_includes_failed_attempts_and_refund_amounts(self):
        failed = self.order(payment_status='failed', transaction_id='pi_failure')
        charges = [
            {'id': 'ch_failure', 'payment_intent': 'pi_failure', 'currency': 'gbp', 'amount': 6000, 'amount_captured': 0, 'amount_refunded': 0, 'paid': False, 'captured': False, 'status': 'failed', 'livemode': True, 'created': int(self.date.timestamp())},
            {'id': 'ch_refund', 'currency': 'gbp', 'amount': 6000, 'amount_refunded': 2000, 'paid': True, 'captured': True, 'livemode': True, 'created': int(self.date.timestamp())},
        ]
        page = MagicMock()
        page.auto_paging_iter.return_value = charges
        with patch('shop.sales.stripe.StripeClient') as client:
            client.return_value.v1.charges.list.return_value = page
            run = sync_stripe_transactions(self.window, self.staff)
        self.assertEqual(run.status, 'complete')
        self.assertEqual(StripeTransaction.objects.get(stripe_id='ch_failure').order, failed)
        report = reconciliation_report(self.window)
        self.assertEqual(report['failures']['matched_count'], 1)
        self.assertEqual(report['refunds']['stripe_total'], Decimal('20'))

    def test_failure_detail_pagination_preserves_category_and_period(self):
        for index in range(51):
            self.failed_receipt(stripe_id=f'ch_paginated_{index}')
        self.client.force_login(self.staff)
        params = {'period': 'quarter', 'year': self.year, 'kind': 'failures'}
        response = self.client.get(reverse('control_sales_reconciliation'), params)
        self.assertEqual(response.context['detail_page'].paginator.count, 51)
        self.assertEqual(len(response.context['detail_page']), 50)
        self.assertContains(response, f'period=quarter&amp;year={self.year}&amp;kind=failures&amp;page=2')
        second = self.client.get(reverse('control_sales_reconciliation'), {**params, 'page': 2})
        self.assertEqual(len(second.context['detail_page']), 1)

    def test_refund_scope_uses_payment_period_instead_of_completion_or_update_date(self):
        order = self.order(payment_status='refunded', refunded_amount=Decimal('60'))
        self.complete(order)
        Order.objects.filter(pk=order.pk).update(payment_date=self.window['end'])
        self.receipt(order, refunded_amount=Decimal('60'), occurred_at=self.window['end'])
        self.assertEqual(reconciliation_report(self.window)['refunds']['entries'], [])
        self.assertEqual(completed_sales_report(self.window)['totals']['refunds'], Decimal('60'))

    def notes_url(self, **params):
        return reverse('control_sales_reconciliation') + '?' + urlencode({'year': self.year, 'kind': 'refunds', **params})

    def test_staff_can_append_notes_without_replacing_original_reason(self):
        receipt = self.receipt(refunded_amount=Decimal('20'), refund_notes='Original reason')
        self.client.force_login(self.staff)
        page = self.client.get(self.notes_url())
        self.assertContains(page, 'Original reason')
        self.assertContains(page, '>Edit</a>')
        self.assertNotContains(page, '<textarea')
        edit = self.client.get(self.notes_url(edit_notes=receipt.pk))
        self.assertContains(edit, 'Additional notes')
        self.assertContains(edit, '<textarea')
        payload = {'action': 'append_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'Customer returned damaged item.'}
        response = self.client.post(self.notes_url(), payload, follow=True)
        self.assertContains(response, 'Additional refund notes saved.')
        self.assertContains(response, 'Customer returned damaged item.')
        self.assertContains(response, '<th>Notes</th>', html=True)
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, 'Original reason\n\n' + payload['refund_notes'])
        response = self.client.post(self.notes_url(), {**payload, 'refund_notes': ''})
        self.assertEqual(response.status_code, 200)
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, 'Original reason\n\n' + payload['refund_notes'])
        self.assertEqual(reconciliation_report(self.window)['refunds']['review_count'], 1)

    def test_refund_notes_survive_order_deletion_and_stripe_refresh(self):
        order = self.order(refunded_amount=Decimal('20'), payment_status='partially_refunded')
        receipt = self.receipt(order, refunded_amount=Decimal('20'))
        self.client.force_login(self.staff)
        self.client.post(self.notes_url(), {'action': 'append_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'Returned by customer'})
        order.delete()
        charge = {'id': receipt.stripe_id, 'currency': 'gbp', 'amount': 6000, 'amount_refunded': 2000, 'paid': True, 'captured': True, 'livemode': True, 'created': int(self.date.timestamp())}
        page = MagicMock()
        page.auto_paging_iter.return_value = [charge]
        with patch('shop.sales.stripe.StripeClient') as client:
            client.return_value.v1.charges.list.return_value = page
            self.assertEqual(sync_stripe_transactions(self.window, self.staff).status, 'complete')
        receipt.refresh_from_db()
        self.assertIsNone(receipt.order)
        self.assertEqual(receipt.refund_notes, 'Returned by customer')
        self.assertContains(self.client.get(self.notes_url()), 'Returned by customer')

    def test_notes_on_unsynced_order_copy_to_new_stripe_receipt(self):
        order = self.order(refunded_amount=Decimal('20'), transaction_id='ch_new_notes', refund_notes='Wrong size')
        self.client.force_login(self.staff)
        order.refresh_from_db()
        self.assertEqual(order.refund_notes, 'Wrong size')
        charge = {'id': 'ch_new_notes', 'currency': 'gbp', 'amount': 6000, 'amount_refunded': 2000, 'paid': True, 'captured': True, 'livemode': True, 'created': int(self.date.timestamp())}
        page = MagicMock()
        page.auto_paging_iter.return_value = [charge]
        with patch('shop.sales.stripe.StripeClient') as client:
            client.return_value.v1.charges.list.return_value = page
            sync_stripe_transactions(self.window, self.staff)
        receipt = StripeTransaction.objects.get()
        self.assertEqual(receipt.refund_notes, 'Wrong size')
        self.client.post(self.notes_url(), {'action': 'append_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'Inspection complete'})
        with patch('shop.sales.stripe.StripeClient') as client:
            client.return_value.v1.charges.list.return_value = page
            sync_stripe_transactions(self.window, self.staff)
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, 'Wrong size\n\nInspection complete')

    def test_refund_notes_validate_length_and_escape_html(self):
        receipt = self.receipt(refunded_amount=Decimal('20'), refund_notes='Existing reason')
        self.client.force_login(self.staff)
        payload = {'action': 'append_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'x' * 2001}
        response = self.client.post(self.notes_url(), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Refund notes were not saved.')
        self.assertContains(response, 'at most 2000 characters')
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, 'Existing reason')
        response = self.client.post(self.notes_url(), {**payload, 'refund_notes': '<script>alert(1)</script>'}, follow=True)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_refund_notes_require_staff_csrf_and_visible_refund_target(self):
        receipt = self.receipt(refunded_amount=Decimal('20'))
        payload = {'action': 'append_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'Unauthorized'}
        self.assertEqual(self.client.post(self.notes_url(), payload).status_code, 302)
        customer = User.objects.create_user(username='notes-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.post(self.notes_url(), payload).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(self.notes_url(), payload).status_code, 403)
        self.client.force_login(self.staff)
        response = self.client.post(self.notes_url(year=self.year - 1), payload, follow=True)
        self.assertContains(response, 'That refund is no longer on this page.')
        response = self.client.post(self.notes_url(kind='failures'), payload, follow=True)
        self.assertContains(response, 'Use Edit to add additional notes')
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, '')

    def test_refund_notes_save_returns_to_same_period_and_page(self):
        for index in range(51):
            self.receipt(stripe_id=f'ch_note_page_{index}', refunded_amount=Decimal('20'))
        self.client.force_login(self.staff)
        url = self.notes_url(period='quarter', page=2)
        page = self.client.get(url).context['detail_page']
        receipt = page[0]['receipt']
        response = self.client.post(url, {'action': 'append_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'Reason on page two'})
        self.assertRedirects(response, reverse('control_sales_reconciliation') + f'?period=quarter&year={self.year}&kind=refunds&page=2')
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, 'Reason on page two')

    def test_order_refund_requires_reason_and_copies_it_to_existing_receipt(self):
        order = self.order(payment_date=timezone.now())
        receipt = self.receipt(order)
        self.client.force_login(self.staff)
        payload = {'action': 'refund_order', 'order_id': order.pk, 'refund_amount': '20'}
        with patch('shop.views.refund_stripe_order', return_value={'id': 're_reason'}) as refund:
            response = self.client.post(reverse('control_orders'), payload)
            self.assertEqual(response.status_code, 302)
            refund.assert_not_called()
            self.client.post(reverse('control_orders'), {**payload, 'refund_notes': 'Wrong size'})
            refund.assert_called_once()
        order.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(order.refund_notes, 'Wrong size')
        self.assertEqual(receipt.refund_notes, 'Wrong size')
        self.assertEqual(order.refunded_amount, Decimal('20'))
        with patch('shop.views.refund_stripe_order', return_value={'id': 're_second'}):
            self.client.post(reverse('control_orders'), {**payload, 'refund_notes': 'Remaining item returned'})
        order.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(order.refund_notes, 'Wrong size\n\nRemaining item returned')
        self.assertEqual(receipt.refund_notes, order.refund_notes)

    def test_failed_refund_does_not_save_reason(self):
        order = self.order()
        self.client.force_login(self.staff)
        with patch('shop.views.refund_stripe_order', side_effect=RuntimeError('Refund rejected')):
            self.client.post(reverse('control_orders'), {'action': 'refund_order', 'order_id': order.pk, 'refund_notes': 'Wrong size'})
        order.refresh_from_db()
        self.assertEqual(order.refund_notes, '')
        self.assertEqual(order.refunded_amount, 0)

    def test_additional_notes_cannot_overwrite_using_old_action_or_exceed_total_limit(self):
        receipt = self.receipt(refunded_amount=Decimal('20'), refund_notes='x' * 1990)
        self.client.force_login(self.staff)
        payload = {'action': 'save_refund_notes', 'receipt_id': receipt.pk, 'refund_notes': 'Replace original'}
        response = self.client.post(self.notes_url(), payload, follow=True)
        self.assertContains(response, 'Use Edit')
        response = self.client.post(self.notes_url(), {**payload, 'action': 'append_refund_notes'})
        self.assertContains(response, 'Refund notes cannot exceed 2,000 characters in total.')
        receipt.refresh_from_db()
        self.assertEqual(receipt.refund_notes, 'x' * 1990)

    def test_staff_refresh_is_blocked_while_another_refresh_is_running(self):
        self.client.force_login(self.staff)
        cache.set('sales:stripe-sync', True, 60)
        try:
            with patch('shop.views.sync_stripe_transactions') as sync:
                response = self.client.post(reverse('control_sales'), {'action': 'sync_stripe'})
                self.assertEqual(response.status_code, 302)
                sync.assert_not_called()
        finally:
            cache.delete('sales:stripe-sync')
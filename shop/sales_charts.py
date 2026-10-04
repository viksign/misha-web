from django.conf import settings
from django.db.models.functions import Coalesce

from .dashboard import DashboardChartView
from .models import StripeTransaction
from .sales import completed_sales_report, local_paid_orders, replenishment_budget, sales_window


class SalesRevenueChartView(DashboardChartView):
    def build_chart(self):
        report = completed_sales_report(sales_window(self.request.GET))
        return {
            'labels': [row['label'] for row in report['periods']],
            'data': [[float(row[field]) for row in report['periods']] for field in ['revenue', 'cogs', 'gift_cost']],
            'providers': ['Completed revenue', 'Cost of goods', 'Free gift cost'],
            'colors': ['#286b56', '#367c96', '#bf9547'],
        }


class SalesReceiptsChartView(DashboardChartView):
    def build_chart(self):
        window = sales_window(self.request.GET)
        remote = list(StripeTransaction.objects.filter(status='paid', currency=window['currency'], livemode=settings.STRIPE_SECRET_KEY.startswith(('sk_live_', 'rk_live_')), occurred_at__gte=window['start'], occurred_at__lt=window['end']))
        local = list(local_paid_orders(window).filter(payment_channel='stripe').annotate(received_at=Coalesce('payment_date', 'paid_at', 'created_at')).filter(received_at__gte=window['start'], received_at__lt=window['end']))
        return {
            'labels': [bucket['label'] for bucket in window['buckets']],
            'data': [
                [float(sum(receipt.amount - receipt.refunded_amount for receipt in remote if bucket['start'] <= receipt.occurred_at < bucket['end'])) for bucket in window['buckets']],
                [float(sum(max(0, order.total - order.refunded_amount) for order in local if bucket['start'] <= order.received_at < bucket['end'])) for bucket in window['buckets']],
            ],
            'providers': ['Stripe receipts (cached)', 'System card receipts'],
            'colors': ['#367c96', '#bf9547'],
        }


class ReplenishmentCostChartView(DashboardChartView):
    def build_chart(self):
        entries = replenishment_budget()['items']
        labels = [entry['product'].name for entry in entries[:12]]
        costs = [float(entry['line_cost']) for entry in entries[:12]]
        if len(entries) > 12:
            labels.append('Other products')
            costs.append(float(sum(entry['line_cost'] for entry in entries[12:])))
        return {'labels': labels, 'data': [costs], 'providers': ['Replenishment cost'], 'colors': ['#bf9547']}
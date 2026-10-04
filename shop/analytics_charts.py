from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from .dashboard import DashboardChartView
from .models import IPGeolocation
from .views import _filter_analytics_events, _parse_range_boundary


class AnalyticsChartView(DashboardChartView):
    def get_context_data(self, **kwargs):
        self.events, self.filtered_user, self.start, self.end = _filter_analytics_events(self.request.GET)
        return super().get_context_data(**kwargs)


class ActivityMixChartView(AnalyticsChartView):
    def build_chart(self):
        counts = self.events.aggregate(views=Count('pk', filter=Q(event_type='page_view')), clicks=Count('pk', filter=Q(event_type='click')))
        return {'labels': ['Page views', 'Clicks'], 'data': [[counts['views'], counts['clicks']]], 'providers': ['Tracked events'], 'colors': [['#286b56', '#367c96']]}


class ConnectionsLocationChartView(AnalyticsChartView):
    def build_chart(self):
        rows = list(self.events.filter(event_type='page_view').values('ip_address').annotate(total=Count('pk')).order_by('ip_address'))
        locations = {
            record.ip_address: record.label
            for record in IPGeolocation.objects.filter(ip_address__in=[row['ip_address'] for row in rows if row['ip_address']])
        }
        grouped = {}
        for row in rows:
            location = locations.get(row['ip_address'], 'Unknown')
            counts = grouped.setdefault(location, {'connections': 0, 'unique_ips': 0})
            counts['connections'] += row['total']
            counts['unique_ips'] += bool(row['ip_address'])
        groups = sorted(grouped.items(), key=lambda item: (-item[1]['connections'], item[0]))
        palette = ['#286b56', '#367c96', '#bf9547', '#b75353', '#557b88', '#a96983', '#64864a', '#a56e43']
        return {
            'labels': [f'{location} · {counts["unique_ips"]} unique IP address{"es" if counts["unique_ips"] != 1 else ""}' for location, counts in groups],
            'data': [[counts['connections'] for location, counts in groups]],
            'providers': ['Connections (page views)'],
            'colors': [[palette[index % len(palette)] for index in range(len(groups))]],
        }


class ActivityTimelineChartView(AnalyticsChartView):
    def build_chart(self):
        bounds = self.events.order_by('created_at').values_list('created_at', flat=True)
        first, last = bounds.first(), bounds.last()
        first = _parse_range_boundary(self.start) or first
        last = _parse_range_boundary(self.end, end_of_day=True) or last
        first_date = timezone.localtime(first).date() if first else None
        last_date = timezone.localtime(last).date() if last else None
        days = max(0, (last_date - first_date).days) if first_date and last_date else 0
        trunc = TruncMonth if days > 180 else TruncWeek if days > 31 else TruncDate
        rows = list(self.events.annotate(bucket=trunc('created_at')).values('bucket').annotate(
            views=Count('pk', filter=Q(event_type='page_view')),
            clicks=Count('pk', filter=Q(event_type='click')),
        ).order_by('bucket'))
        if trunc is TruncDate and first and last:
            values = {row['bucket']: row for row in rows}
            rows = [values.get(first_date + timedelta(days=offset), {'bucket': first_date + timedelta(days=offset), 'views': 0, 'clicks': 0}) for offset in range(days + 1)]
        labels = [row['bucket'].strftime('%b %Y' if days > 180 else 'Week of %d %b %Y' if days > 31 else '%d %b %Y') for row in rows]
        return {'labels': labels, 'data': [[row['views'] for row in rows], [row['clicks'] for row in rows]], 'providers': ['Page views', 'Clicks'], 'colors': ['#286b56', '#367c96']}


class TopPagesChartView(AnalyticsChartView):
    def build_chart(self):
        rows = list(self.events.filter(event_type='page_view').values('path').annotate(total=Count('pk')).order_by('-total', 'path')[:8])
        return {'labels': ['Home' if row['path'] == '/' else row['path'] for row in rows], 'data': [[row['total'] for row in rows]], 'providers': ['Page views'], 'colors': ['#286b56']}


class ProductActivityChartView(AnalyticsChartView):
    def build_chart(self):
        rows = list(self.events.filter(product__isnull=False).values('product_id', 'product__name').annotate(total=Count('pk')).order_by('-total', 'product__name', 'product_id')[:8])
        return {'labels': [row['product__name'] for row in rows], 'data': [[row['total'] for row in rows]], 'providers': ['Tracked product events'], 'colors': ['#bf9547']}
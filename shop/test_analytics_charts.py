from datetime import timedelta
from urllib.parse import parse_qs
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AnalyticsEvent, IPGeolocation, Product


@override_settings(SECURE_SSL_REDIRECT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class AnalyticsChartTests(TestCase):
    endpoints = ['analytics_mix_chart', 'analytics_locations_chart', 'analytics_timeline_chart', 'analytics_pages_chart', 'analytics_products_chart']

    def setUp(self):
        self.staff = User.objects.create_user(username='analytics-staff', is_staff=True)
        self.customer = User.objects.create_user(username='analytics-customer')
        self.product = Product.objects.create(name='Analytics pendant', slug='analytics-pendant', sku='ANALYTICS-001', price='29.99', stock_quantity=20)
        self.today = timezone.localdate()
        self.params = {'start': (self.today - timedelta(days=2)).isoformat(), 'end': self.today.isoformat(), 'user': self.customer.pk}

    def event(self, kind, days=0, user=None, path='/'):
        event = AnalyticsEvent.objects.create(event_type=kind, path=path, product=self.product, user=user or self.customer)
        AnalyticsEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(days=days))
        return event

    def test_endpoints_require_staff(self):
        for name in self.endpoints:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.client.force_login(self.customer)
        for name in self.endpoints:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_charts_share_date_and_user_filters_and_fill_empty_days(self):
        self.event('page_view', days=2)
        self.event('page_view')
        self.event('click')
        self.event('click', days=10)
        self.event('click', user=self.staff)
        self.client.force_login(self.staff)
        mix_response = self.client.get(reverse('analytics_mix_chart'), self.params)
        self.assertEqual(mix_response['Cache-Control'], 'no-store, private')
        self.assertEqual(mix_response.json()['datasets'][0]['data'], [2, 1])
        timeline = self.client.get(reverse('analytics_timeline_chart'), self.params).json()
        self.assertEqual(len(timeline['labels']), 3)
        self.assertEqual(timeline['datasets'][0]['data'], [1, 0, 1])
        self.assertEqual(timeline['datasets'][1]['data'], [0, 0, 1])
        pages = self.client.get(reverse('analytics_pages_chart'), self.params).json()
        self.assertEqual(pages['labels'], ['Home'])
        self.assertEqual(pages['datasets'][0]['data'], [2])
        products = self.client.get(reverse('analytics_products_chart'), self.params).json()
        self.assertEqual(products['datasets'][0]['data'], [3])

    def test_empty_charts_are_valid(self):
        self.client.force_login(self.staff)
        for name in self.endpoints:
            response = self.client.get(reverse(name), self.params)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['empty'])

    def test_short_range_crossing_midnight_keeps_both_calendar_days(self):
        end = timezone.localtime(timezone.now()).replace(hour=1, minute=0, second=0, microsecond=0)
        start = (end - timedelta(days=1)).replace(hour=23)
        for created_at in [start + timedelta(minutes=30), end - timedelta(minutes=30)]:
            event = self.event('page_view')
            AnalyticsEvent.objects.filter(pk=event.pk).update(created_at=created_at)
        self.client.force_login(self.staff)
        params = {'start': start.strftime('%Y-%m-%dT%H:%M'), 'end': end.strftime('%Y-%m-%dT%H:%M')}
        data = self.client.get(reverse('analytics_timeline_chart'), params).json()
        self.assertEqual(len(data['labels']), 2)
        self.assertEqual(data['datasets'][0]['data'], [1, 1])

    def test_page_links_preserve_filters_and_replace_old_charts(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('control_analytics'), self.params)
        self.assertContains(response, 'data-dashboard-chart', count=5)
        self.assertContains(response, 'data-chart-type="doughnut"', count=2)
        self.assertContains(response, 'Connections by location')
        self.assertContains(response, 'data-chart-type="vertical-bar"', count=1)
        self.assertNotContains(response, 'class="activity-bars"', html=False)
        self.assertEqual(parse_qs(response.context['analytics_chart_query']), {key: [str(value)] for key, value in self.params.items()})

    def location_event(self, ip, **values):
        event = self.event(values.pop('kind', 'page_view'), **values)
        AnalyticsEvent.objects.filter(pk=event.pk).update(ip_address=ip)

    def test_location_connections_count_page_views_and_distinct_ips_with_shared_filters(self):
        for ip in ['192.0.2.1', '192.0.2.2']:
            IPGeolocation.objects.create(ip_address=ip, city='London', country='United Kingdom')
        IPGeolocation.objects.create(ip_address='198.51.100.1', city='Paris', country='France')
        self.location_event('192.0.2.1')
        self.location_event('192.0.2.1')
        self.location_event('192.0.2.2')
        self.location_event('198.51.100.1')
        self.location_event('192.0.2.1', kind='click')
        self.location_event('198.51.100.1', days=10)
        self.location_event('198.51.100.1', user=self.staff)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('analytics_locations_chart'), self.params)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        data = response.json()
        self.assertEqual(data['labels'], ['London, United Kingdom · 2 unique IP addresses', 'Paris, France · 1 unique IP address'])
        self.assertEqual(data['datasets'][0]['data'], [3, 1])
        self.assertEqual(len(data['datasets'][0]['backgroundColor']), 2)
        self.assertFalse(data['empty'])

    def test_location_chart_includes_unknown_missing_and_private_ips_without_remote_lookups(self):
        IPGeolocation.objects.create(ip_address='10.0.0.1', is_private=True)
        self.location_event('10.0.0.1')
        self.location_event('203.0.113.1')
        self.location_event('203.0.113.1')
        self.location_event(None)
        self.client.force_login(self.staff)
        with patch('shop.services.requests.post') as lookup:
            response = self.client.get(reverse('analytics_locations_chart'), self.params)
            lookup.assert_not_called()
        data = response.json()
        self.assertEqual(data['labels'], ['Unknown · 1 unique IP address', 'Private network · 1 unique IP address'])
        self.assertEqual(data['datasets'][0]['data'], [3, 1])

    def test_click_only_location_chart_is_empty(self):
        self.location_event('192.0.2.1', kind='click')
        self.client.force_login(self.staff)
        self.assertTrue(self.client.get(reverse('analytics_locations_chart'), self.params).json()['empty'])

    def location_filter_data(self):
        for ip, city, country in [
            ('192.0.2.1', 'London', 'United Kingdom'),
            ('192.0.2.2', 'Manchester', 'United Kingdom'),
            ('198.51.100.1', 'London', 'Canada'),
            ('198.51.100.2', 'Paris', 'France'),
        ]:
            IPGeolocation.objects.create(ip_address=ip, city=city, country=country)
            self.location_event(ip)

    def test_location_and_city_filters_apply_to_log_summaries_and_every_chart(self):
        self.location_filter_data()
        self.location_event('192.0.2.1', kind='click')
        self.location_event('192.0.2.1', days=10)
        self.location_event('192.0.2.1', user=self.staff)
        self.client.force_login(self.staff)
        params = {**self.params, 'country': '  united king  ', 'city': '  LON  '}
        response = self.client.get(reverse('control_analytics'), params)
        self.assertEqual(response.context['event_page'].paginator.count, 2)
        self.assertEqual(response.context['page_views'], 1)
        self.assertEqual(response.context['clicks'], 1)
        self.assertEqual(response.context['unique_ips'], 1)
        self.assertEqual({event.ip_address for event in response.context['events']}, {'192.0.2.1'})
        self.assertContains(response, 'data-location="London, United Kingdom"')
        self.assertContains(response, 'name="country" value="united king"')
        self.assertContains(response, 'name="city" value="LON"')
        chart_params = parse_qs(response.context['analytics_chart_query'])
        self.assertEqual(chart_params['country'], ['united king'])
        self.assertEqual(chart_params['city'], ['LON'])
        self.assertEqual(self.client.get(reverse('analytics_mix_chart'), params).json()['datasets'][0]['data'], [1, 1])
        self.assertEqual(self.client.get(reverse('analytics_locations_chart'), params).json()['datasets'][0]['data'], [1])
        self.assertEqual(self.client.get(reverse('analytics_pages_chart'), params).json()['datasets'][0]['data'], [1])
        self.assertEqual(self.client.get(reverse('analytics_products_chart'), params).json()['datasets'][0]['data'], [2])
        timeline = self.client.get(reverse('analytics_timeline_chart'), params).json()
        self.assertEqual(sum(timeline['datasets'][0]['data']), 1)
        self.assertEqual(sum(timeline['datasets'][1]['data']), 1)

    def test_country_or_city_can_be_used_independently_and_missing_matches_are_empty(self):
        self.location_filter_data()
        self.client.force_login(self.staff)
        for filters, expected in [
            ({'country': 'KINGDOM'}, 2),
            ({'city': 'london'}, 2),
            ({'country': 'Canada', 'city': 'Paris'}, 0),
            ({'city': 'Not a real city'}, 0),
        ]:
            response = self.client.get(reverse('control_analytics'), {**self.params, **filters})
            self.assertEqual(response.context['event_page'].paginator.count, expected)
        data = self.client.get(reverse('analytics_locations_chart'), {**self.params, 'city': 'Not a real city'}).json()
        self.assertTrue(data['empty'])

    def test_location_filters_and_clear_links_persist_through_pagination_and_page_size(self):
        IPGeolocation.objects.create(ip_address='192.0.2.1', city='St. John\'s', country='Antigua & Barbuda')
        for index in range(16):
            self.location_event('192.0.2.1')
        self.client.force_login(self.staff)
        params = {**self.params, 'city': 'St. John\'s', 'country': 'Antigua & Barbuda', 'page_size': 15}
        response = self.client.get(reverse('control_analytics'), params)
        query = parse_qs(response.context['analytics_pagination_query'])
        self.assertEqual(query['city'], [params['city']])
        self.assertEqual(query['country'], [params['country']])
        self.assertEqual(query['page_size'], ['15'])
        self.assertContains(response, '&amp;page=2')
        second = self.client.get(reverse('control_analytics'), {**params, 'page': 2})
        self.assertEqual(len(second.context['events']), 1)
        resized = self.client.get(reverse('control_analytics'), {**params, 'page_size': 30})
        self.assertEqual(len(resized.context['events']), 16)
        clear_dates = parse_qs(response.context['analytics_clear_dates_query'])
        self.assertEqual(clear_dates['city'], [params['city']])
        self.assertNotIn('start', clear_dates)
        clear_location = parse_qs(response.context['analytics_clear_locations_query'])
        self.assertEqual(clear_location['start'], [self.params['start']])
        self.assertEqual(clear_location['user'], [str(self.customer.pk)])
        self.assertNotIn('city', clear_location)
        self.assertNotIn('country', clear_location)

    def test_confirmed_purge_respects_location_date_and_user_filters(self):
        self.location_filter_data()
        self.location_event('192.0.2.1', days=10)
        self.location_event('192.0.2.1', user=self.staff)
        self.client.force_login(self.staff)
        params = {**self.params, 'country': 'United Kingdom', 'city': 'London'}
        response = self.client.post(reverse('control_analytics'), {**params, 'action': 'purge'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AnalyticsEvent.objects.count(), 5)
        remaining = AnalyticsEvent.objects.filter(ip_address='192.0.2.1')
        self.assertEqual(remaining.count(), 2)
        query = parse_qs(response.url.split('?', 1)[1])
        self.assertEqual(query['city'], ['London'])
        self.assertEqual(query['country'], ['United Kingdom'])

    def test_mauritius_search_finds_records_beyond_first_page(self):
        IPGeolocation.objects.create(ip_address='192.0.2.1', city='Port Louis', country='Mauritius')
        IPGeolocation.objects.create(ip_address='198.51.100.1', city='Paris', country='France')
        self.location_event('192.0.2.1', days=1)
        for index in range(35):
            self.location_event('198.51.100.1')
        self.client.force_login(self.staff)
        default = self.client.get(reverse('control_analytics'), self.params)
        self.assertEqual(default.context['event_page'].paginator.count, 36)
        self.assertNotIn('192.0.2.1', [event.ip_address for event in default.context['events']])
        params = {**self.params, 'q': '  mauritius  '}
        response = self.client.get(reverse('control_analytics'), params)
        self.assertEqual(response.context['event_page'].paginator.count, 1)
        self.assertEqual(response.context['page_views'], 1)
        self.assertContains(response, 'Port Louis, Mauritius')
        self.assertContains(response, 'Search all activity records')
        self.assertContains(response, 'name="q" value="mauritius"')
        self.assertNotContains(response, 'data-analytics-filter')
        self.assertEqual(parse_qs(response.context['analytics_chart_query'])['q'], ['mauritius'])
        self.assertEqual(parse_qs(response.context['analytics_pagination_query'])['q'], ['mauritius'])
        self.assertNotIn('q', parse_qs(response.context['analytics_clear_search_query']))
        locations = self.client.get(reverse('analytics_locations_chart'), params).json()
        self.assertEqual(locations['datasets'][0]['data'], [1])
        self.assertEqual(self.client.get(reverse('analytics_mix_chart'), params).json()['datasets'][0]['data'], [1, 0])

    def test_activity_search_matches_city_and_other_fields_and_combines_filters(self):
        self.location_filter_data()
        self.client.force_login(self.staff)
        for query, count in [('london', 2), ('192.0.2.1', 1), ('Analytics pendant', 4), ('Page view', 4), ('not-a-match', 0)]:
            response = self.client.get(reverse('control_analytics'), {**self.params, 'q': query})
            self.assertEqual(response.context['event_page'].paginator.count, count, query)
        response = self.client.get(reverse('control_analytics'), {**self.params, 'q': 'London', 'country': 'Canada'})
        self.assertEqual(response.context['event_page'].paginator.count, 1)

    def test_confirmed_purge_respects_global_activity_search(self):
        self.location_filter_data()
        self.client.force_login(self.staff)
        response = self.client.post(reverse('control_analytics'), {**self.params, 'q': 'France', 'action': 'purge'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AnalyticsEvent.objects.count(), 3)
        self.assertEqual(parse_qs(response.url.split('?', 1)[1])['q'], ['France'])
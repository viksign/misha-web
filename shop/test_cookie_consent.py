from unittest.mock import patch
from urllib.parse import quote

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.http import HttpRequest
from django.test import RequestFactory, SimpleTestCase

from .cookie_consent import cookie_preferences, has_cookie_consent
from .middleware import AnalyticsMiddleware


class CookieConsentTests(SimpleTestCase):
    def request_with_cookie(self, value=None):
        request = HttpRequest()
        request.COOKIES = {'misha_cookie_consent': quote(value)} if value is not None else {}
        return request

    def test_no_choice_means_no_optional_consent(self):
        request = self.request_with_cookie()

        self.assertIsNone(cookie_preferences(request))
        self.assertFalse(has_cookie_consent(request, 'analytics'))
        self.assertFalse(has_cookie_consent(request, 'advertising'))

    def test_preferences_are_independent_by_category(self):
        request = self.request_with_cookie('{"analytics": true, "advertising": false}')

        self.assertTrue(has_cookie_consent(request, 'analytics'))
        self.assertFalse(has_cookie_consent(request, 'advertising'))

    def test_invalid_cookie_does_not_grant_consent(self):
        request = self.request_with_cookie('not-json')

        self.assertIsNone(cookie_preferences(request))
        self.assertFalse(has_cookie_consent(request, 'analytics'))

    @patch('shop.middleware.AnalyticsEvent.objects.create')
    def test_page_views_are_not_recorded_without_analytics_consent(self, create_event):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()

        AnalyticsMiddleware(lambda _request: HttpResponse('ok'))(request)

        create_event.assert_not_called()

    @patch('shop.middleware.AnalyticsEvent.objects.create')
    def test_page_views_are_recorded_after_analytics_consent(self, create_event):
        request = RequestFactory().get(
            '/',
            HTTP_COOKIE=f'misha_cookie_consent={quote("{\"analytics\": true, \"advertising\": false}")}',
            HTTP_X_REAL_IP='203.0.113.45',
            HTTP_CF_IPCOUNTRY='GB',
            HTTP_CF_IPCITY='St.%20John%27s',
            HTTP_CF_REGION='England',
            HTTP_CF_IPLATITUDE='51.5072',
            HTTP_CF_IPLONGITUDE='-0.1276',
        )
        request.user = AnonymousUser()

        AnalyticsMiddleware(lambda _request: HttpResponse('ok'))(request)

        create_event.assert_called_once()
        self.assertEqual(create_event.call_args.kwargs['ip_address'], '203.0.113.45')
        self.assertEqual(create_event.call_args.kwargs['country'], 'United Kingdom')
        self.assertEqual(create_event.call_args.kwargs['city'], "St. John's")
        self.assertEqual(create_event.call_args.kwargs['region'], 'England')
        self.assertEqual(create_event.call_args.kwargs['latitude'], 51.5072)
        self.assertEqual(create_event.call_args.kwargs['longitude'], -0.1276)

    @patch('shop.middleware.AnalyticsEvent.objects.create')
    def test_invalid_cloudflare_coordinates_are_not_recorded(self, create_event):
        request = RequestFactory().get(
            '/',
            HTTP_COOKIE=f'misha_cookie_consent={quote("{\"analytics\": true}")}',
            HTTP_CF_IPLATITUDE='nan',
            HTTP_CF_IPLONGITUDE='181',
        )
        request.user = AnonymousUser()

        AnalyticsMiddleware(lambda _request: HttpResponse('ok'))(request)

        self.assertIsNone(create_event.call_args.kwargs['latitude'])
        self.assertIsNone(create_event.call_args.kwargs['longitude'])
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
        request = RequestFactory().get('/', HTTP_COOKIE=f'misha_cookie_consent={quote("{\"analytics\": true, \"advertising\": false}")}')
        request.user = AnonymousUser()

        AnalyticsMiddleware(lambda _request: HttpResponse('ok'))(request)

        create_event.assert_called_once()
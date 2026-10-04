import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from .assistant import contains_personal_data, gemini_chat_reply
from .views import store_assistant


class GeminiAssistantTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(GEMINI_API_KEY='')
    @patch('shop.assistant.requests.post')
    def test_missing_api_key_does_not_call_provider(self, post):
        self.assertIsNone(gemini_chat_reply('What is your delivery policy?'))
        post.assert_not_called()

    def test_detects_personal_information(self):
        self.assertTrue(contains_personal_data('Email me at customer@example.com'))
        self.assertTrue(contains_personal_data('Order reference 123456'))
        self.assertFalse(contains_personal_data('What is the price of the silver ring?'))

    @override_settings(GEMINI_API_KEY='private-test-key', GEMINI_MODEL='gemini-3.8-flash')
    @patch('shop.assistant.requests.post')
    @patch('shop.assistant.DeliveryOption.objects.filter')
    @patch('shop.assistant.Product.objects.filter')
    def test_request_uses_documented_endpoint_and_server_side_key(self, product_filter, delivery_filter, post):
        product_filter.return_value.order_by.return_value = []
        delivery_filter.return_value = []
        provider_response = post.return_value
        provider_response.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': 'I can help with that.'}]}}],
        }

        answer = gemini_chat_reply('Can you tell me about your jewellery?')

        self.assertEqual(answer, 'I can help with that.')
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent')
        self.assertEqual(kwargs['headers']['x-goog-api-key'], 'private-test-key')
        self.assertNotIn('private-test-key', args[0])
        self.assertEqual(kwargs['json']['contents'][0]['parts'][0]['text'], 'Can you tell me about your jewellery?')

    @override_settings(GEMINI_API_KEY='test-key')
    @patch('shop.views.gemini_chat_reply')
    def test_general_question_requires_consent(self, chat_reply):
        request = self.factory.post(
            '/assistant/message/',
            data=json.dumps({'message': 'Do you offer gift wrapping?'}),
            content_type='application/json',
        )

        response = store_assistant(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['needs_ai_consent'])
        chat_reply.assert_not_called()

    @override_settings(GEMINI_API_KEY='test-key')
    @patch('shop.views.gemini_rate_limited', return_value=False)
    @patch('shop.views.gemini_chat_reply', return_value='I do not have that information.')
    def test_consented_general_question_uses_provider(self, chat_reply, _rate_limit):
        request = self.factory.post(
            '/assistant/message/',
            data=json.dumps({'message': 'Do you offer gift wrapping?', 'ai_consent': True}),
            content_type='application/json',
        )

        response = store_assistant(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['ai_generated'])
        chat_reply.assert_called_once_with('Do you offer gift wrapping?')

    @override_settings(GEMINI_API_KEY='test-key')
    @patch('shop.views.gemini_chat_reply')
    def test_personal_data_never_reaches_provider(self, chat_reply):
        request = self.factory.post(
            '/assistant/message/',
            data=json.dumps({'message': 'Please email me at customer@example.com'}),
            content_type='application/json',
        )

        response = store_assistant(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['needs_contact_form'])
        chat_reply.assert_not_called()
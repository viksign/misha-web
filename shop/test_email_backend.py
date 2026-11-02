import json
from unittest.mock import Mock, patch

from django.core.mail import EmailMessage
from django.test import SimpleTestCase

from .email_backend import EmailBackend


class DirectEmailLinkTests(SimpleTestCase):
    @patch('django.core.mail.backends.smtp.EmailBackend.send_messages', return_value=1)
    def test_disables_tracking_on_all_smtp_messages_and_preserves_other_options(self, send):
        messages = [
            EmailMessage('Confirmation', 'https://mishaislandheritage.com/', to=['customer@example.com']),
            EmailMessage('Reset', 'Reset link', to=['customer@example.com'], headers={
                'x-smtpapi': json.dumps({'category': ['account'], 'filters': {'opentrack': {'settings': {'enable': 1}}, 'clicktrack': {'settings': {'enable': 1}}}}),
            }),
        ]
        self.assertEqual(EmailBackend().send_messages(messages), 1)
        self.assertEqual(json.loads(messages[0].extra_headers['X-SMTPAPI'])['filters']['clicktrack']['settings']['enable'], 0)
        options = json.loads(messages[1].extra_headers['x-smtpapi'])
        self.assertEqual(options['filters']['clicktrack']['settings']['enable'], 0)
        self.assertEqual(options['filters']['opentrack']['settings']['enable'], 1)
        self.assertEqual(options['category'], ['account'])
        self.assertNotIn('X-SMTPAPI', messages[1].extra_headers)
        send.assert_called_once_with(messages)

    @patch('django.core.mail.backends.smtp.EmailBackend.send_messages')
    def test_empty_mail_batch_does_not_open_connection(self, send):
        self.assertEqual(EmailBackend().send_messages([]), 0)
        send.assert_not_called()

    def test_invalid_provider_header_is_not_silently_dropped(self):
        message = EmailMessage('Invalid', 'Body', headers={'X-SMTPAPI': 'not json'})
        with self.assertRaises(json.JSONDecodeError):
            EmailBackend().send_messages([message])

    def test_smtp_payload_contains_direct_link_tracking_override(self):
        backend = EmailBackend()
        backend.connection = Mock()
        message = EmailMessage('Confirmation', 'https://mishaislandheritage.com/', from_email='shop@example.com', to=['customer@example.com'])
        self.assertEqual(backend.send_messages(iter([message])), 1)
        payload = backend.connection.sendmail.call_args.args[2].decode()
        self.assertIn('X-SMTPAPI: {"filters": {"clicktrack": {"settings": {"enable": 0}}}}', payload)
        self.assertIn('https://mishaislandheritage.com/', payload)

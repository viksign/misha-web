import json

from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend


class EmailBackend(SMTPEmailBackend):
    def send_messages(self, email_messages):
        email_messages = list(email_messages or [])
        if not email_messages:
            return 0
        for message in email_messages:
            header_name = next((name for name in message.extra_headers if name.lower() == 'x-smtpapi'), 'X-SMTPAPI')
            options = json.loads(message.extra_headers.get(header_name, '{}'))
            options.setdefault('filters', {}).setdefault('clicktrack', {}).setdefault('settings', {})['enable'] = 0
            message.extra_headers[header_name] = json.dumps(options)
        return super().send_messages(email_messages)

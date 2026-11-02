import logging
from smtplib import SMTPException

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shop.order_reminders import send_order_reminders


class Command(BaseCommand):
    help = 'Send overdue order reminders to configured staff/list recipients once per order status.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count due reminders without emailing or recording delivery.')

    def handle(self, *args, **options):
        try:
            count = send_order_reminders(dry_run=options['dry_run'])
        except (SMTPException, OSError, ValidationError, RuntimeError) as error:
            logging.getLogger(__name__).exception('Order reminder job failed')
            raise CommandError(str(error)) from error
        self.stdout.write(f'{timezone.now().isoformat()}: {count} reminder(s) {"due" if options["dry_run"] else "sent"}.')

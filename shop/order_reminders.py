import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import Order, OrderReminderDelivery, OrderReminderRule

logger = logging.getLogger(__name__)


def send_order_reminders(*, now=None, dry_run=False):
    now = now or timezone.now()
    sent = 0
    for rule_id in OrderReminderRule.objects.filter(enabled=True).values_list('pk', flat=True):
        rule = OrderReminderRule.objects.get(pk=rule_id)
        cutoff = now - timedelta(hours=rule.after_hours)
        candidates = Order.objects.filter(status=rule.status, status_entered_at__lte=cutoff).exclude(
            reminder_deliveries__status=rule.status,
        ).values_list('pk', flat=True)
        for order_id in candidates.iterator():
            with transaction.atomic():
                rule = OrderReminderRule.objects.select_for_update().get(pk=rule_id)
                order = Order.objects.select_for_update().get(pk=order_id)
                if not rule.enabled or order.status != rule.status or order.status_entered_at > now - timedelta(hours=rule.after_hours):
                    continue
                if OrderReminderDelivery.objects.filter(order=order, status=order.status).exists():
                    continue
                recipients = {
                    email.strip().lower() for email in rule.distribution_emails.splitlines() if email.strip()
                }
                recipients.update(
                    email.strip().lower() for email in rule.staff.filter(is_staff=True, is_active=True).exclude(email='').values_list('email', flat=True)
                )
                if not recipients:
                    raise ValidationError(f'Reminder rule {rule.pk} has no eligible recipients.')
                for email in recipients:
                    validate_email(email)
                if dry_run:
                    sent += 1
                    continue
                hours = (now - order.status_entered_at).total_seconds() / 3600
                url = settings.SITE_URL + reverse('control_orders') + '?' + urlencode({'edit': order.pk})
                message = EmailMessage(
                    subject=f'MISHA reminder: order #{order.order_reference or order.pk} is {order.get_status_display()}',
                    body=(
                        f'Order #{order.order_reference or order.pk} has remained in {order.get_status_display()} '
                        f'for {hours:.1f} hours (reminder threshold: {rule.after_hours} hours).\n\n'
                        f'Review in the staff Control Panel: {url}\n\n'
                        'This is a staff reminder only. The order has not been changed. '
                        'No customer notification has been sent.\n'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    bcc=sorted(recipients),
                    connection=get_connection(timeout=30),
                )
                if message.send(fail_silently=False) != 1:
                    raise RuntimeError(f'Email backend did not send reminder for order {order.pk}.')
                OrderReminderDelivery.objects.create(order=order, status=order.status, sent_at=now, recipients='\n'.join(sorted(recipients)))
                sent += 1
                logger.info('Sent order status reminder for order %s, status %s', order.pk, order.status)
    return sent

from urllib.parse import quote, urlencode

from django.conf import settings
from django.core import signing
from django.urls import reverse

TRACKING_LINK_SALT = 'shop.order-tracking.v1'
TRACKING_LINK_MAX_AGE = 90 * 24 * 60 * 60


def private_order_tracking_path(order):
    token = signing.dumps({'order_id': order.pk}, salt=TRACKING_LINK_SALT)
    return f'{reverse("order_tracking")}?{urlencode({"token": token})}'


def private_order_tracking_url(order):
    return f'{settings.ORDER_TRACKING_SITE_URL}{private_order_tracking_path(order)}'


def tracking_order_id(token):
    data = signing.loads(token, salt=TRACKING_LINK_SALT, max_age=TRACKING_LINK_MAX_AGE)
    if not isinstance(data, dict) or type(data.get('order_id')) is not int or data['order_id'] <= 0:
        raise signing.BadSignature('Invalid tracking link.')
    return data['order_id']

TRACKING_STAGES = (
    ('Order received', 'created_at'),
    ('Processing', 'processing_started_at'),
    ('Dispatched', 'shipped_at'),
    ('Delivered', 'completed_at'),
)
STAGE_COLORS = ('#315e48', '#719a67', '#c18a44', '#83ada1')
UPCOMING_STAGE_COLOR = '#e3e9e5'


def royalmail_tracking_url(tracking_number):
    return f'https://www.royalmail.com/track-your-item#/tracking-results/{quote(tracking_number, safe="")}'


def order_tracking_data(order):
    status_rank = {
        'pending': 0,
        'processing': 1,
        'shipped': 2,
        'completed': 3,
    }
    reached_stage = status_rank.get(order.status)
    if reached_stage is None:
        reached_stage = 0
        for index, (_, timestamp_field) in enumerate(TRACKING_STAGES):
            if getattr(order, timestamp_field):
                reached_stage = index
    else:
        reached_stage = max(
            reached_stage,
            max(
                (index for index, (_, timestamp_field) in enumerate(TRACKING_STAGES) if getattr(order, timestamp_field)),
                default=0,
            ),
        )

    steps = []
    colors = []
    for index, (label, timestamp_field) in enumerate(TRACKING_STAGES):
        timestamp = getattr(order, timestamp_field)
        is_reached = index <= reached_stage
        is_current = index == reached_stage and order.status not in ('completed', 'cancelled', 'return')
        steps.append({
            'label': label,
            'timestamp': timestamp,
            'state': 'current' if is_current else 'complete' if is_reached else 'upcoming',
            'state_label': 'Current stage' if is_current else 'Complete' if is_reached else 'Up next',
        })
        colors.append(STAGE_COLORS[index] if is_reached else UPCOMING_STAGE_COLOR)

    slices = ', '.join(
        f'{color} {index * 25}% {(index + 1) * 25}%'
        for index, color in enumerate(colors)
    )
    return {
        'steps': steps,
        'progress_percent': (reached_stage + 1) * 25,
        'pie_style': f'conic-gradient({slices})',
        'tracking_url': order.tracking_url or (
            royalmail_tracking_url(order.tracking_number) if order.tracking_number else ''
        ),
    }

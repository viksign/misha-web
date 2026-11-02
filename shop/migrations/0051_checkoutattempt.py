import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('shop', '0050_order_reminders')]

    operations = [
        migrations.CreateModel(
            name='CheckoutAttempt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('fingerprint', models.CharField(blank=True, max_length=64)),
                ('cart_snapshot', models.JSONField(default=dict)),
                ('form_data', models.JSONField(default=dict)),
                ('cart_cleared_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='checkout_attempts', to='shop.order')),
            ],
        ),
    ]

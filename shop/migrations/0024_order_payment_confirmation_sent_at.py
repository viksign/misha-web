from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0023_order_stripe_refund_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='payment_confirmation_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0021_order_discount_amount_order_refund_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='payment_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='payment_method',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='order',
            name='transaction_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
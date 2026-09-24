from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0022_order_payment_date_order_payment_method_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='stripe_refund_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
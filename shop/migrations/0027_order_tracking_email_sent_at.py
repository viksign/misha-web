from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0026_order_payment_status_choices'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='tracking_email_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
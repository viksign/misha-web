from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0027_order_tracking_email_sent_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='tracking_url',
            field=models.URLField(blank=True),
        ),
    ]

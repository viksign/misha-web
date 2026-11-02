from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0047_deliveryregion_deliveryoption_region'),
    ]

    operations = [
        migrations.AddField(
            model_name='customeraddress',
            name='address2',
            field=models.CharField(blank=True, max_length=160),
        ),
    ]

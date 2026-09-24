from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0015_order_tracking_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='length',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='product',
            name='size',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='product',
            name='technical_details',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='product',
            name='weight',
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
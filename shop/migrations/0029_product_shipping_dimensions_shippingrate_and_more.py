from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0028_order_tracking_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='shipping_box_weight_grams',
            field=models.PositiveIntegerField(default=0, help_text='Weight of this product in its jewellery box, in grams.'),
        ),
        migrations.AddField(
            model_name='product',
            name='shipping_box_length_cm',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='shipping_box_width_cm',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='shipping_box_height_cm',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
        ),
        migrations.CreateModel(
            name='ShippingPackaging',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('packaging_weight_grams', models.PositiveIntegerField(default=0)),
                ('extra_length_cm', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=7)),
                ('extra_width_cm', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=7)),
                ('extra_height_cm', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=7)),
            ],
            options={
                'verbose_name': 'shipping packaging setting',
                'verbose_name_plural': 'shipping packaging settings',
            },
        ),
        migrations.CreateModel(
            name='ShippingRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('country', models.CharField(max_length=120)),
                ('max_weight_grams', models.PositiveIntegerField()),
                ('max_length_cm', models.DecimalField(decimal_places=2, max_digits=7)),
                ('max_width_cm', models.DecimalField(decimal_places=2, max_digits=7)),
                ('max_height_cm', models.DecimalField(decimal_places=2, max_digits=7)),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('active', models.BooleanField(default=True)),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shipping_rates', to='shop.deliveryoption')),
            ],
            options={
                'ordering': ['country', 'service__sort_order', 'max_weight_grams', 'price'],
            },
        ),
    ]
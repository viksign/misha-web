from django.db import migrations, models
import django.db.models.deletion

from shop.shipping import DEFAULT_DELIVERY_REGIONS


def create_default_regions(apps, schema_editor):
    DeliveryRegion = apps.get_model('shop', 'DeliveryRegion')
    DeliveryOption = apps.get_model('shop', 'DeliveryOption')
    regions = {}
    for index, region in enumerate(DEFAULT_DELIVERY_REGIONS):
        regions[region['name']], _ = DeliveryRegion.objects.get_or_create(
            name=region['name'],
            defaults={
                'countries': '\n'.join(region['countries']),
                'is_default': region['is_default'],
                'sort_order': region['sort_order'] or index + 1,
            },
        )
    assignments = {
        'standard_uk': 'UK',
        'express_uk': 'UK',
        'standard_usa': 'USA',
        'standard_eu': 'Europe',
        'international': 'International',
    }
    for code, region_name in assignments.items():
        DeliveryOption.objects.filter(code=code, region__isnull=True).update(region=regions[region_name])


def remove_default_regions(apps, schema_editor):
    DeliveryRegion = apps.get_model('shop', 'DeliveryRegion')
    DeliveryRegion.objects.filter(
        name__in=[region['name'] for region in DEFAULT_DELIVERY_REGIONS],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0046_productoption_description'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeliveryRegion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80, unique=True)),
                ('countries', models.TextField(blank=True, help_text='One country per line, exactly as it appears in the checkout country list.')),
                ('is_default', models.BooleanField(default=False, help_text='Use this group for any country that is not listed in another group.')),
                ('active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'delivery region',
                'verbose_name_plural': 'delivery regions',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='deliveryoption',
            name='transit_time',
            field=models.CharField(blank=True, help_text='Estimated transit time shown to customers, for example "3-5 business days".', max_length=60),
        ),
        migrations.AddField(
            model_name='deliveryoption',
            name='region',
            field=models.ForeignKey(
                blank=True,
                help_text='Destination group this method is offered for. Leave blank to offer it everywhere.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='delivery_options',
                to='shop.deliveryregion',
            ),
        ),
        migrations.RunPython(create_default_regions, remove_default_regions),
    ]

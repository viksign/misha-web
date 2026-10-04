from decimal import Decimal

from django.db import migrations

def update_shipping_prices(apps, schema_editor):
    DeliveryOption = apps.get_model('shop', 'DeliveryOption')
    DeliveryOption.objects.filter(code='standard_uk').update(
        label='UK Standard Delivery',
        price=Decimal('3.99'),
        free_over=None,
    )
    DeliveryOption.objects.filter(code='international').update(
        label='International Standard Delivery (Royal Mail)',
        price=Decimal('11.99'),
        free_over=None,
    )


def restore_shipping_prices(apps, schema_editor):
    DeliveryOption = apps.get_model('shop', 'DeliveryOption')
    DeliveryOption.objects.filter(code='standard_uk').update(
        label='UK Standard delivery',
        price=Decimal('4.95'),
        free_over=Decimal('100.00'),
    )
    DeliveryOption.objects.filter(code='international').update(
        label='International delivery',
        price=Decimal('24.95'),
        free_over=None,
    )


class Migration(migrations.Migration):
    dependencies = [('shop', '0034_french_catalogue_fields')]

    operations = [
        migrations.RunPython(update_shipping_prices, restore_shipping_prices),
    ]
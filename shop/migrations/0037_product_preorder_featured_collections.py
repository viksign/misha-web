from django.db import migrations, models


def copy_collection_preorders_to_products(apps, schema_editor):
    Collection = apps.get_model('shop', 'Collection')
    Product = apps.get_model('shop', 'Product')
    database = schema_editor.connection.alias
    preorder_collection_ids = Collection.objects.using(database).filter(
        preorder_enabled=True,
    ).values_list('pk', flat=True)
    Product.objects.using(database).filter(
        collection_id__in=preorder_collection_ids,
    ).update(preorder_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0036_blockedip_securityevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='collection',
            name='featured',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='product',
            name='preorder_enabled',
            field=models.BooleanField(default=False, help_text='Allow registered customers to pre-order this product when it is out of stock.'),
        ),
        migrations.RunPython(copy_collection_preorders_to_products, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='collection',
            name='preorder_enabled',
        ),
    ]
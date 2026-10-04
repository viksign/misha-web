from django.db import migrations


def queue_paid_orders(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.using(schema_editor.connection.alias).filter(
        status='paid', payment_status='paid',
    ).update(status='pending')


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0037_product_preorder_featured_collections'),
    ]

    operations = [
        migrations.RunPython(queue_paid_orders, migrations.RunPython.noop),
    ]
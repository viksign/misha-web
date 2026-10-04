from django.db import migrations, models


def mark_existing_checkout_deductions(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.using(schema_editor.connection.alias).filter(items__isnull=False).distinct().update(stock_deducted_at=models.F('created_at'))


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0039_order_processing_assignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='stock_deducted_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(mark_existing_checkout_deductions, migrations.RunPython.noop),
    ]
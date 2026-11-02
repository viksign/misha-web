from django.db import migrations, models

KIND_NAMES = {'chain_length': 'Chain length', 'size': 'Size'}


def forwards(apps, schema_editor):
    ProductOption = apps.get_model('shop', 'ProductOption')
    CartItem = apps.get_model('shop', 'CartItem')
    for old, new in KIND_NAMES.items():
        ProductOption.objects.filter(kind=old).update(kind=new)
    for item in CartItem.objects.exclude(selection_key=''):
        ids = sorted({int(part) for part in item.selection_key.split(':') if part})
        item.selection_key = '-'.join(str(value) for value in ids)
        item.save(update_fields=['selection_key'])
    seen = set()
    default_ids = []
    for option in ProductOption.objects.filter(additional_cost=0).order_by('product_id', 'sort_order', 'pk'):
        key = (option.product_id, option.kind.casefold())
        if key not in seen:
            seen.add(key)
            default_ids.append(option.pk)
    ProductOption.objects.filter(pk__in=default_ids).update(is_default=True)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0044_productoption_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='productoption',
            name='kind',
            field=models.CharField(help_text='Option type shown to customers, e.g. Chain length, Size or Colour.', max_length=60),
        ),
        migrations.AddField(
            model_name='productoption',
            name='is_default',
            field=models.BooleanField(default=False, help_text='Preselected for customers and included in the base price.'),
        ),
        migrations.AlterField(
            model_name='cartitem',
            name='selection_key',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='options_summary',
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='productoption',
            constraint=models.CheckConstraint(condition=models.Q(('is_default', False), ('additional_cost', 0), _connector='OR'), name='product_option_default_included'),
        ),
    ]

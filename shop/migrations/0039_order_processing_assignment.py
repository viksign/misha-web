from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def remove_legacy_paid_status(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.using(schema_editor.connection.alias).filter(status='paid').update(status='pending')


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shop', '0038_paid_orders_pending_fulfilment'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='processing_started_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='shipped_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='completed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='assigned_to',
            field=models.ForeignKey(
                blank=True,
                null=True,
                limit_choices_to={'is_staff': True, 'is_active': True},
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assigned_orders',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Processing by',
            ),
        ),
        migrations.RunPython(remove_legacy_paid_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[('pending', 'Pending'), ('processing', 'Processing'), ('shipped', 'Shipped'), ('completed', 'Completed'), ('return', 'Return'), ('cancelled', 'Cancelled')],
                default='pending',
                max_length=20,
            ),
        ),
    ]
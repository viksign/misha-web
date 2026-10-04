from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0033_customdesignrequest'),
    ]

    operations = [
        migrations.AddField(model_name='collection', name='name_fr', field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name='collection', name='description_fr', field=models.TextField(blank=True)),
        migrations.AddField(model_name='product', name='name_fr', field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='product', name='description_fr', field=models.TextField(blank=True)),
        migrations.AddField(model_name='product', name='short_description_fr', field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name='product', name='material_fr', field=models.CharField(blank=True, max_length=120)),
    ]
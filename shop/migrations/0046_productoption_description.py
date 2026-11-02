from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0045_productoption_is_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='productoption',
            name='description',
            field=models.TextField(blank=True, default='', help_text='Optional guidance shown to customers when they choose this option.'),
        ),
    ]

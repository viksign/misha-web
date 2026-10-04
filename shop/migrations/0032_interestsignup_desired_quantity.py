from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0031_collection_preorder_interestsignup_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='interestsignup',
            name='desired_quantity',
            field=models.PositiveIntegerField(default=1, help_text='Number of units the customer would like to order.'),
        ),
    ]
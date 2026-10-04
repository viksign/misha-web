from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0030_interestsignup'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='collection',
            name='preorder_enabled',
            field=models.BooleanField(default=False, help_text='Allow registered customers to join the pre-order list for products in this collection.'),
        ),
        migrations.AddField(
            model_name='interestsignup',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='interest_signups', to=settings.AUTH_USER_MODEL),
        ),
    ]
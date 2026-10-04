from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0029_product_shipping_dimensions_shippingrate_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InterestSignup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254)),
                ('interest_type', models.CharField(choices=[('preorder', 'New collection pre-order'), ('restock', 'Back in stock notification')], max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('notified_at', models.DateTimeField(blank=True, null=True)),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='interest_signups', to='shop.product')),
            ],
            options={
                'ordering': ['-created_at'],
                'constraints': [models.UniqueConstraint(fields=('email', 'product', 'interest_type'), name='unique_product_interest_signup')],
            },
        ),
    ]
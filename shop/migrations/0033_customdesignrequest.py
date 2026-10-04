from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0032_interestsignup_desired_quantity'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomDesignRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('product_details', models.TextField(help_text='What would you like us to create or adapt?')),
                ('inspiration', models.TextField(blank=True, help_text='Share the story, reference, mood or inspiration behind your idea.')),
                ('material', models.CharField(blank=True, max_length=120)),
                ('budget', models.CharField(blank=True, max_length=120)),
                ('photo', models.ImageField(blank=True, null=True, upload_to='custom-design/')),
                ('status', models.CharField(choices=[('new', 'New'), ('reviewing', 'Reviewing'), ('feasible', 'Feasible'), ('not_feasible', 'Not feasible'), ('completed', 'Completed')], default='new', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_design_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='CustomDesignRequestReply',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='replies', to='shop.customdesignrequest')),
                ('staff_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['created_at']},
        ),
    ]
import django.db.models.deletion
import shop.models
from django.db import migrations, models


def seed_categories_and_tags(apps, schema_editor):
    Category = apps.get_model('shop', 'ProductCategory')
    Tag = apps.get_model('shop', 'ProductTag')
    alias = schema_editor.connection.alias
    for name, slug in (
        ('Necklaces', 'necklaces'), ('Bracelets', 'bracelets'),
        ('Rings', 'rings'), ('Earrings', 'earrings'),
        ('Chains', 'chains'), ('Other', 'other'),
    ):
        Category.objects.using(alias).get_or_create(slug=slug, defaults={'name': name})
    for name, slug in (
        ('Best seller', 'best-seller'), ("Men's wear", 'mens-wear'),
        ('Gifts for parents', 'gifts-for-parents'),
    ):
        Tag.objects.using(alias).get_or_create(slug=slug, defaults={'name': name})


def assign_existing_products(apps, schema_editor):
    alias = schema_editor.connection.alias
    Category = apps.get_model('shop', 'ProductCategory')
    Product = apps.get_model('shop', 'Product')
    other = Category.objects.using(alias).get(slug='other')
    Product.objects.using(alias).filter(category__isnull=True).update(category=other)


class Migration(migrations.Migration):
    dependencies = [('shop', '0048_customeraddress_address2')]
    operations = [
        migrations.CreateModel(
            name='ProductCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True)),
                ('slug', models.SlugField(unique=True)),
            ],
            options={'ordering': ['name'], 'verbose_name_plural': 'product categories'},
        ),
        migrations.CreateModel(
            name='ProductTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True)),
                ('slug', models.SlugField(unique=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.RunPython(seed_categories_and_tags, migrations.RunPython.noop),
        migrations.AddField(
            model_name='product', name='category',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products', to='shop.productcategory',
            ),
        ),
        migrations.RunPython(assign_existing_products, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='product', name='category',
            field=models.ForeignKey(
                default=shop.models.default_product_category,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products', to='shop.productcategory',
            ),
        ),
        migrations.AddField(
            model_name='product', name='tags',
            field=models.ManyToManyField(blank=True, related_name='products', to='shop.producttag'),
        ),
    ]

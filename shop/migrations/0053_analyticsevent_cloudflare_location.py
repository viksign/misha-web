import pycountry

from django.db import migrations, models


def normalize_country_codes(apps, schema_editor):
    analytics_event = apps.get_model('shop', 'AnalyticsEvent')
    countries = analytics_event.objects.exclude(country='').values_list('country', flat=True).distinct()
    for country_code in countries:
        if len(country_code) != 2:
            continue
        country = pycountry.countries.get(alpha_2=country_code.upper())
        if country:
            analytics_event.objects.filter(country=country_code).update(country=country.name)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0052_order_phone'),
    ]

    operations = [
        migrations.AddField(
            model_name='analyticsevent',
            name='city',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='analyticsevent',
            name='region',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='analyticsevent',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='analyticsevent',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.RunPython(normalize_country_codes, migrations.RunPython.noop),
    ]

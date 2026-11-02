from decimal import Decimal

from .models import DeliveryOption, DeliveryRegion, ShippingPackaging, ShippingRate


EUROPEAN_COUNTRIES = frozenset({
    'Albania', 'Andorra', 'Austria', 'Belarus', 'Belgium', 'Bosnia and Herzegovina',
    'Bulgaria', 'Croatia', 'Cyprus', 'Czechia', 'Denmark', 'Estonia', 'Finland',
    'France', 'Georgia', 'Germany', 'Greece', 'Hungary', 'Iceland', 'Ireland',
    'Italy', 'Latvia', 'Liechtenstein', 'Lithuania', 'Luxembourg', 'Malta',
    'Moldova', 'Monaco', 'Montenegro', 'Netherlands', 'North Macedonia', 'Norway',
    'Poland', 'Portugal', 'Romania', 'San Marino', 'Serbia', 'Slovakia', 'Slovenia',
    'Spain', 'Sweden', 'Switzerland', 'Turkey', 'Ukraine', 'Vatican City',
})

SHIPPING_TIERS = {
    'uk': {
        'countries': ['United Kingdom'],
        'service_code': 'standard_uk',
        'label': 'UK Standard Delivery',
        'transit': '3-5 business days',
        'price': '3.99',
    },
    'usa': {
        'countries': ['United States'],
        'service_code': 'international',
        'label': 'USA Standard Delivery (Royal Mail)',
        'transit': '7-15 business days',
        'price': '11.99',
    },
    'europe': {
        'countries': sorted(EUROPEAN_COUNTRIES),
        'service_code': 'international',
        'label': 'Europe Standard Delivery (Royal Mail)',
        'transit': '7-15 business days',
        'price': '9.99',
    },
    'international': {
        'countries': [],
        'service_code': 'international',
        'label': 'Canada & International Standard Delivery (Royal Mail)',
        'transit': '10-25 business days',
        'price': '11.99',
    },
}


DEFAULT_DELIVERY_REGIONS = [
    {'name': 'UK', 'countries': ['United Kingdom'], 'is_default': False, 'sort_order': 1},
    {'name': 'USA', 'countries': ['United States'], 'is_default': False, 'sort_order': 2},
    {'name': 'Europe', 'countries': sorted(EUROPEAN_COUNTRIES), 'is_default': False, 'sort_order': 3},
    {'name': 'International', 'countries': [], 'is_default': True, 'sort_order': 4},
]


def delivery_regions():
    return list(DeliveryRegion.objects.filter(active=True).prefetch_related('delivery_options'))


def delivery_region_for_country(country, regions=None):
    """Return the configured delivery region a destination country belongs to.

    Falls back to the region flagged as the catch-all group when the country is
    not listed explicitly. Returns ``None`` when no country is known or no
    region groups are configured.
    """
    regions = delivery_regions() if regions is None else regions
    if not regions or not (country or '').strip():
        return None
    for region in regions:
        if region.matches(country):
            return region
    return next((region for region in regions if region.is_default), None)


def shipping_tier_for_country(country):
    normalized_country = (country or '').strip().casefold()
    if not normalized_country:
        return None
    if normalized_country == 'united kingdom':
        return 'uk'
    if normalized_country == 'united states':
        return 'usa'
    if normalized_country in {name.casefold() for name in EUROPEAN_COUNTRIES}:
        return 'europe'
    return 'international'


def destination_shipping_price(country, service_code):
    option = DeliveryOption.objects.filter(code=service_code).first()
    if option is not None and option.region_id is not None:
        return None
    tier = shipping_tier_for_country(country)
    if tier is None:
        return None
    rule = SHIPPING_TIERS[tier]
    if service_code != rule['service_code']:
        return None
    return Decimal(rule['price'])


def checkout_shipping_configuration():
    return {
        'regions': [
            {
                'name': region.name,
                'countries': region.country_list(),
                'is_default': region.is_default,
                'services': [
                    {
                        'code': option.code,
                        'label': option.label,
                        'transit': option.transit_time,
                        'price': str(option.price),
                        'free_over': str(option.free_over) if option.free_over is not None else None,
                    }
                    for option in region.delivery_options.all()
                    if option.active
                ],
            }
            for region in delivery_regions()
        ],
        'tiers': [
            {**rule, 'tier': tier}
            for tier, rule in SHIPPING_TIERS.items()
        ],
    }


def shipping_delivery_summary():
    """Delivery methods and prices for customer-facing shipping information.

    Uses the configured region groups when an administrator has assigned
    delivery methods to them, and otherwise falls back to the standard
    destination tiers that pricing already uses.
    """
    rows = [
        {
            'label': option.label,
            'transit': option.transit_time,
            'price': option.price,
            'region': region.name,
        }
        for region in delivery_regions()
        for option in region.delivery_options.all()
        if option.active
    ]
    if rows:
        return rows
    return [
        {
            'label': rule['label'],
            'transit': rule['transit'],
            'price': Decimal(rule['price']),
            'region': tier,
        }
        for tier, rule in SHIPPING_TIERS.items()
    ]


def parcel_metrics(items, packaging=None):
    packaging = packaging or ShippingPackaging.objects.order_by('pk').first()
    if packaging is None or not items:
        return None

    weight_grams = packaging.packaging_weight_grams
    lengths = []
    widths = []
    stacked_height = packaging.extra_height_cm

    for item in items:
        product = item['product']
        quantity = int(item['quantity'])
        dimensions = (
            product.shipping_box_length_cm,
            product.shipping_box_width_cm,
            product.shipping_box_height_cm,
        )
        if product.shipping_box_weight_grams <= 0 or quantity <= 0 or any(value is None or value <= 0 for value in dimensions):
            return None
        weight_grams += product.shipping_box_weight_grams * quantity
        lengths.append(dimensions[0])
        widths.append(dimensions[1])
        stacked_height += dimensions[2] * quantity

    return {
        'weight_grams': weight_grams,
        'length_cm': max(lengths) + packaging.extra_length_cm,
        'width_cm': max(widths) + packaging.extra_width_cm,
        'height_cm': stacked_height,
    }


def matching_shipping_rate(items, country, service_code):
    parcel = parcel_metrics(items)
    if parcel is None or not country or not service_code:
        return None, parcel

    rates = ShippingRate.objects.filter(
        country__iexact=country,
        service__code=service_code,
        active=True,
        max_weight_grams__gte=parcel['weight_grams'],
        max_length_cm__gte=parcel['length_cm'],
        max_width_cm__gte=parcel['width_cm'],
        max_height_cm__gte=parcel['height_cm'],
    ).order_by('max_weight_grams', 'max_length_cm', 'max_width_cm', 'max_height_cm', 'price')
    return rates.first(), parcel


def shipping_rate_payload():
    return list(ShippingRate.objects.filter(active=True).values(
        'country',
        'service__code',
        'max_weight_grams',
        'max_length_cm',
        'max_width_cm',
        'max_height_cm',
        'price',
    ))
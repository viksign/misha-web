import re
from functools import lru_cache

import phonenumbers
import pycountry
from django.core.exceptions import ValidationError


COUNTRY_ALIASES = {
    'Brunei': 'BN',
    'Congo': 'CG',
    "Cote d'Ivoire": 'CI',
    'Democratic Republic of the Congo': 'CD',
    'North Korea': 'KP',
    'Micronesia': 'FM',
    'Palestine': 'PS',
    'South Korea': 'KR',
    'Russia': 'RU',
    'Turkey': 'TR',
    'Vatican City': 'VA',
}


@lru_cache(maxsize=1)
def phone_countries():
    from .forms import COUNTRIES

    result = {}
    for name in COUNTRIES:
        region = COUNTRY_ALIASES.get(name)
        if not region:
            region = pycountry.countries.lookup(name).alpha_2
        code = phonenumbers.country_code_for_region(region)
        if not code:
            raise ValueError(f'No telephone calling code configured for {name}.')
        example = phonenumbers.example_number_for_type(region, phonenumbers.PhoneNumberType.MOBILE)
        if example is None:
            example = phonenumbers.example_number(region)
        result[name] = {
            'region': region,
            'calling_code': f'+{code}',
            'example': phonenumbers.national_significant_number(example) if example else '',
        }
    return result


def phone_parts(value):
    number = phonenumbers.parse(value, None)
    return phonenumbers.region_code_for_number(number), phonenumbers.national_significant_number(number)


def validate_checkout_phone(region, value):
    if not re.fullmatch(r'[0-9 ()-]+', value):
        raise ValidationError('Enter the phone number without the country code, using digits only (spaces, brackets and hyphens are allowed).')
    digits = re.sub(r'[^0-9]', '', value)
    try:
        number = phonenumbers.parse(digits, region)
    except phonenumbers.NumberParseException as error:
        raise ValidationError('Enter a valid phone number for the selected telephone country.') from error
    if not phonenumbers.is_valid_number_for_region(number, region) or number.extension:
        raise ValidationError('Enter a valid phone number for the selected telephone country. Check its length and telephone country code.')
    canonical = phonenumbers.national_significant_number(number)
    if digits != canonical:
        raise ValidationError(
            'Omit the domestic dialling prefix or country code. For this number, enter %(number)s after +%(code)s.',
            params={'number': canonical, 'code': number.country_code},
        )
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)

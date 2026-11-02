from decimal import Decimal
import uuid

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from .models import Collection, CustomDesignRequest, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, DeliveryRegion, Enquiry, InterestSignup, Order, Product, ProductCategory, ProductTag, ProductOption, ShippingPackaging, ShippingRate
from .shipping import SHIPPING_TIERS, delivery_region_for_country, shipping_tier_for_country
from django.core.validators import validate_email
from .models import OrderReminderRule
import phonenumbers
from .phone import phone_countries, phone_parts, validate_checkout_phone
from .order_tracking import royalmail_tracking_url


class OrderReminderRuleForm(forms.ModelForm):
    class Meta:
        model = OrderReminderRule
        fields = ['status', 'after_hours', 'staff', 'distribution_emails', 'enabled']
        widgets = {'staff': forms.SelectMultiple(), 'distribution_emails': forms.Textarea(attrs={'rows': 4})}
        labels = {'after_hours': 'Remind after (hours)', 'staff': 'Staff recipients'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['staff'].queryset = User.objects.filter(is_staff=True, is_active=True).exclude(email='').order_by('username')
        self.fields['staff'].label_from_instance = lambda user: f'{user.get_full_name() or user.username} ({user.email})'

    def clean_distribution_emails(self):
        emails = []
        for email in self.cleaned_data['distribution_emails'].splitlines():
            email = email.strip()
            if email:
                validate_email(email)
                if email.lower() not in emails:
                    emails.append(email.lower())
        return '\n'.join(emails)

    def clean(self):
        data = super().clean()
        if data.get('enabled') and not data.get('staff') and not data.get('distribution_emails'):
            raise ValidationError('Choose staff recipients or enter a distribution-list email before enabling this reminder.')
        return data

COUNTRIES = [
    'Afghanistan', 'Albania', 'Algeria', 'Andorra', 'Angola', 'Antigua and Barbuda', 'Argentina', 'Armenia', 'Australia',
    'Austria', 'Azerbaijan', 'Bahamas', 'Bahrain', 'Bangladesh', 'Barbados', 'Belarus', 'Belgium', 'Belize', 'Benin',
    'Bhutan', 'Bolivia', 'Bosnia and Herzegovina', 'Botswana', 'Brazil', 'Brunei', 'Bulgaria', 'Burkina Faso', 'Burundi',
    'Cabo Verde', 'Cambodia', 'Cameroon', 'Canada', 'Central African Republic', 'Chad', 'Chile', 'China', 'Colombia',
    'Comoros', 'Congo', 'Costa Rica', "Cote d'Ivoire", 'Croatia', 'Cuba', 'Cyprus', 'Czechia', 'Democratic Republic of the Congo',
    'Denmark', 'Djibouti', 'Dominica', 'Dominican Republic', 'Ecuador', 'Egypt', 'El Salvador', 'Equatorial Guinea', 'Eritrea',
    'Estonia', 'Eswatini', 'Ethiopia', 'Fiji', 'Finland', 'France', 'Gabon', 'Gambia', 'Georgia', 'Germany', 'Ghana',
    'Greece', 'Grenada', 'Guatemala', 'Guinea', 'Guinea-Bissau', 'Guyana', 'Haiti', 'Honduras', 'Hungary', 'Iceland',
    'India', 'Indonesia', 'Iran', 'Iraq', 'Ireland', 'Israel', 'Italy', 'Jamaica', 'Japan', 'Jordan', 'Kazakhstan',
    'Kenya', 'Kiribati', 'Kuwait', 'Kyrgyzstan', 'Laos', 'Latvia', 'Lebanon', 'Lesotho', 'Liberia', 'Libya', 'Liechtenstein',
    'Lithuania', 'Luxembourg', 'Madagascar', 'Malawi', 'Malaysia', 'Maldives', 'Mali', 'Malta', 'Marshall Islands', 'Mauritania',
    'Mauritius', 'Mexico', 'Micronesia', 'Moldova', 'Monaco', 'Mongolia', 'Montenegro', 'Morocco', 'Mozambique', 'Myanmar',
    'Namibia', 'Nauru', 'Nepal', 'Netherlands', 'New Zealand', 'Nicaragua', 'Niger', 'Nigeria', 'North Korea', 'North Macedonia',
    'Norway', 'Oman', 'Pakistan', 'Palau', 'Palestine', 'Panama', 'Papua New Guinea', 'Paraguay', 'Peru', 'Philippines',
    'Poland', 'Portugal', 'Qatar', 'Romania', 'Russia', 'Rwanda', 'Saint Kitts and Nevis', 'Saint Lucia', 'Saint Vincent and the Grenadines',
    'Samoa', 'San Marino', 'Sao Tome and Principe', 'Saudi Arabia', 'Senegal', 'Serbia', 'Seychelles', 'Sierra Leone', 'Singapore',
    'Slovakia', 'Slovenia', 'Solomon Islands', 'Somalia', 'South Africa', 'South Korea', 'South Sudan', 'Spain', 'Sri Lanka',
    'Sudan', 'Suriname', 'Sweden', 'Switzerland', 'Syria', 'Taiwan', 'Tajikistan', 'Tanzania', 'Thailand', 'Timor-Leste',
    'Togo', 'Tonga', 'Trinidad and Tobago', 'Tunisia', 'Turkey', 'Turkmenistan', 'Tuvalu', 'Uganda', 'Ukraine', 'United Arab Emirates',
    'United Kingdom', 'United States', 'Uruguay', 'Uzbekistan', 'Vanuatu', 'Vatican City', 'Venezuela', 'Vietnam', 'Yemen',
    'Zambia', 'Zimbabwe',
]

DELIVERY_OPTIONS = [
    ('standard_uk', 'UK Standard Delivery - £3.99'),
    ('express_uk', 'UK Express Delivery - £9.95'),
    ('international', 'International Standard Delivery (Royal Mail) - £11.99'),
]

def get_delivery_options():
    defaults = [
        ('standard_uk', 'UK Standard Delivery', Decimal('3.99'), None, 1),
        ('express_uk', 'UK Express delivery', Decimal('9.95'), None, 2),
        ('international', 'International Standard Delivery (Royal Mail)', Decimal('11.99'), None, 3),
    ]
    for code, label, price, free_over, sort_order in defaults:
        DeliveryOption.objects.get_or_create(
            code=code,
            defaults={'label': label, 'price': price, 'free_over': free_over, 'sort_order': sort_order},
        )
    options = list(DeliveryOption.objects.filter(active=True).values_list('code', 'label'))
    return options or DELIVERY_OPTIONS


def get_delivery_options_for_country(country):
    options = get_delivery_options()
    if not country:
        return options
    service_codes = set(ShippingRate.objects.filter(
        country__iexact=country,
        active=True,
        service__active=True,
    ).values_list('service__code', flat=True))

    region = delivery_region_for_country(country)
    region_codes = {
        option.code for option in region.delivery_options.all() if option.active
    } if region is not None else set()
    if region_codes:
        choices = [option for option in options if option[0] in region_codes]
        narrowed = [option for option in choices if option[0] in service_codes]
        return narrowed or choices

    tier = shipping_tier_for_country(country)
    if not service_codes:
        if tier == 'uk':
            return [option for option in options if option[0] in ('standard_uk', 'express_uk')]
        if tier:
            return [('international', SHIPPING_TIERS[tier]['label'])]
        return options
    choices = [option for option in options if option[0] in service_codes]
    if tier:
        rule = SHIPPING_TIERS[tier]
        choices = [
            (code, rule['label'] if code == rule['service_code'] else label)
            for code, label in choices
        ]
    return choices

class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ['name','email','phone','subject','message']
        widgets = {'message': forms.Textarea(attrs={'rows': 6})}


class CustomDesignRequestForm(forms.ModelForm):
    class Meta:
        model = CustomDesignRequest
        fields = ['title', 'product_details', 'inspiration', 'material', 'budget', 'photo']
        widgets = {
            'product_details': forms.Textarea(attrs={'rows': 5}),
            'inspiration': forms.Textarea(attrs={'rows': 5}),
            'photo': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
        }


class InterestSignupForm(forms.ModelForm):
    class Meta:
        model = InterestSignup
        fields = ['email']
        widgets = {'email': forms.EmailInput(attrs={'placeholder': 'Your email address', 'autocomplete': 'email'})}

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class PreorderInterestForm(forms.ModelForm):
    class Meta:
        model = InterestSignup
        fields = []


class CustomerReviewForm(forms.ModelForm):
    class Meta:
        model = CustomerReview
        fields = ['name', 'email', 'rating', 'title', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 5}), 'rating': forms.Select(choices=[(n, f'{n} / 5') for n in range(5, 0, -1)])}


class ProductManagementForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'slug', 'sku', 'category', 'tags', 'collection', 'description', 'short_description', 'price', 'cost_price', 'reorder_level', 'restock_target', 'material', 'chain_style', 'colour', 'dimensions', 'weight', 'technical_details', 'stock_quantity', 'preorder_enabled', 'featured', 'active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'short_description': forms.Textarea(attrs={'rows': 2}),
            'technical_details': forms.Textarea(attrs={'rows': 5}),
            'tags': forms.SelectMultiple(),
        }
        help_texts = {'category': 'Choose one category.', 'tags': 'Choose any tags that apply. Manage categories and tags in the Control Panel.'}


class ProductCategoryManagementForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ['name', 'slug']


class ProductTagManagementForm(forms.ModelForm):
    class Meta:
        model = ProductTag
        fields = ['name', 'slug']


class CatalogueFilterForm(forms.Form):
    category = forms.ModelChoiceField(
        queryset=ProductCategory.objects.all(), to_field_name='slug',
        required=False, empty_label='All categories',
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=ProductTag.objects.all(), to_field_name='slug',
        required=False, widget=forms.SelectMultiple(attrs={'aria-describedby': 'tag-filter-help'}),
    )
    sort = forms.ChoiceField(required=False, choices=[
        ('default', 'Recommended'),
        ('price_asc', 'Price: low to high'),
        ('price_desc', 'Price: high to low'),
        ('preorder_first', 'Pre-order first'),
    ], initial='default')


class ProductCatalogueFilterForm(CatalogueFilterForm):
    sort = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tags'].widget.attrs['aria-describedby'] = 'product-filter-tag-help'


class BaseProductOptionFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        groups = {}
        position = 0
        for form in self.forms:
            if not form.has_changed() and not form.instance.pk:
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            if not form.cleaned_data.get('label'):
                continue
            kind = form.cleaned_data['kind']
            group = groups.setdefault(kind.casefold(), {'kind': kind, 'forms': []})
            form.cleaned_data['kind'] = form.instance.kind = group['kind']
            form.instance.sort_order = position
            position += 1
            group['forms'].append(form)
        labels = set()
        for key, group in groups.items():
            for form in group['forms']:
                label_key = (key, form.cleaned_data['label'].casefold())
                if label_key in labels:
                    raise ValidationError(f'“{form.cleaned_data["label"]}” is listed more than once for {group["kind"].lower()}.')
                labels.add(label_key)
            defaults = [form for form in group['forms'] if form.cleaned_data.get('is_default')]
            if len(defaults) > 1:
                raise ValidationError(f'Choose only one default {group["kind"].lower()}.')
            if not defaults:
                included = [form for form in group['forms'] if not form.cleaned_data.get('additional_cost')]
                if not included:
                    raise ValidationError(f'Add a {group["kind"].lower()} option with £0.00 extra price and tick it as the default.')
                included[0].cleaned_data['is_default'] = included[0].instance.is_default = True

    def save(self, commit=True):
        saved = super().save(commit=commit)
        if commit:
            for form in self.forms:
                if form.instance.pk and form not in self.deleted_forms:
                    ProductOption.objects.filter(pk=form.instance.pk).update(
                        kind=form.instance.kind, sort_order=form.instance.sort_order,
                        description=form.instance.description, is_default=form.instance.is_default,
                    )
        return saved


class ProductOptionForm(forms.ModelForm):
    class Meta:
        model = ProductOption
        fields = ['kind', 'label', 'description', 'additional_cost', 'is_default']
        labels = {
            'kind': 'Option type',
            'label': 'Option',
            'description': 'Guidance',
            'additional_cost': 'Extra price (£)',
            'is_default': 'Default',
        }
        widgets = {
            'kind': forms.TextInput(attrs={'list': 'product-option-kinds', 'placeholder': 'e.g. Chain length'}),
            'label': forms.TextInput(attrs={'placeholder': 'e.g. 18 inches or Medium'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Explain who this option suits or how it sits when worn'}),
            'additional_cost': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def clean_kind(self):
        kind = ' '.join(self.cleaned_data['kind'].split())
        if not kind:
            raise ValidationError('Enter an option type, e.g. Chain length, Size or Colour.')
        return kind[:1].upper() + kind[1:]

    def clean_label(self):
        return ' '.join(self.cleaned_data['label'].split())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_default') and cleaned.get('additional_cost'):
            self.add_error('additional_cost', 'The default option is included in the base price, so its extra price must be £0.00.')
        return cleaned


ProductOptionFormSet = forms.inlineformset_factory(
    Product, ProductOption, form=ProductOptionForm, formset=BaseProductOptionFormSet,
    extra=0, can_delete=True,
)


class RefundNotesForm(forms.Form):
    refund_notes = forms.CharField(
        label='Refund reason / notes', max_length=2000,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Enter the reason for the refund'}),
    )


class OfflineSaleForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(active=True).order_by('name'))
    quantity = forms.IntegerField(min_value=1, max_value=9999)
    payment_channel = forms.ChoiceField(label='Entry type', choices=[('cash', 'Cash sale'), ('gift', 'Free product gift'), ('gift_card', 'Gift-card redemption')])
    amount = forms.DecimalField(label='Total received / redeemed', min_value=0, max_digits=10, decimal_places=2, required=False)
    customer_name = forms.CharField(label='Customer / recipient', max_length=160)
    reference = forms.CharField(label='Receipt / verified redemption reference', max_length=255, required=False)
    redemption_verified = forms.BooleanField(label='Gift-card redemption verified', required=False)
    entry_id = forms.UUIDField(widget=forms.HiddenInput(), initial=uuid.uuid4)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].label_from_instance = lambda product: f'{product.name} ({product.sku}) - {product.stock_quantity} available'

    def clean(self):
        data = super().clean()
        channel = data.get('payment_channel')
        amount = data.get('amount') or Decimal('0')
        if channel == 'gift' and amount:
            self.add_error('amount', 'A free gift must have zero revenue.')
        elif channel in {'cash', 'gift_card'} and amount <= 0:
            self.add_error('amount', 'Enter the amount received or redeemed.')
        if channel == 'gift_card' and (not data.get('reference') or not data.get('redemption_verified')):
            self.add_error('reference', 'A verified redemption reference is required.')
        data['amount'] = amount
        return data


class StockManagementForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['stock_quantity', 'active']


class CollectionManagementForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ['name', 'slug', 'description', 'active', 'featured']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class DeliveryRegionManagementForm(forms.ModelForm):
    class Meta:
        model = DeliveryRegion
        fields = ['name', 'countries', 'is_default', 'sort_order', 'active']
        labels = {
            'name': 'Group name',
            'countries': 'Countries in this group',
            'is_default': 'Catch-all group',
            'sort_order': 'Display order',
        }
        help_texts = {
            'name': 'For example UK, Europe, International or Others.',
            'is_default': 'Destinations that are not listed in any group fall back to this one.',
        }
        widgets = {'countries': forms.Textarea(attrs={'rows': 8})}

    def clean_name(self):
        return self.cleaned_data['name'].strip()

    def clean_countries(self):
        known = {country.casefold(): country for country in COUNTRIES}
        names = []
        unknown = []
        for line in self.cleaned_data['countries'].splitlines():
            entry = line.strip()
            if not entry:
                continue
            match = known.get(entry.casefold())
            if match is None:
                unknown.append(entry)
            elif match not in names:
                names.append(match)
        if unknown:
            raise ValidationError('These countries are not in the checkout country list: ' + ', '.join(unknown))
        return '\n'.join(names)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('is_default'):
            duplicates = DeliveryRegion.objects.filter(is_default=True)
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error('is_default', f'{duplicates.first().name} is already the catch-all group.')
        return cleaned_data


class DeliveryOptionManagementForm(forms.ModelForm):
    class Meta:
        model = DeliveryOption
        fields = ['code', 'label', 'region', 'transit_time', 'price', 'free_over', 'active', 'sort_order']
        labels = {'region': 'Destination group', 'transit_time': 'Estimated transit time'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['region'].queryset = DeliveryRegion.objects.all()
        self.fields['region'].empty_label = 'All destinations (ungrouped)'


class ShippingPackagingManagementForm(forms.ModelForm):
    class Meta:
        model = ShippingPackaging
        fields = ['packaging_weight_grams', 'extra_length_cm', 'extra_width_cm', 'extra_height_cm']
        labels = {
            'packaging_weight_grams': 'Outer packaging weight (g)',
            'extra_length_cm': 'Length allowance (cm)',
            'extra_width_cm': 'Width allowance (cm)',
            'extra_height_cm': 'Height allowance (cm)',
        }
        help_texts = {
            'extra_length_cm': 'Added to the longest jewellery-box length.',
            'extra_width_cm': 'Added to the widest jewellery-box width.',
            'extra_height_cm': 'Added after jewellery boxes are stacked.',
        }


class ShippingRateManagementForm(forms.ModelForm):
    class Meta:
        model = ShippingRate
        fields = ['country', 'service', 'max_weight_grams', 'max_length_cm', 'max_width_cm', 'max_height_cm', 'price', 'active']
        labels = {
            'max_weight_grams': 'Maximum parcel weight (g)',
            'max_length_cm': 'Maximum length (cm)',
            'max_width_cm': 'Maximum width (cm)',
            'max_height_cm': 'Maximum height (cm)',
        }
        help_texts = {
            'country': 'Use the country name shown at checkout, for example United Kingdom.',
            'price': 'Enter the Royal Mail charge for this country, service and parcel limit.',
        }


class ProductShippingForm(forms.ModelForm):
    shipping_box_weight_grams = forms.IntegerField(min_value=0, required=True, label='Box weight (g)')
    shipping_box_length_cm = forms.DecimalField(min_value=0, max_digits=7, decimal_places=2, required=False, label='Box length (cm)')
    shipping_box_width_cm = forms.DecimalField(min_value=0, max_digits=7, decimal_places=2, required=False, label='Box width (cm)')
    shipping_box_height_cm = forms.DecimalField(min_value=0, max_digits=7, decimal_places=2, required=False, label='Box height (cm)')

    class Meta:
        model = Product
        fields = ['shipping_box_weight_grams', 'shipping_box_length_cm', 'shipping_box_width_cm', 'shipping_box_height_cm']


class OrderManagementForm(forms.ModelForm):
    discount_amount = forms.DecimalField(label='Discount amount', min_value=0, required=False)

    class Meta:
        model = Order
        fields = ['status', 'tracking_number', 'tracking_url', 'assigned_to', 'payment_status', 'delivery_method', 'delivery_cost', 'discount_amount']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].label_from_instance = lambda staff: staff.get_full_name().strip() or f'Staff #{staff.pk} (name not set)'
        if not self.is_bound and not self.instance.assigned_to_id and user and user.is_active and user.is_staff:
            self.initial['assigned_to'] = user.pk
        self.fields['payment_status'].disabled = True
        self.fields['payment_status'].help_text = 'Updated automatically by Stripe.'
        self.fields['tracking_number'].help_text = 'Enter or paste the Royal Mail tracking number. The tracking URL fills automatically.'
        self.fields['tracking_number'].widget.attrs.update({'autocomplete': 'off', 'placeholder': 'Enter tracking number'})
        self.fields['tracking_url'].help_text = 'Automatically generated for Royal Mail. You can replace it for another carrier.'
        self.fields['tracking_url'].widget.attrs['placeholder'] = royalmail_tracking_url('TRACKING_NUMBER')
        if not self.is_bound and self.instance.tracking_number and not self.instance.tracking_url:
            self.initial['tracking_url'] = royalmail_tracking_url(self.instance.tracking_number.strip())

    def clean(self):
        cleaned = super().clean()
        number = cleaned.get('tracking_number', '')
        url = cleaned.get('tracking_url', '')
        previous_url = royalmail_tracking_url(self.instance.tracking_number.strip()) if self.instance.tracking_number else ''
        if 'tracking_url' in cleaned and (not url or url == previous_url):
            cleaned['tracking_url'] = royalmail_tracking_url(number) if number else ''
        return cleaned

class CheckoutForm(forms.ModelForm):
    phone_country = forms.ChoiceField(
        label='Telephone country code',
        widget=forms.Select(attrs={'autocomplete': 'tel-country-code', 'aria-describedby': 'checkout-phone-help'}),
    )
    phone = forms.CharField(
        label='Phone number', max_length=30,
        widget=forms.TextInput(attrs={'type': 'tel', 'inputmode': 'tel', 'autocomplete': 'tel-national', 'placeholder': 'Enter your phone number without the leading 0', 'pattern': r'[0-9 \(\)\-]+', 'aria-describedby': 'checkout-phone-help'}),
    )
    shipping_method = forms.ChoiceField(label='Delivery method', choices=DELIVERY_OPTIONS)
    ship_to_different_address = forms.BooleanField(label='Use a different shipping address', required=False, initial=False)
    shipping_address = forms.ChoiceField(label='Shipping address', required=False)
    billing_name = forms.CharField(label='Billing full name', max_length=150)
    shipping_name = forms.CharField(label='Shipping full name', required=False, max_length=150, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    billing_house_flat = forms.CharField(label='Billing House or Number:', required=False, max_length=30, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    billing_address1 = forms.CharField(label='Billing Address Line 1', required=False, max_length=160, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    billing_address2 = forms.CharField(label='Billing Address Line 2', required=False, max_length=160)
    billing_city = forms.CharField(label='Billing City', required=False, max_length=100, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    billing_postcode = forms.CharField(label='Billing Postcode', required=False, max_length=30, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    billing_country = forms.ChoiceField(label='Billing Country', required=False, widget=forms.Select(attrs={'data-required-address': 'true'}))
    shipping_house_flat = forms.CharField(label='Shipping House or Number:', required=False, max_length=30, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    shipping_address1 = forms.CharField(label='Shipping Address Line 1', required=False, max_length=160, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    shipping_address2 = forms.CharField(label='Shipping Address Line 2', required=False, max_length=160)
    shipping_city = forms.CharField(label='Shipping City', required=False, max_length=100, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    shipping_postcode = forms.CharField(label='Shipping Postcode', required=False, max_length=30, widget=forms.TextInput(attrs={'data-required-address': 'true'}))
    shipping_country = forms.ChoiceField(label='Shipping Country', required=False, widget=forms.Select(attrs={'data-required-address': 'true'}))
    save_shipping_address = forms.BooleanField(label='Save this shipping address', required=False)

    class Meta:
        model = Order
        fields = ['email','phone','shipping_name','shipping_address1','shipping_address2','shipping_city','shipping_postcode','shipping_country']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        countries = phone_countries()
        self.fields['phone_country'].choices = [('', 'Select telephone country')] + [
            (details['region'], f'{name} ({details["calling_code"]})')
            for name, details in countries.items()
        ]
        if not self.is_bound:
            default_country = countries.get(self.initial.get('billing_country', ''))
            if default_country:
                self.initial.setdefault('phone_country', default_country['region'])
            if str(self.initial.get('phone', '')).startswith('+'):
                try:
                    region, national = phone_parts(self.initial['phone'])
                except phonenumbers.NumberParseException:
                    pass
                else:
                    self.initial['phone_country'] = region
                    self.initial['phone'] = national
        shipping_choices = [('', 'Add a new shipping address')]
        shipping_addresses = {}
        if user and user.is_authenticated:
            shipping_addresses = {
                str(address.pk): address
                for address in CustomerAddress.objects.filter(user=user, address_type='shipping')
            }
            shipping_choices += [(pk, address.label or str(address)) for pk, address in shipping_addresses.items()]
        self.fields['shipping_address'].choices = shipping_choices
        self.fields['billing_country'].choices = [('', 'Select a country or region')] + [(country, country) for country in COUNTRIES]
        self.fields['shipping_country'].choices = [('', 'Select a country or region')] + [(country, country) for country in COUNTRIES]
        address_data = self.data if self.is_bound else self.initial
        ship_different = address_data.get('ship_to_different_address') in (True, 'on', 'true', '1')
        country_field = 'shipping_country' if ship_different else 'billing_country'
        destination_country = address_data.get(country_field, '')
        saved_address = shipping_addresses.get(str(address_data.get('shipping_address', '')))
        if ship_different and saved_address:
            destination_country = saved_address.country
        self.fields['shipping_method'].choices = get_delivery_options_for_country(destination_country)
        shipping_address_is_saved = saved_address is not None
        billing_required_fields = (
            'billing_house_flat', 'billing_address1', 'billing_city', 'billing_postcode', 'billing_country',
        )
        shipping_required_fields = (
            'shipping_name', 'shipping_house_flat', 'shipping_address1', 'shipping_city', 'shipping_postcode', 'shipping_country',
        )
        for field_name in billing_required_fields:
            self.fields[field_name].required = True
        for field_name in shipping_required_fields:
            self.fields[field_name].required = ship_different and not shipping_address_is_saved
        self.fields['shipping_address2'].required = False
        self.fields['email'].widget.attrs.update({'autocomplete': 'email', 'inputmode': 'email'})
        for prefix in ('billing', 'shipping'):
            for suffix, autocomplete in (
                ('name', 'name'),
                ('address1', 'address-line1'),
                ('address2', 'address-line2'),
                ('city', 'address-level2'),
                ('postcode', 'postal-code'),
                ('country', 'country-name'),
            ):
                self.fields[f'{prefix}_{suffix}'].widget.attrs['autocomplete'] = f'section-{prefix} {prefix} {autocomplete}'
        self.fields['ship_to_different_address'].initial = False
        self.order_fields([
            'email', 'phone_country', 'phone', 'billing_name', 'billing_house_flat', 'billing_address1',
            'billing_address2', 'billing_city', 'billing_postcode', 'billing_country',
            'ship_to_different_address', 'shipping_address', 'shipping_name', 'shipping_house_flat',
            'shipping_address1', 'shipping_address2', 'shipping_city', 'shipping_postcode', 'shipping_country',
            'save_shipping_address', 'shipping_method',
        ])

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('phone_country') and cleaned_data.get('phone'):
            try:
                cleaned_data['phone'] = validate_checkout_phone(cleaned_data['phone_country'], cleaned_data['phone'])
            except ValidationError as error:
                self.add_error('phone', error)
        return cleaned_data


class GuestOrderTrackingForm(forms.Form):
    email = forms.EmailField(label='Email address')
    order_number = forms.CharField(label='Order number', max_length=32)
    postcode = forms.CharField(label='Delivery postcode', max_length=30)

    def clean_order_number(self):
        return self.cleaned_data['order_number'].strip().removeprefix('#').strip()

    def clean_postcode(self):
        return self.cleaned_data['postcode'].strip()


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['email', 'password1', 'password2']

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(username=email).exists():
            raise ValidationError('An account with that email address already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email'].strip().lower()
        user.email = user.username
        if commit:
            user.save()
        return user


class EmailAuthenticationForm(forms.Form):
    email = forms.EmailField(label='Email address')
    password = forms.CharField(label='Password', strip=False, widget=forms.PasswordInput)

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        password = cleaned_data.get('password')
        if email and password:
            self.user_cache = authenticate(
                self.request,
                username=email.strip().lower(),
                password=password,
            )
            if self.user_cache is None:
                raise ValidationError('Please enter a valid email address and password.')
            if not self.user_cache.is_active:
                raise ValidationError('This account is inactive.')
        return cleaned_data

    def get_user(self):
        return self.user_cache


class CustomerProfileForm(forms.ModelForm):
    email = forms.EmailField(label='Email address')
    address = forms.CharField(label='Address Line 2', max_length=160, required=False)
    title = forms.ChoiceField(label='Title', required=False, choices=CustomerProfile.TITLE_CHOICES)
    first_name = forms.CharField(label='First name', max_length=150)
    last_name = forms.CharField(label='Last name', max_length=150)
    phone = forms.CharField(
        label='Phone number',
        max_length=16,
        required=False,
        validators=[RegexValidator(
            regex=r'^\+[1-9]\d{7,14}$',
            message='Use E.164 format, for example +447911123456.',
        )],
        help_text='Use international format: + country code and number, with no spaces.',
    )

    class Meta:
        model = CustomerProfile
        fields = ['title', 'first_name', 'last_name', 'email', 'phone', 'house_number', 'street_name', 'address', 'city', 'postcode', 'country']
        labels = {'address': 'Address Line 2'}
        widgets = {
            'address': forms.TextInput(),
            'title': forms.Select(choices=CustomerProfile.TITLE_CHOICES),
            'country': forms.Select(choices=[('', 'Select your country')] + [(country, country) for country in COUNTRIES]),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].initial = self.instance.title
        self.fields['email'].initial = self.instance.user.email
        self.fields['first_name'].initial = self.instance.user.first_name
        self.fields['last_name'].initial = self.instance.user.last_name
        billing = CustomerAddress.objects.filter(user=self.instance.user, address_type='billing').first()
        if billing:
            for name in ('house_number', 'street_name', 'city', 'postcode', 'country'):
                self.initial[name] = getattr(billing, name)
            self.initial['address'] = billing.address2

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        duplicate = User.objects.filter(username=email).exclude(pk=self.instance.user_id).exists()
        if duplicate:
            raise ValidationError('That email address is already in use.')
        return email

    def save(self, commit=True):
        profile = super().save(commit=False)
        email = self.cleaned_data['email']
        profile.user.email = email
        profile.user.username = email
        profile.user.first_name = self.cleaned_data['first_name'].strip()
        profile.user.last_name = self.cleaned_data['last_name'].strip()
        if commit:
            profile.user.save(update_fields=['email', 'username', 'first_name', 'last_name'])
            profile.save()
        return profile


class CustomerShippingAddressForm(forms.ModelForm):
    class Meta:
        model = CustomerAddress
        fields = ['label', 'house_number', 'street_name', 'address2', 'city', 'postcode', 'country']
        labels = {
            'house_number': 'House or Number',
            'street_name': 'Address Line 1',
            'address2': 'Address Line 2',
        }
        widgets = {
            'country': forms.Select(choices=[('', 'Select a country or region')] + [(country, country) for country in COUNTRIES]),
        }

    def clean_country(self):
        country = self.cleaned_data['country']
        if country not in COUNTRIES:
            raise ValidationError('Select a valid country or region.')
        return country

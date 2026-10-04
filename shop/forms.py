from decimal import Decimal
import uuid

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from .models import Collection, CustomDesignRequest, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, Enquiry, InterestSignup, Order, Product, ShippingPackaging, ShippingRate
from .shipping import SHIPPING_TIERS, shipping_tier_for_country

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
        fields = ['name', 'slug', 'sku', 'collection', 'description', 'short_description', 'price', 'compare_at_price', 'cost_price', 'reorder_level', 'restock_target', 'material', 'dimensions', 'size', 'length', 'weight', 'shipping_box_weight_grams', 'shipping_box_length_cm', 'shipping_box_width_cm', 'shipping_box_height_cm', 'technical_details', 'stock_quantity', 'preorder_enabled', 'featured', 'active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'short_description': forms.Textarea(attrs={'rows': 2}),
            'technical_details': forms.Textarea(attrs={'rows': 5}),
        }


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


class DeliveryOptionManagementForm(forms.ModelForm):
    class Meta:
        model = DeliveryOption
        fields = ['code', 'label', 'price', 'free_over', 'active', 'sort_order']


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
        fields = ['status', 'assigned_to', 'payment_status', 'delivery_method', 'delivery_cost', 'discount_amount', 'tracking_number', 'tracking_url']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].label_from_instance = lambda staff: staff.get_full_name().strip() or f'Staff #{staff.pk} (name not set)'
        if not self.is_bound and not self.instance.assigned_to_id and user and user.is_active and user.is_staff:
            self.initial['assigned_to'] = user.pk
        self.fields['payment_status'].disabled = True
        self.fields['payment_status'].help_text = 'Updated automatically by Stripe.'

class CheckoutForm(forms.ModelForm):
    shipping_method = forms.ChoiceField(label='Delivery method', choices=DELIVERY_OPTIONS)
    ship_to_different_address = forms.BooleanField(label='Use a different shipping address', required=False, initial=False)
    billing_address = forms.ChoiceField(label='Billing address', required=False)
    shipping_address = forms.ChoiceField(label='Shipping address', required=False)
    billing_name = forms.CharField(label='Billing name', required=False)
    billing_address1 = forms.CharField(label='Billing house number and street', required=False)
    billing_address2 = forms.CharField(label='Billing address line 2', required=False)
    billing_city = forms.CharField(label='Billing city', required=False)
    billing_postcode = forms.CharField(label='Billing postcode', required=False)
    billing_country = forms.ChoiceField(label='Billing country', required=False)
    save_shipping_address = forms.BooleanField(label='Save this shipping address', required=False)

    class Meta:
        model = Order
        fields = ['email','shipping_name','shipping_address1','shipping_address2','shipping_city','shipping_postcode','shipping_country']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        billing_choices = [('', 'Add a new billing address')]
        shipping_choices = [('', 'Add a new shipping address')]
        if user and user.is_authenticated:
            billing_choices += [(str(address.pk), address.label or str(address)) for address in CustomerAddress.objects.filter(user=user, address_type='billing')]
            shipping_choices += [(str(address.pk), address.label or str(address)) for address in CustomerAddress.objects.filter(user=user, address_type='shipping')]
        self.fields['billing_address'].choices = billing_choices
        self.fields['shipping_address'].choices = shipping_choices
        self.fields['billing_country'].choices = [('', 'Select a billing country')] + [(country, country) for country in COUNTRIES]
        self.fields['shipping_country'].choices = [('', 'Select a shipping country')] + [(country, country) for country in COUNTRIES]
        address_data = self.data if self.is_bound else self.initial
        ship_different = address_data.get('ship_to_different_address') in (True, 'on', 'true', '1')
        address_type = 'shipping' if ship_different else 'billing'
        address_field = 'shipping_address' if ship_different else 'billing_address'
        country_field = 'shipping_country' if ship_different else 'billing_country'
        destination_country = address_data.get(country_field, '')
        saved_address_id = address_data.get(address_field)
        if user and user.is_authenticated and saved_address_id:
            saved_address = CustomerAddress.objects.filter(
                pk=saved_address_id,
                user=user,
                address_type=address_type,
            ).first()
            if saved_address:
                destination_country = saved_address.country
        self.fields['shipping_method'].choices = get_delivery_options_for_country(destination_country)
        for field_name in (
            'shipping_name', 'shipping_address1', 'shipping_address2', 'shipping_city',
            'shipping_postcode', 'shipping_country', 'billing_name', 'billing_address1',
            'billing_address2', 'billing_city', 'billing_postcode', 'billing_country',
        ):
            self.fields[field_name].required = False
        self.fields['ship_to_different_address'].initial = False
        self.order_fields([
            'email', 'shipping_method', 'billing_address', 'billing_name', 'billing_address1',
            'billing_address2', 'billing_city', 'billing_postcode', 'billing_country',
            'ship_to_different_address', 'shipping_address', 'shipping_name',
            'shipping_address1', 'shipping_address2', 'shipping_city', 'shipping_postcode',
            'shipping_country', 'save_shipping_address',
        ])

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('billing_address'):
            for field_name in ('billing_name', 'billing_address1', 'billing_city', 'billing_postcode', 'billing_country'):
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, 'Enter a billing address or choose a saved billing address.')
        if cleaned_data.get('ship_to_different_address') and not cleaned_data.get('shipping_address'):
            for field_name in ('shipping_name', 'shipping_address1', 'shipping_city', 'shipping_postcode', 'shipping_country'):
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, 'Enter a shipping address or choose a saved shipping address.')
        return cleaned_data


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
        fields = ['title', 'first_name', 'last_name', 'email', 'phone', 'house_number', 'street_name', 'city', 'postcode', 'country']
        widgets = {
            'title': forms.Select(choices=CustomerProfile.TITLE_CHOICES),
            'country': forms.Select(choices=[('', 'Select your country')] + [(country, country) for country in COUNTRIES]),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].initial = self.instance.title
        self.fields['email'].initial = self.instance.user.email
        self.fields['first_name'].initial = self.instance.user.first_name
        self.fields['last_name'].initial = self.instance.user.last_name

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

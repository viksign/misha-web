from decimal import Decimal

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from .models import Collection, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, Enquiry, Order, Product

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
    ('standard_uk', 'UK Standard delivery - £4.95'),
    ('express_uk', 'UK Express delivery - £9.95'),
    ('international', 'International delivery - £24.95'),
]

def get_delivery_options():
    defaults = [
        ('standard_uk', 'UK Standard delivery', Decimal('4.95'), Decimal('100.00'), 1),
        ('express_uk', 'UK Express delivery', Decimal('9.95'), None, 2),
        ('international', 'International delivery', Decimal('24.95'), None, 3),
    ]
    for code, label, price, free_over, sort_order in defaults:
        DeliveryOption.objects.get_or_create(
            code=code,
            defaults={'label': label, 'price': price, 'free_over': free_over, 'sort_order': sort_order},
        )
    options = list(DeliveryOption.objects.filter(active=True).values_list('code', 'label'))
    return options or DELIVERY_OPTIONS

class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ['name','email','phone','subject','message']
        widgets = {'message': forms.Textarea(attrs={'rows': 6})}


class CustomerReviewForm(forms.ModelForm):
    class Meta:
        model = CustomerReview
        fields = ['name', 'email', 'rating', 'title', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 5}), 'rating': forms.Select(choices=[(n, f'{n} / 5') for n in range(5, 0, -1)])}


class ProductManagementForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'slug', 'sku', 'collection', 'description', 'short_description', 'price', 'compare_at_price', 'cost_price', 'reorder_level', 'restock_target', 'material', 'dimensions', 'size', 'length', 'weight', 'technical_details', 'stock_quantity', 'featured', 'active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'short_description': forms.Textarea(attrs={'rows': 2}),
            'technical_details': forms.Textarea(attrs={'rows': 5}),
        }


class StockManagementForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['stock_quantity', 'active']


class CollectionManagementForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ['name', 'slug', 'description', 'active']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class DeliveryOptionManagementForm(forms.ModelForm):
    class Meta:
        model = DeliveryOption
        fields = ['code', 'label', 'price', 'free_over', 'active', 'sort_order']


class OrderManagementForm(forms.ModelForm):
    discount_amount = forms.DecimalField(label='Discount amount', min_value=0, required=False)

    class Meta:
        model = Order
        fields = ['status', 'payment_status', 'delivery_method', 'delivery_cost', 'discount_amount', 'tracking_number']

class CheckoutForm(forms.ModelForm):
    shipping_method = forms.ChoiceField(label='Delivery method', choices=DELIVERY_OPTIONS)
    same_as_shipping = forms.BooleanField(label='Use shipping address for billing', required=False, initial=True)
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
        self.fields['shipping_method'].choices = get_delivery_options()
        if user and user.is_authenticated:
            for field_name in ('shipping_name', 'shipping_address1', 'shipping_city', 'shipping_postcode', 'shipping_country'):
                self.fields[field_name].required = False
            self.order_fields(['email', 'shipping_method', 'shipping_address', 'shipping_name', 'shipping_address1', 'shipping_address2', 'shipping_city', 'shipping_postcode', 'shipping_country', 'same_as_shipping', 'billing_address', 'billing_name', 'billing_address1', 'billing_address2', 'billing_city', 'billing_postcode', 'billing_country', 'save_shipping_address'])
        else:
            self.order_fields(['email', 'shipping_method', 'shipping_address', 'shipping_name', 'shipping_address1', 'shipping_address2', 'shipping_city', 'shipping_postcode', 'shipping_country', 'same_as_shipping', 'billing_address', 'billing_name', 'billing_address1', 'billing_address2', 'billing_city', 'billing_postcode', 'billing_country', 'save_shipping_address'])

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('shipping_address'):
            for field_name in ('shipping_name', 'shipping_address1', 'shipping_city', 'shipping_postcode', 'shipping_country'):
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, 'Enter a new shipping address or choose a saved address.')
        if not cleaned_data.get('same_as_shipping') and not cleaned_data.get('billing_address'):
            for field_name in ('billing_name', 'billing_address1', 'billing_city', 'billing_postcode', 'billing_country'):
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, 'Enter a new billing address or choose a saved address.')
        return cleaned_data


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['email', 'password1', 'password2']

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

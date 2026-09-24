from decimal import Decimal
from django.conf import settings
from django.db import models
from django.urls import reverse

class Collection(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='collections/', blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self): return self.name
    def get_absolute_url(self): return reverse('collection', args=[self.slug])

class Product(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    sku = models.CharField(max_length=60, unique=True)
    collection = models.ForeignKey(Collection, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    description = models.TextField()
    short_description = models.CharField(max_length=300, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'), help_text='Unit cost to source/manufacture this piece, used for margin and replenishment budgeting.')
    reorder_level = models.PositiveIntegerField(default=5, help_text='Reorder when stock falls to or below this quantity.')
    restock_target = models.PositiveIntegerField(default=20, help_text='Target stock quantity to replenish up to.')
    material = models.CharField(max_length=120, blank=True)
    dimensions = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=120, blank=True)
    length = models.CharField(max_length=120, blank=True)
    weight = models.CharField(max_length=120, blank=True)
    technical_details = models.TextField(blank=True)
    stock_quantity = models.PositiveIntegerField(default=0)
    featured = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-featured', '-created_at']

    def __str__(self): return self.name
    def get_absolute_url(self): return reverse('product_detail', args=[self.slug])
    @property
    def in_stock(self): return self.stock_quantity > 0
    @property
    def primary_image(self): return self.images.filter(is_primary=True).first() or self.images.first()


class DeliveryOption(models.Model):
    code = models.CharField(max_length=30, unique=True)
    label = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    free_over = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self): return self.label

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/')
    alt_text = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self): return f'{self.product.name} image'


class CartItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'product'], name='unique_user_cart_product')]
        ordering = ['created_at']

    def __str__(self): return f'{self.user} - {self.product} ({self.quantity})'


class CustomerProfile(models.Model):
    TITLE_CHOICES = [
        ('', 'Select a title'),
        ('Mr', 'Mr'),
        ('Mrs', 'Mrs'),
        ('Ms', 'Ms'),
        ('Mx', 'Mx'),
        ('Dr', 'Dr'),
        ('Prof', 'Prof'),
    ]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_profile')
    title = models.CharField(max_length=10, choices=TITLE_CHOICES, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    street_name = models.CharField(max_length=160, blank=True)
    house_number = models.CharField(max_length=30, blank=True)
    city = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=80, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self): return f'{self.user.email} profile'


class CustomerAddress(models.Model):
    ADDRESS_TYPES = [('billing', 'Billing'), ('shipping', 'Shipping')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_addresses')
    address_type = models.CharField(max_length=10, choices=ADDRESS_TYPES)
    label = models.CharField(max_length=80, blank=True)
    house_number = models.CharField(max_length=30)
    street_name = models.CharField(max_length=160)
    city = models.CharField(max_length=100)
    postcode = models.CharField(max_length=30)
    country = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['address_type', 'label', 'id']

    def __str__(self):
        return self.label or f'{self.house_number} {self.street_name}, {self.city}'


class PrivacyRequest(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('completed', 'Completed'), ('rejected', 'Rejected')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='privacy_requests')
    request_type = models.CharField(max_length=30, default='erasure')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'{self.user.email} - {self.request_type} ({self.status})'

class Order(models.Model):
    STATUS_CHOICES = [('pending','Pending'),('paid','Paid'),('processing','Processing'),('shipped','Shipped'),('completed','Completed'),('return','Return'),('cancelled','Cancelled')]
    PAYMENT_STATUS_CHOICES = [('pending','Pending'),('processing','Processing'),('paid','Paid'),('failed','Failed'),('cancelled','Cancelled')]
    DELIVERY_METHOD_CHOICES = [('standard_uk', 'UK Standard delivery'), ('express_uk', 'UK Express delivery'), ('international', 'International delivery')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    order_reference = models.CharField(max_length=6, unique=True, null=True, blank=True, db_index=True)
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    stripe_session_id = models.CharField(max_length=255, blank=True, unique=True, null=True)
    revolut_order_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    revolut_checkout_url = models.URLField(blank=True, null=True)
    shipping_name = models.CharField(max_length=160)
    shipping_address1 = models.CharField(max_length=255)
    shipping_address2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100)
    shipping_postcode = models.CharField(max_length=30)
    shipping_country = models.CharField(max_length=80, default='United Kingdom')
    billing_name = models.CharField(max_length=160, blank=True)
    billing_address1 = models.CharField(max_length=255, blank=True)
    billing_address2 = models.CharField(max_length=255, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_postcode = models.CharField(max_length=30, blank=True)
    billing_country = models.CharField(max_length=80, blank=True)
    delivery_method = models.CharField(max_length=30, choices=DELIVERY_METHOD_CHOICES, default='standard_uk')
    delivery_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    tracking_number = models.CharField(max_length=120, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    currency = models.CharField(max_length=3, default='GBP')
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=120, blank=True, default='')
    transaction_id = models.CharField(max_length=255, blank=True, default='')
    payment_date = models.DateTimeField(null=True, blank=True)
    payment_confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    refund_status = models.CharField(max_length=20, blank=True, default='')
    stripe_refund_id = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'Order #{self.order_reference or self.pk}'

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=60)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'), help_text='Product cost price at time of order, snapshotted for accurate historical margin reporting.')
    quantity = models.PositiveIntegerField()

    @property
    def line_total(self): return self.unit_price * self.quantity

    @property
    def line_cost(self): return self.unit_cost * self.quantity

class WebhookEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='received')
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self): return f'{self.event_type} ({self.event_id})'

class Enquiry(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'{self.name} — {self.subject or "Enquiry"}'


class EnquiryReply(models.Model):
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name='replies')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self): return f'Reply to {self.enquiry_id}'


class AnalyticsEvent(models.Model):
    EVENT_TYPES = [('page_view', 'Page view'), ('click', 'Click')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True, blank=True, related_name='analytics_events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    path = models.CharField(max_length=500)
    target = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    country = models.CharField(max_length=80, blank=True)
    referrer = models.CharField(max_length=500, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class IPGeolocation(models.Model):
    ip_address = models.GenericIPAddressField(unique=True)
    is_private = models.BooleanField(default=False)
    country = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self): return self.ip_address

    @property
    def label(self):
        if self.is_private:
            return 'Private network'
        parts = [part for part in [self.city, self.country] if part]
        return ', '.join(parts) if parts else 'Unknown'


class CustomerReview(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')]
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    rating = models.PositiveSmallIntegerField(default=5)
    title = models.CharField(max_length=160, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'{self.name} - {self.rating}/5'

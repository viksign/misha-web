from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import get_language

class Collection(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    name_fr = models.CharField(max_length=120, blank=True)
    description_fr = models.TextField(blank=True)
    image = models.ImageField(upload_to='collections/', blank=True, null=True)
    active = models.BooleanField(default=True)
    featured = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self): return self.name
    @property
    def localized_name(self): return self.name_fr if get_language() == 'fr' and self.name_fr else self.name
    @property
    def localized_description(self): return self.description_fr if get_language() == 'fr' and self.description_fr else self.description
    def get_absolute_url(self): return reverse('collection', args=[self.slug])

class Product(models.Model):
    name = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(unique=True)
    sku = models.CharField(max_length=60, unique=True)
    collection = models.ForeignKey(Collection, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    description = models.TextField()
    description_fr = models.TextField(blank=True)
    short_description = models.CharField(max_length=300, blank=True)
    short_description_fr = models.CharField(max_length=300, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'), help_text='Unit cost to source/manufacture this piece, used for margin and replenishment budgeting.')
    reorder_level = models.PositiveIntegerField(default=5, help_text='Reorder when stock falls to or below this quantity.')
    restock_target = models.PositiveIntegerField(default=20, help_text='Target stock quantity to replenish up to.')
    material = models.CharField(max_length=120, blank=True)
    material_fr = models.CharField(max_length=120, blank=True)
    dimensions = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=120, blank=True)
    length = models.CharField(max_length=120, blank=True)
    weight = models.CharField(max_length=120, blank=True)
    shipping_box_weight_grams = models.PositiveIntegerField(default=0, help_text='Weight of this product in its jewellery box, in grams.')
    shipping_box_length_cm = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    shipping_box_width_cm = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    shipping_box_height_cm = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    technical_details = models.TextField(blank=True)
    stock_quantity = models.PositiveIntegerField(default=0)
    featured = models.BooleanField(default=False)
    preorder_enabled = models.BooleanField(default=False, help_text='Allow registered customers to pre-order this product when it is out of stock.')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-featured', '-created_at']

    def __str__(self): return self.name
    @property
    def localized_name(self): return self.name_fr if get_language() == 'fr' and self.name_fr else self.name
    @property
    def localized_description(self): return self.description_fr if get_language() == 'fr' and self.description_fr else self.description
    @property
    def localized_short_description(self): return self.short_description_fr if get_language() == 'fr' and self.short_description_fr else self.short_description
    @property
    def localized_material(self): return self.material_fr if get_language() == 'fr' and self.material_fr else self.material
    def get_absolute_url(self): return reverse('product_detail', args=[self.slug])
    @property
    def in_stock(self): return self.stock_quantity > 0
    @property
    def stock_status(self):
        if self.stock_quantity == 0:
            return 'out'
        return 'low' if self.stock_quantity <= self.reorder_level else 'healthy'

    @property
    def replenishment_units(self):
        return max(0, self.restock_target - self.stock_quantity)

    @cached_property
    def primary_image(self):
        images = list(self.images.all())
        return next((image for image in images if image.is_primary), images[0] if images else None)

    def save(self, *args, stock_actor=None, stock_reason='', stock_order=None, **kwargs):
        update_fields = kwargs.get('update_fields')
        if not self._state.adding and update_fields is not None and 'stock_quantity' not in update_fields:
            return super().save(*args, **kwargs)
        database = kwargs.get('using') or self._state.db or 'default'
        kwargs['using'] = database
        with transaction.atomic(using=database):
            previous = None
            if not self._state.adding:
                previous = type(self).objects.using(database).select_for_update().filter(pk=self.pk).values_list('stock_quantity', flat=True).first()
            super().save(*args, **kwargs)
            if previous is None or previous != self.stock_quantity:
                before = previous if previous is not None else 0
                change = self.stock_quantity - before
                reason = stock_reason or ('opening' if previous is None else 'restock' if change > 0 else 'adjustment')
                actor_name = stock_actor.get_full_name().strip() or f'Staff #{stock_actor.pk} (name not set)' if stock_actor else ''
                StockMovement.objects.using(database).create(
                    product=self, product_name=self.name, sku=self.sku, reason=reason,
                    quantity_change=change, balance_before=before, balance_after=self.stock_quantity,
                    actor=stock_actor, actor_name=actor_name, order=stock_order,
                    order_reference=str(stock_order.order_reference or stock_order.pk) if stock_order else '',
                )


class StockMovement(models.Model):
    REASON_CHOICES = [('opening', 'Opening balance'), ('restock', 'Restock'), ('adjustment', 'Manual adjustment'), ('sale', 'Paid purchase'), ('gift', 'Free gift')]
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=60)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    quantity_change = models.IntegerField()
    balance_before = models.PositiveIntegerField()
    balance_after = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    actor_name = models.CharField(max_length=310, blank=True)
    order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    order_reference = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['product', '-created_at'], name='shop_stock_product_date_idx')]


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


class ShippingPackaging(models.Model):
    packaging_weight_grams = models.PositiveIntegerField(default=0)
    extra_length_cm = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))
    extra_width_cm = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))
    extra_height_cm = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))

    class Meta:
        verbose_name = 'shipping packaging setting'
        verbose_name_plural = 'shipping packaging settings'

    def __str__(self): return 'Outer packaging'


class ShippingRate(models.Model):
    country = models.CharField(max_length=120)
    service = models.ForeignKey(DeliveryOption, on_delete=models.CASCADE, related_name='shipping_rates')
    max_weight_grams = models.PositiveIntegerField()
    max_length_cm = models.DecimalField(max_digits=7, decimal_places=2)
    max_width_cm = models.DecimalField(max_digits=7, decimal_places=2)
    max_height_cm = models.DecimalField(max_digits=7, decimal_places=2)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['country', 'service__sort_order', 'max_weight_grams', 'price']

    def __str__(self):
        return f'{self.country} · {self.service.label} · up to {self.max_weight_grams} g'

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/')
    alt_text = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self): return f'{self.product.name} image'

    @cached_property
    def dimensions(self):
        try:
            return self.image.width, self.image.height
        except (OSError, ValueError):
            return None, None

    @property
    def image_width(self):
        return self.dimensions[0]

    @property
    def image_height(self):
        return self.dimensions[1]


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
    STATUS_CHOICES = [('pending','Pending'),('processing','Processing'),('shipped','Shipped'),('completed','Completed'),('return','Return'),('cancelled','Cancelled')]
    PAYMENT_STATUS_CHOICES = [('pending','Pending'),('processing','Processing'),('paid','Paid'),('failed','Failed'),('cancelled','Cancelled'),('partially_refunded','Partially refunded'),('refunded','Refunded')]
    PAYMENT_CHANNEL_CHOICES = [('stripe', 'Stripe / card'), ('cash', 'Cash'), ('gift', 'Free gift'), ('gift_card', 'Gift card')]
    DELIVERY_METHOD_CHOICES = [('standard_uk', 'UK Standard delivery'), ('express_uk', 'UK Express delivery'), ('international', 'International delivery')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_orders', limit_choices_to={'is_staff': True, 'is_active': True}, verbose_name='Processing by')
    order_reference = models.CharField(max_length=6, unique=True, null=True, blank=True, db_index=True)
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    payment_channel = models.CharField(max_length=20, choices=PAYMENT_CHANNEL_CHOICES, default='stripe')
    manual_entry_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    manual_payment_reference = models.CharField(max_length=255, null=True, blank=True, unique=True)
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
    tracking_url = models.URLField(blank=True)
    tracking_email_sent_at = models.DateTimeField(null=True, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    currency = models.CharField(max_length=3, default='GBP')
    paid_at = models.DateTimeField(null=True, blank=True)
    stock_deducted_at = models.DateTimeField(null=True, blank=True, editable=False)
    processing_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    shipped_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    payment_method = models.CharField(max_length=120, blank=True, default='')
    transaction_id = models.CharField(max_length=255, blank=True, default='')
    payment_date = models.DateTimeField(null=True, blank=True)
    payment_confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    refund_status = models.CharField(max_length=20, blank=True, default='')
    stripe_refund_id = models.CharField(max_length=255, blank=True, default='')
    refund_notes = models.TextField(blank=True, default='', max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'Order #{self.order_reference or self.pk}'

    @property
    def processor_name(self):
        if not self.assigned_to_id:
            return 'Unassigned'
        return self.assigned_to.get_full_name().strip() or f'Staff #{self.assigned_to_id} (name not set)'

    def save(self, *args, **kwargs):
        milestone = {
            'processing': 'processing_started_at',
            'shipped': 'shipped_at',
            'completed': 'completed_at',
        }.get(self.status)
        update_fields = kwargs.get('update_fields')
        if milestone and not getattr(self, milestone) and (update_fields is None or 'status' in update_fields):
            previous_status = None
            if not self._state.adding:
                previous_status = type(self).objects.using(kwargs.get('using') or self._state.db).filter(pk=self.pk).values_list('status', flat=True).first()
            if self._state.adding or previous_status != self.status:
                setattr(self, milestone, timezone.now())
                if update_fields is not None:
                    kwargs['update_fields'] = set(update_fields) | {milestone}
        super().save(*args, **kwargs)

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

class StripeSyncRun(models.Model):
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    currency = models.CharField(max_length=3)
    livemode = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=[('complete', 'Complete'), ('partial', 'Partial'), ('failed', 'Failed')])
    transaction_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class StripeTransaction(models.Model):
    stripe_id = models.CharField(max_length=255, unique=True)
    payment_intent_id = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='stripe_transactions')
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    refund_notes = models.TextField(blank=True, default='', max_length=2000)
    fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    net = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=[('paid', 'Paid'), ('pending', 'Pending'), ('failed', 'Failed')])
    occurred_at = models.DateTimeField(db_index=True)
    livemode = models.BooleanField(default=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-occurred_at', '-pk']


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


class CustomDesignRequest(models.Model):
    STATUS_CHOICES = [
        ('new', 'New'),
        ('reviewing', 'Reviewing'),
        ('feasible', 'Feasible'),
        ('not_feasible', 'Not feasible'),
        ('completed', 'Completed'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='custom_design_requests')
    title = models.CharField(max_length=160)
    product_details = models.TextField(help_text='What would you like us to create or adapt?')
    inspiration = models.TextField(blank=True, help_text='Share the story, reference, mood or inspiration behind your idea.')
    material = models.CharField(max_length=120, blank=True)
    budget = models.CharField(max_length=120, blank=True)
    photo = models.ImageField(upload_to='custom-design/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'{self.title} - {self.user.email}'


class CustomDesignRequestReply(models.Model):
    request = models.ForeignKey(CustomDesignRequest, on_delete=models.CASCADE, related_name='replies')
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class InterestSignup(models.Model):
    INTEREST_TYPES = [
        ('preorder', 'New collection pre-order'),
        ('restock', 'Back in stock notification'),
    ]
    email = models.EmailField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='interest_signups')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True, related_name='interest_signups')
    interest_type = models.CharField(max_length=20, choices=INTEREST_TYPES)
    desired_quantity = models.PositiveIntegerField(default=1, help_text='Number of units the customer would like to order.')
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['email', 'product', 'interest_type'], name='unique_product_interest_signup'),
        ]

    def __str__(self):
        target = self.product.name if self.product else 'New collection'
        return f'{self.email} - {target}'


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


class BlockedIP(models.Model):
    ip_address = models.GenericIPAddressField(unique=True)
    reason = models.CharField(max_length=255)
    blocked_at = models.DateTimeField(auto_now=True)
    blocked_until = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-blocked_at']

    def __str__(self): return f'{self.ip_address} until {self.blocked_until}'


class SecurityEvent(models.Model):
    EVENT_TYPES = [
        ('auto_block', 'Automatic IP block'),
        ('unblocked', 'IP unblocked'),
    ]
    ip_address = models.GenericIPAddressField()
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    path = models.CharField(max_length=500, blank=True)
    reason = models.CharField(max_length=255)
    request_count = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['-created_at'])]

    def __str__(self): return f'{self.get_event_type_display()}: {self.ip_address}'


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

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
    material = models.CharField(max_length=120, blank=True)
    dimensions = models.CharField(max_length=200, blank=True)
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

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/')
    alt_text = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self): return f'{self.product.name} image'

class Order(models.Model):
    STATUS_CHOICES = [('pending','Pending'),('paid','Paid'),('processing','Processing'),('shipped','Shipped'),('completed','Completed'),('cancelled','Cancelled')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    stripe_session_id = models.CharField(max_length=255, blank=True, unique=True, null=True)
    shipping_name = models.CharField(max_length=160)
    shipping_address1 = models.CharField(max_length=255)
    shipping_address2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100)
    shipping_postcode = models.CharField(max_length=30)
    shipping_country = models.CharField(max_length=80, default='United Kingdom')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return f'Order #{self.pk}'

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=60)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()

    @property
    def line_total(self): return self.unit_price * self.quantity

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

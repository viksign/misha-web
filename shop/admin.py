from django.contrib import admin
from .models import AnalyticsEvent, Collection, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, Enquiry, Order, OrderItem, PrivacyRequest, Product, ProductImage

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name','sku','collection','price','stock_quantity','featured','active')
    list_filter = ('active','featured','collection')
    search_fields = ('name','sku','description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline]

@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ('name','active')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(DeliveryOption)
class DeliveryOptionAdmin(admin.ModelAdmin):
    list_display = ('label', 'code', 'price', 'free_over', 'active', 'sort_order')
    list_filter = ('active',)
    list_editable = ('price', 'free_over', 'active', 'sort_order')
    search_fields = ('label', 'code')

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product_name','sku','unit_price','quantity','line_total')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id','email','status','total','created_at')
    list_filter = ('status','created_at')
    search_fields = ('email','stripe_session_id','shipping_name')
    readonly_fields = ('stripe_session_id','created_at')
    inlines = [OrderItemInline]

@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ('name','email','subject','handled','created_at')
    list_filter = ('handled','created_at')
    search_fields = ('name','email','subject','message')

@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'country', 'updated_at')
    search_fields = ('user__email', 'phone', 'address', 'country')

@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'address_type', 'label', 'city', 'country')
    list_filter = ('address_type', 'country')
    search_fields = ('user__email', 'label', 'street_name', 'city', 'postcode')

@admin.register(PrivacyRequest)
class PrivacyRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'request_type', 'status', 'created_at', 'completed_at')
    list_filter = ('request_type', 'status', 'created_at')
    search_fields = ('user__email',)

@admin.register(CustomerReview)
class CustomerReviewAdmin(admin.ModelAdmin):
    list_display = ('name', 'rating', 'status', 'featured', 'created_at')
    list_filter = ('status', 'featured', 'rating')
    search_fields = ('name', 'email', 'title', 'body')

@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'event_type', 'path', 'target', 'ip_address', 'country', 'user')
    list_filter = ('event_type', 'country', 'created_at')
    search_fields = ('path', 'target', 'ip_address', 'country', 'user__email')
    readonly_fields = ('created_at',)

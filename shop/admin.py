from django.contrib import admin
from .models import ProductCategory, ProductTag, StockMovement
from .models import AnalyticsEvent, Collection, CustomDesignRequest, CustomDesignRequestReply, CustomerAddress, CustomerProfile, CustomerReview, DeliveryOption, DeliveryRegion, Enquiry, InterestSignup, Order, OrderItem, PrivacyRequest, Product, ProductImage, ProductOption, ShippingPackaging, ShippingRate

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1

class ProductOptionInline(admin.TabularInline):
    model = ProductOption
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name','sku','category','collection','price','stock_quantity','preorder_enabled','featured','active')
    list_filter = ('active','featured','preorder_enabled','category','tags','collection')
    filter_horizontal = ('tags',)
    search_fields = ('name','sku','description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, ProductOptionInline]

    def save_model(self, request, obj, form, change):
        obj.save(stock_actor=request.user)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'sku', 'reason', 'quantity_change', 'balance_after', 'actor_name', 'order_reference')
    list_filter = ('reason', 'created_at')
    search_fields = ('sku', 'product_name', 'actor_name', 'order_reference')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShippingPackaging)
class ShippingPackagingAdmin(admin.ModelAdmin):
    list_display = ('packaging_weight_grams', 'extra_length_cm', 'extra_width_cm', 'extra_height_cm')

    def has_add_permission(self, request):
        return not ShippingPackaging.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = ('country', 'service', 'max_weight_grams', 'max_length_cm', 'max_width_cm', 'max_height_cm', 'price', 'active')
    list_filter = ('country', 'service', 'active')
    list_editable = ('price', 'active')
    search_fields = ('country', 'service__label')
    autocomplete_fields = ('service',)

@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'featured', 'active')
    prepopulated_fields = {'slug': ('name',)}
    list_filter = ('featured', 'active')


@admin.register(ProductCategory, ProductTag)
class ProductTaxonomyAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}

@admin.register(DeliveryRegion)
class DeliveryRegionAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_default', 'active', 'sort_order')
    list_filter = ('active', 'is_default')
    list_editable = ('is_default', 'active', 'sort_order')
    search_fields = ('name', 'countries')

@admin.register(DeliveryOption)
class DeliveryOptionAdmin(admin.ModelAdmin):
    list_display = ('label', 'code', 'region', 'transit_time', 'price', 'free_over', 'active', 'sort_order')
    list_filter = ('active', 'region')
    list_editable = ('price', 'free_over', 'active', 'sort_order')
    search_fields = ('label', 'code')

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product_name','sku','options_summary','chain_length','size','unit_price','quantity','line_total')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_reference','email','status','payment_status','checkout_state','processor_name','total','created_at')
    list_filter = ('payment_status','status','assigned_to','created_at')
    search_fields = ('order_reference','email','phone','stripe_session_id','shipping_name')
    readonly_fields = ('stripe_session_id', 'payment_status', 'paid_at', 'stock_deducted_at', 'processing_started_at', 'shipped_at', 'completed_at', 'payment_method', 'transaction_id', 'payment_date', 'refund_status', 'refunded_amount', 'stripe_refund_id', 'tracking_email_sent_at', 'created_at')
    inlines = [OrderItemInline]

    @admin.display(description='Payment confirmation')
    def checkout_state(self, obj):
        if obj.is_unpaid_checkout:
            return 'Unpaid checkout attempt - do not fulfil'
        return 'Payment confirmed' if obj.paid_at or obj.payment_status in {'paid', 'partially_refunded', 'refunded'} else 'Manual order'

    @admin.display(description='Processing by', ordering='assigned_to__first_name')
    def processor_name(self, obj):
        return obj.processor_name

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == 'assigned_to':
            field.label_from_instance = lambda staff: staff.get_full_name().strip() or f'Staff #{staff.pk} (name not set)'
        return field

    def save_model(self, request, obj, form, change):
        if obj.status == 'processing' and not obj.assigned_to_id:
            obj.assigned_to = request.user
        super().save_model(request, obj, form, change)

@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ('name','email','subject','handled','created_at')
    list_filter = ('handled','created_at')
    search_fields = ('name','email','subject','message')

@admin.register(InterestSignup)
class InterestSignupAdmin(admin.ModelAdmin):
    list_display = ('email', 'user', 'interest_type', 'product', 'created_at', 'notified_at')
    list_filter = ('interest_type', 'created_at', 'notified_at')
    search_fields = ('email', 'product__name')
    readonly_fields = ('created_at',)

class CustomDesignRequestReplyInline(admin.TabularInline):
    model = CustomDesignRequestReply
    extra = 0
    readonly_fields = ('created_at',)

@admin.register(CustomDesignRequest)
class CustomDesignRequestAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'status', 'created_at', 'updated_at')
    list_filter = ('status', 'created_at')
    search_fields = ('title', 'user__email', 'product_details', 'inspiration')
    list_editable = ('status',)
    inlines = [CustomDesignRequestReplyInline]

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

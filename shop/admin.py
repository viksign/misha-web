from django.contrib import admin
from .models import Collection, Enquiry, Order, OrderItem, Product, ProductImage

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

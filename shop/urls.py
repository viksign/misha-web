from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('jewellery/', views.catalogue, name='catalogue'),
    path('collection/<slug:slug>/', views.collection_detail, name='collection'),
    path('jewellery/<slug:slug>/', views.product_detail, name='product_detail'),
    path('bag/', views.cart_view, name='cart'),
    path('bag/add/<int:pk>/', views.add_to_cart, name='add_to_cart'),
    path('bag/update/<int:pk>/', views.update_cart, name='update_cart'),
    path('bag/remove/<int:pk>/', views.remove_from_cart, name='remove_from_cart'),
    path('checkout/', views.checkout, name='checkout'),
    path('checkout/success/', views.checkout_success, name='checkout_success'),
    path('payments/stripe/webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('contact/', views.enquiry, name='contact'),
    path('account/', views.account, name='account'),
]

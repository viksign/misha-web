from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from shop.forms import EmailAuthenticationForm
from shop.sitemaps import CollectionSitemap, ProductSitemap, StaticViewSitemap
from shop.views import robots_txt
from shop.seo import favicon, google_verification, sitemap_index, sitemap_section

handler400 = 'shop.errors.bad_request'
handler403 = 'shop.errors.permission_denied'
handler404 = 'shop.errors.page_not_found'
handler500 = 'shop.errors.server_error'

sitemaps = {
    'products': ProductSitemap,
    'collections': CollectionSitemap,
    'static': StaticViewSitemap,
}

urlpatterns = [
    path('favicon.ico', favicon, name='favicon'),
    path('google26bfb82dbebf9ae3.html', google_verification, name='google_verification'),
    path('staff-auth/', include('shop.staff_auth_urls')),
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap_index, {'sitemaps': sitemaps}, name='sitemap_index'),
    path('sitemap-<section>.xml', sitemap_section, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('', include('shop.urls')),
    path('login/', auth_views.LoginView.as_view(
        template_name='registration/login.html',
        authentication_form=EmailAuthenticationForm,
    ), name='login'),
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='registration/password_reset_form.html',
        email_template_name='registration/password_reset_email.txt',
        subject_template_name='registration/password_reset_subject.txt',
        from_email=settings.PASSWORD_RESET_FROM_EMAIL,
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
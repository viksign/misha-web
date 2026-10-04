from pathlib import Path
import os
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() == 'true'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'dev-only-change-me'
    else:
        raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set when DJANGO_DEBUG=False')
if not DEBUG and (len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5):
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be at least 50 characters and sufficiently random')

ALLOWED_HOSTS = [host.strip() for host in os.getenv(
    'DJANGO_ALLOWED_HOSTS',
    'localhost,127.0.0.1',
).split(',') if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv(
    'DJANGO_CSRF_TRUSTED_ORIGINS',
    'https://mishaislandheritage.com,https://www.mishaislandheritage.com',
).split(',') if origin.strip()]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.getenv('DJANGO_SECURE_SSL_REDIRECT', str(not DEBUG)).lower() == 'true'
SESSION_COOKIE_SECURE = os.getenv('DJANGO_SESSION_COOKIE_SECURE', str(not DEBUG)).lower() == 'true'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = os.getenv('DJANGO_CSRF_COOKIE_SECURE', str(not DEBUG)).lower() == 'true'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_FAILURE_VIEW = 'shop.errors.csrf_failure'
SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_SECURE_HSTS_SECONDS', '31536000' if not DEBUG else '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', str(not DEBUG)).lower() == 'true'
SECURE_HSTS_PRELOAD = os.getenv('DJANGO_SECURE_HSTS_PRELOAD', 'False').lower() == 'true'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
X_FRAME_OPTIONS = 'DENY'
SECURITY_REQUEST_LIMIT = int(os.getenv('SECURITY_REQUEST_LIMIT', '300'))
SECURITY_REQUEST_WINDOW_SECONDS = int(os.getenv('SECURITY_REQUEST_WINDOW_SECONDS', '60'))
SECURITY_REQUEST_BLOCK_SECONDS = int(os.getenv('SECURITY_REQUEST_BLOCK_SECONDS', '900'))
SECURITY_LOGIN_LIMIT = int(os.getenv('SECURITY_LOGIN_LIMIT', '12'))
SECURITY_LOGIN_WINDOW_SECONDS = int(os.getenv('SECURITY_LOGIN_WINDOW_SECONDS', '600'))
SECURITY_LOGIN_BLOCK_SECONDS = int(os.getenv('SECURITY_LOGIN_BLOCK_SECONDS', '1800'))
SECURITY_EVENT_RETENTION_DAYS = int(os.getenv('SECURITY_EVENT_RETENTION_DAYS', '90'))
STAFF_MFA_REQUIRED = os.getenv('STAFF_MFA_REQUIRED', 'True').lower() == 'true'
TWO_FACTOR_PATCH_ADMIN = False
TWO_FACTOR_TOTP_ISSUER = 'MISHA staff'
TWO_FACTOR_LOGIN_TIMEOUT = 300
TWO_FACTOR_REMEMBER_COOKIE_AGE = None

INSTALLED_APPS = [
    'shop.admin_config.StaffAdminConfig', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'chartjs',
    'django_otp',
    'django_otp.plugins.otp_static',
    'django_otp.plugins.otp_totp',
    'two_factor',
    'shop',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'shop.seo.SeoHeadersMiddleware',
    'shop.security_middleware.SuspiciousActivityMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'shop.staff_auth.StaffMFAMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'shop.middleware.AnalyticsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'django.template.context_processors.i18n',
        'shop.context_processors.shop_context',
        'shop.seo.seo_context',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.getenv('MYSQL_DATABASE', 'djapp'),
        'USER': os.getenv('MYSQL_USER', 'djapp'),
        'PASSWORD': os.getenv('MYSQL_PASSWORD', 'djapp-password'),
        'HOST': os.getenv('MYSQL_HOST', 'db'),
        'PORT': os.getenv('MYSQL_PORT', '3306'),
    }
}

REDIS_URL = os.getenv('REDIS_URL', '').strip()
if not DEBUG and not REDIS_URL:
    raise ImproperlyConfigured('REDIS_URL must be set when DJANGO_DEBUG=False')
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache' if REDIS_URL else 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': REDIS_URL or 'misha-local-cache',
        'KEY_PREFIX': 'misha',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'en-gb'
LANGUAGES = [('en', 'English')]
LOCALE_PATHS = []
TIME_ZONE = 'Europe/London'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_REDIRECT_URL = '/account/'
LOGIN_URL = '/login/'
LOGOUT_REDIRECT_URL = '/'
EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend',
)
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Misha Island Heritage <no-reply@mishaislandheritage.com>')
PASSWORD_RESET_FROM_EMAIL = os.getenv('PASSWORD_RESET_FROM_EMAIL', 'no-reply@mishaislandheritage.com')
CUSTOM_REQUEST_NOTIFICATION_EMAIL = os.getenv('CUSTOM_REQUEST_NOTIFICATION_EMAIL', os.getenv('CERTBOT_EMAIL', ''))
PREORDER_NOTIFICATION_EMAIL = os.getenv('PREORDER_NOTIFICATION_EMAIL', CUSTOM_REQUEST_NOTIFICATION_EMAIL).strip()
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() == 'true'

SITE_URL = os.getenv('SITE_URL', 'https://www.mishaislandheritage.com').rstrip('/')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash').strip()

GOOGLE_SITE_VERIFICATION = os.getenv('GOOGLE_SITE_VERIFICATION', '')
GOOGLE_ADS_ID = os.getenv('GOOGLE_ADS_ID', '')
GOOGLE_ANALYTICS_ID = os.getenv('GOOGLE_ANALYTICS_ID', '')

REVOLUT_API_KEY = os.getenv('REVOLUT_API_KEY', '')
REVOLUT_API_BASE_URL = os.getenv('REVOLUT_API_BASE_URL', 'https://sandbox-merchant.revolut.com').rstrip('/')
REVOLUT_WEBHOOK_SECRET = os.getenv('REVOLUT_WEBHOOK_SECRET', '')
REVOLUT_CURRENCY = os.getenv('REVOLUT_CURRENCY', 'GBP').upper()

STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_CURRENCY = os.getenv('STRIPE_CURRENCY', 'gbp')

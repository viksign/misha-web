# MISHA Island Heritage — Full Django Jewellery Shop

This is a full Django shop foundation for MISHA Island Heritage, not just a static template.

## Included

- Django project + `shop` app
- Responsive luxury black/gold storefront
- Product catalogue
- Collections
- Product detail pages
- Product image gallery
- Django Admin management
- Session-based shopping bag
- Guest checkout
- User accounts / order history
- Order + OrderItem models
- Stripe Checkout integration
- Stripe webhook endpoint
- Custom jewellery enquiry form stored in database
- Seed command for initial products
- Your final MISHA Island Heritage logo
- Your three supplied necklace photographs included under `media/products/`

## Install

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env  # macOS/Linux

python manage.py migrate
python manage.py createsuperuser
python manage.py seed_shop
python manage.py runserver
```

Open:

- Storefront: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/
- Jewellery: http://127.0.0.1:8000/jewellery/

## Add the real necklace images to a product

The supplied images are in `media/products/`. In Django Admin, open **Products → Plumeria Pendant → Product images** and upload them there. The first primary image will appear on the product card/detail page.

## Stripe

Set these in `.env`:

```text
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
SITE_URL=http://127.0.0.1:8000
```

The webhook endpoint is:

`/payments/stripe/webhook/`

For production, use HTTPS and configure the Stripe webhook to send `checkout.session.completed`.

## Recommended production additions

- PostgreSQL instead of SQLite
- Cloud object storage for jewellery photos
- Email confirmations
- Stock reservation/transaction handling around payment
- Shipping/tax rules
- GDPR/privacy/cookie consent
- Password reset email configuration
- Search and filtering with a proper search index
- Product variants (chain length, ring size, finish)
- Discount codes
- Reviews
- Wishlist
- Analytics
- Production deployment with Gunicorn + Nginx or a managed platform

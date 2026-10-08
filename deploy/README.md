# Production deployment

The production stack exposes Nginx on ports 80 and 443. Django runs behind it
with Gunicorn. MySQL and Gunicorn are not published to the host. Certbot renews
certificates from persistent Docker volumes.

## Prerequisites

1. Point DNS `A`/`AAAA` records for `mishaislandheritage.com` and
   `www.mishaislandheritage.com` to this server.
2. Allow inbound TCP ports 80 and 443 in the host and provider firewalls.
3. Generate a secret with `openssl rand -base64 48` and set the result as
   `DJANGO_SECRET_KEY` in `.env`. Keep `DJANGO_DEBUG=False`.
4. Confirm the production values in `.env`, especially database credentials,
   `CERTBOT_EMAIL`, Stripe keys, and SMTP credentials.
5. Replace the example MySQL user and root passwords with unique, strong values;
   Compose refuses to start the database if either password is missing.
6. Set `HOME_IP_ADDRESSES` in `.env` to the public IP address to exclude from
   Control Panel analytics by default. Separate multiple IPv4 or IPv6 addresses
   with commas. Staff can temporarily include that activity from the analytics
   filter.

## Email delivery

Production defaults to Django's SMTP backend. Set `EMAIL_HOST`, `EMAIL_PORT`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, and a sender address
that your mail provider has authorized in `.env`. Pre-order staff notifications
go to `PREORDER_NOTIFICATION_EMAIL`; if it is unset, the app falls back to
`CUSTOM_REQUEST_NOTIFICATION_EMAIL`, then `CERTBOT_EMAIL`.

After changing the code or mail settings, rebuild and restart the web service
with `docker compose up -d --build web`. Submit a new pre-order request and
check `docker compose logs --tail=200 web` for SMTP errors if the confirmation
does not arrive. A successful SMTP handoff does not guarantee inbox delivery;
also check spam and the mail provider's delivery/activity logs.

## Security controls

Nginx rate-limits dynamic requests per client IP, applies a stricter limit to
login submissions, caps concurrent connections, and rejects slow clients.
The application uses shared Redis counters across Gunicorn workers. More than
300 requests per minute triggers a 15-minute IP block; more than 12 login
submissions in 10 minutes triggers a 30-minute block. These values can be
adjusted in `.env` using the `SECURITY_*` settings.

Automatic blocks and staff unblocks are listed under Control Panel → Analytics.
Staff can remove a block there. Security events retain only the IP, request
path, reason, count, and expiry; they do not capture request bodies or passwords.
Old security events are pruned when new blocks are recorded.

Site activity includes a **Connections by location** doughnut chart that follows
the selected date/time and user filters. Connections mean tracked page views
(not sessions or live sockets); each city/country label shows its distinct IP
count. It uses cached IP locations without additional external lookups.
Unresolved or missing IP locations are grouped as **Unknown**, and missing IP
addresses do not contribute to unique-IP counts.

The activity log supports **Location / country** and **City** filters with
case-insensitive partial-name matching and suggestions from cached locations.
These filters apply to the full result set, summary counts, charts, pagination,
and the existing confirmed purge action. Unknown and private locations do not
match a country/city filter. **Search all activity records** searches location,
city, IP, event type, product, path, target, and customer email across the full
filtered result set. Press Enter or **Search** to apply it; search terms persist
through pagination, chart requests, and the existing confirmed purge action.

Nginx and application limits mitigate HTTP floods, but cannot absorb a
volumetric network-layer DDoS that saturates the server connection before
Nginx. Enable Azure DDoS protection or an upstream WAF/CDN for production.
Keep database backups encrypted and restrict access to the host and Docker
volumes, which contain customer and order data.

## Staff multifactor authentication

`STAFF_MFA_REQUIRED=True` (the default) requires authenticator-app verification
for both `/admin/` and every `/controlpanel/` page, including chart and PDF
endpoints. A password-only customer login, password reset, or existing staff
session cannot bypass the MFA gate. Customer account login is unchanged.

Staff sign in at `/staff-auth/login/` with their username or unique email address
and password. Accounts without an authenticator are directed to setup. Scan the
QR code with an authenticator app, then enter its current six-digit code to
confirm enrollment. A verified session is established only after confirmation.
Generate recovery codes from the setup-complete page, or from **Account
security → Manage recovery codes**, and store them securely offline. Each
recovery code is usable once; generating a new set invalidates the previous set.
Authenticator setup/login submissions are covered by the login rate limit.
The Account security overview identifies the signed-in user and confirms
staff/MFA verification. Its recovery-code link opens a separate page; returning
to the overview does not require another login while the session remains verified.

There is no staff-facing disable-MFA endpoint or trusted-browser bypass.
Already enrolled accounts must verify before accessing setup or security pages.
If someone loses both their authenticator and recovery codes, the server
administrator must verify their identity out of band, reset their password
with `python manage.py changepassword USERNAME` (invalidating existing sessions),
and remove only that user's TOTP and static devices using the Django shell.
They can then sign in with the new password and enroll again. Never remove all
users' devices or turn off MFA globally as a routine recovery procedure.

MFA secrets and recovery codes are stored in the database; restrict database
access and encrypt backups. First-time enrollment does not protect an account
until the rightful staff member has completed setup, so enroll promptly.

For existing business-function tests that use password-only `force_login`,
run with `STAFF_MFA_REQUIRED=False`. The separate `shop.test_staff_auth` suite
explicitly enables enforcement and tests TOTP, enrollment, recovery codes,
CSRF, admin permission checks, and password-only-session bypass prevention.
Do not disable this setting on the live site.

## First deployment

Build the image and request the first certificate:

```bash
docker compose build web
./deploy/init-letsencrypt.sh
```

The bootstrap script starts Nginx with a temporary certificate, requests the
Let's Encrypt certificate for both hostnames, reloads Nginx, and starts the
renewal service.

## Later releases

```bash
docker compose up -d --build --remove-orphans
```

## Checks

```bash
docker compose ps
curl -I https://mishaislandheritage.com
docker compose logs --tail=100 web nginx certbot
```

Back up the `mysql_data`, `media_data`, and `certbot_conf` Docker volumes.

## Checkout payment methods

Stripe Checkout uses the payment methods enabled in the Stripe Dashboard.
Keep card payments enabled there; do not pass `payment_method_types` when
creating sessions, as the current Stripe API rejects that parameter.
Session creation failures are logged with their error type and code. Customers
remain on checkout with their bag and entered details intact so they can retry.

## Sales reconciliation

Control Panel → Sales includes card refund and payment-failure reconciliation.
Use **Refresh from Stripe** to refresh cached charges, refunds, and failed charge
attempts for the selected calendar period. Incomplete refreshes are provisional.
Refund totals are cumulative refunds on payments made in that period, not a
refund-date ledger. Cash and gift-card refunds appear in completed sales details.

Failure details compare Stripe attempts with current system order states and
flag unmatched attempts, amount/status mismatches, and failed orders without
a Stripe charge. A failed attempt followed by a paid order is shown separately.
Failed amounts never count as revenue. Stripe uses the charge attempt date;
system failed orders use payment date or, when unavailable, order creation date.
Expired checkouts and failures before charge creation may have no Stripe charge.
The detail links retain the period and category when paging.

Staff enter the initial refund reason when initiating a refund from the order
form. The refund details table shows internal **Notes** read-only, with an
**Edit** link to append additional notes without replacing or clearing the
original reason (up to 2,000 characters in total). Notes on imported Stripe
transactions survive order deletion and are not overwritten by Stripe refreshes.
For an unsynced order, notes are initially stored on the order and copied when
its Stripe transaction is first imported. Refresh Stripe before deleting such an
order to preserve those notes. Notes do not mark a mismatch as reconciled.
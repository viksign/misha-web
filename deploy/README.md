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

For SendGrid SMTP, the shop automatically uses `shop.email_backend.EmailBackend`
to disable click wrapping on every outgoing message, including password resets,
order confirmations and staff notifications. Other SMTP providers and test
backends are unchanged. Keep SendGrid **Settings > Tracking > Click Tracking**
disabled for HTML and plain text until link branding is repaired.

The branded tracking hostname `url6798.mishaislandheritage.com` must be a
**DNS-only CNAME to `click.sendgrid.net`**, not routed/proxied to the shop.
SendGrid also requires the verification records displayed under **Sender
Authentication > Link Branding**. Verify those records and HTTPS link handling
with SendGrid before enabling tracking. Do not add the hostname to Django's
allowed hosts: the shop cannot decode SendGrid tracking tokens.
Disabling tracking protects newly sent messages; already wrapped email links
still require the DNS/link-branding repair.

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

For Cloudflare visitor locations, enable **Rules → Transform Rules → Managed
Transforms → Add visitor location headers**. Nginx forwards Cloudflare's city,
region, country, latitude and longitude headers only when the connecting proxy
is in Cloudflare's trusted IP ranges. New analytics events store that
approximate location with the event; coordinates are approximate, not a precise
visitor address. Existing events keep their cached IP-lookup location and are
not retroactively changed. Requests without Cloudflare location data continue
to use the cached IP lookup where available.

Site activity includes a **Connections by location** doughnut chart that follows
the selected date/time and user filters. Connections mean tracked page views
(not sessions or live sockets); each city/region/country label shows its
distinct IP count. Unresolved or missing IP locations are grouped as
**Unknown**, and missing IP addresses do not contribute to unique-IP counts.

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

The storefront header uses icon shortcuts with accessible names and hover
titles. Above 1200px all five links are visible. At 1001-1200px four remain,
at 761-1000px three remain, and at 760px or below Home and Jewellery remain.
Hidden links move into the burger menu, in the same order. At 480px or below
the logo occupies its own row above navigation and account/bag actions.
When changing the header, check guest, signed-in customer, and staff layouts
at each breakpoint and verify the logo never overlaps interactive controls.

```bash
docker compose up -d --build --remove-orphans
```

## Checks

### Local standby hostname on Cloudflare

This maintenance configuration is for the local standby host only. The
standby also continuously replicates Azure's database and media; see
"Live standby replication" below for setup, monitoring, failover and
failback.

In Cloudflare DNS for `mishaislandheritage.com`, add:

| Type | Name | Content | Proxy status | TTL |
| --- | --- | --- | --- | --- |
| A | `www2` | `82.8.59.50` | DNS only initially | 5 minutes |

Remove any conflicting `www2` CNAME or obsolete AAAA record. Forward public
TCP ports 80 and 443 to the local server. Obtain its certificate before
enabling Cloudflare proxying; if proxying is enabled afterward, use
**Full (strict)** TLS, not Flexible. Do not change the active site's DNS yet.

On the already deployed local server, keep the existing main-domain
certificate and its renewal configuration. With its normal Nginx running,
request the additional certificate using the existing HTTP challenge route:

```bash
docker compose run --rm --no-deps --entrypoint certbot certbot certonly \
  --webroot --webroot-path /var/www/certbot \
  --cert-name www2.mishaislandheritage.com \
  -d www2.mishaislandheritage.com \
  --email YOUR_CERTIFICATE_EMAIL --agree-tos --no-eff-email
```

Replace `YOUR_CERTIFICATE_EMAIL` with your certificate notification address.
This requires `www2` to resolve to the local server and port 80 to be reachable.
For a fresh local installation without the main-domain certificate, provision
that certificate separately before starting this override; the existing
main-domain bootstrap requires its HTTP challenges to reach this host.

Validate and activate standby routing on the **local host only**:

```bash
docker compose -f docker-compose.yaml -f docker-compose.standby.yaml \
  run --rm --no-deps --entrypoint nginx nginx -t
docker compose -f docker-compose.yaml -f docker-compose.standby.yaml \
  up -d --no-deps nginx certbot
curl -i https://www2.mishaislandheritage.com/
```

Expect HTTP **503**, a standby message, `X-Robots-Tag: noindex, nofollow`,
and `Cache-Control: no-store`. The local main-domain routes also return 503,
so visitors with cached local DNS cannot place orders there. All HTTPS paths,
including checkout, admin, media and payment webhooks, are blocked at Nginx;
there is deliberately no Django forwarding or need to allow `www2` in
Django's allowed hosts or CSRF origins.

The override removes Nginx's web-service startup dependency and places `web`
in the `promoted` profile, so a plain `docker compose up -d` on the standby
skips it. This matters because the web entrypoint runs migrations. Local
order-reminder and reconciliation schedulers must stay disabled; Nginx
maintenance does not stop host jobs.

After failover, `www2` is no longer a dedicated standby endpoint: normal
routing supports only the main domain. Do not add `www2` to Django to create
a second checkout.

The local main-domain certificate was issued manually and does **not**
auto-renew (it expires 26 January 2027). Renew it before then, or at failover
time once the main domain resolves to the local host, using the webroot
command above with `--cert-name mishaislandheritage.com -d mishaislandheritage.com -d www.mishaislandheritage.com`.

### Live standby replication

Azure is the only writable primary. The local host is a warm standby: its
MySQL is a read-only GTID replica of Azure, and uploaded media is mirrored
every five minutes. Each host's role is set in its `.env` file through
`COMPOSE_FILE`, so the usual `docker compose up -d --build --remove-orphans`
keeps the right services on each host:

| Host | `COMPOSE_FILE` | Extra services |
| --- | --- | --- |
| Azure (primary) | `docker-compose.yaml:docker-compose.primary.yaml` | `db-relay` (127.0.0.1:3307 → MySQL), `media-export` (read-only rsync, 127.0.0.1:8730) |
| Local (standby) | `docker-compose.yaml:docker-compose.standby.yaml:docker-compose.replica.yaml` | `replication-tunnel`, `media-sync`; `web` skipped |

Each host's `.env` also sets `MISHA_REPLICATION_DIR` (a mode-700 directory
outside the repository). On Azure this holds `rsyncd.secrets`
(`mediasync:<password>`). On the standby it holds `tunnel_ed25519`,
`known_hosts` (Azure's pinned SSH host key) and `rsync.password`. The standby
also sets `REPLICATION_PRIMARY_HOST` to Azure's public IP.

The tunnel logs in to Azure as the system user `misha-replica`, which has no
shell or password. Its `authorized_keys` entry is limited to the two
loopback forwards:

```text
restrict,port-forwarding,permitopen="127.0.0.1:3307",permitopen="127.0.0.1:8730",command="/usr/sbin/nologin" ssh-ed25519 AAAA... misha-standby-tunnel@dkhost01
```

MySQL settings (applied with `SET PERSIST`):

- Both hosts: `gtid_mode=ON`, `enforce_gtid_consistency=ON`. Azure is
  `server_id=1`, and the standby is `server_id=2` with `read_only`,
  `super_read_only` and `relay_log_recovery` enabled.
- Azure has the replication account `repl`@`172.18.%` (`REQUIRE SSL`,
  `REPLICATION SLAVE` only). The standby connects through
  `replication-tunnel:3306` using `SOURCE_SSL=1` and `SOURCE_AUTO_POSITION=1`.
- Azure's event `misha_replication.heartbeat_tick` updates a heartbeat row
  every 30 seconds. It is disabled on the replica, so the row's age on the
  standby measures end-to-end lag.

Run MySQL account changes on Azure with `SET SESSION sql_log_bin=0;`
first. Accounts differ between hosts, so a replicated `CREATE USER` or
`ALTER USER` stops the replica's SQL thread. If one slips through, skip it
on the standby with an empty transaction for the failing GTID
(`SET GTID_NEXT='<uuid>:<n>'; BEGIN; COMMIT; SET GTID_NEXT='AUTOMATIC'; START REPLICA;`).
Azure keeps binary logs for 30 days. A standby offline for longer must be
re-seeded from a fresh dump.

#### Monitoring

On the standby, `deploy/replication-status.sh` checks that both replica
threads are running, the heartbeat is under 120 seconds old,
`super_read_only` is on, and `media-sync` is healthy. It exits non-zero on
failure. If `MISHA_ALERT_EMAIL` is set, it sends one email through the app's
SMTP settings whenever the state changes. Install it in the standby user's
crontab:

```cron
*/5 * * * * MISHA_ALERT_EMAIL=you@example.com /opt/misha-web/deploy/replication-status.sh >> $HOME/.local/state/misha/replication-status.log 2>&1
```

Releases: deploy the same commit to both hosts. Azure is deployed first,
because migrations run there and replicate. On the standby,
`docker compose build web` prepares the image without starting it, and
`docker compose --profile promoted run --rm --no-deps --entrypoint python web manage.py migrate --check`
confirms that the standby code matches the replicated schema.

#### Failover (Azure unavailable)

Up to a few seconds of the most recent orders may be lost. Run on the standby:

1. **Fence Azure** so it cannot take orders if it comes back: stop its VM
   or its `web` container, or confirm the VM is down. Never run both hosts
   writable at the same time.
2. Check how far replication got:
   `./deploy/replication-status.sh` and `SHOW REPLICA STATUS\G`
   (`Retrieved_Gtid_Set` should equal `Executed_Gtid_Set`).
3. Promote the database:

   ```sql
   STOP REPLICA; RESET REPLICA ALL;
   SET PERSIST super_read_only = OFF; SET PERSIST read_only = OFF;
   ```

4. In `.env`, set `COMPOSE_FILE=docker-compose.yaml` (the base file only),
   then remove the replication services and start the live site:

   ```bash
   docker compose -f docker-compose.yaml -f docker-compose.standby.yaml -f docker-compose.replica.yaml \
     stop replication-tunnel media-sync
   docker compose up -d --build --remove-orphans
   curl --resolve www.mishaislandheritage.com:443:127.0.0.1 -I https://www.mishaislandheritage.com/
   ```

5. Point the main domain's Cloudflare records (`@` and `www`) to
   `82.8.59.50`. Keep them proxied, with SSL mode **Full (strict)**.
6. Enable the production jobs on the local host (on Azure they run as root
   cron and as `dockadmin`'s crontab). Adjust the reminder script's
   lock and log paths for the local user:

   ```cron
   0 3 * * * /opt/misha-web/deploy/stripe-reconciliation.sh >> $HOME/.local/state/misha/stripe-reconciliation.log 2>&1
   */5 * * * * cd /opt/misha-web && flock -n $HOME/.local/state/misha/order-reminders.lock docker compose exec -T web python manage.py send_order_reminders >> $HOME/.local/state/misha/order-reminders.log 2>&1
   ```

7. Check that Stripe webhooks are reaching the site and test checkout.

#### Failback (return to Azure)

Azure's data is now stale. Do not just start it.

1. On Azure, keep `web` stopped. Back up its database, then restore a fresh
   dump of the local database
   (`mysqldump --single-transaction --set-gtid-purged=ON --triggers --routines --events`).
   Copy media from local to Azure.
2. Optionally, replicate local → Azure briefly to catch up. Then choose a
   short maintenance window: stop local `web`, and confirm Azure has the
   final GTID.
3. Start Azure `web`, switch Cloudflare back to Azure, and move the jobs back.
4. Re-seed the local standby from Azure, as in the initial setup: reset local
   replication state, restore an Azure dump taken with
   `--set-gtid-purged=ON`, re-enable read-only, `CHANGE REPLICATION SOURCE`
   with `SOURCE_AUTO_POSITION=1`, and restore the standby `COMPOSE_FILE`.

### Daily Stripe reconciliation

Production runs `deploy/stripe-reconciliation.sh` as root at **03:00 UTC**
daily using `/etc/cron.d/misha-stripe-reconciliation`. This imports the current
and previous calendar years using `python manage.py sync_stripe_reconciliation`;
older years can still be refreshed manually in Sales & reconciliation.
It only imports reconciliation records, never charges/refunds customers or
changes orders. File locking prevents overlapping scheduled jobs; the shared
cache lock prevents overlap with the manual refresh button.

Output and failures go to `/var/log/misha-stripe-reconciliation.log`. A partial
or failed Stripe sync exits nonzero and remains marked incomplete in the
finance dashboard. Check this log and the dashboard's last refresh status
regularly. A manual run uses the same shell script.
The host scheduler uses UTC (04:00 UK summer time / 03:00 UK winter time).

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

The bag is retained until payment confirmation. A persistent, browser-scoped
checkout attempt serializes retries and resumes the same open Stripe session
when the validated addresses, items, prices and delivery cost are unchanged.
Returning from Stripe keeps the form filled in. Changes expire the old unpaid
session before a replacement order is created; paid or asynchronously processing
sessions lead to the existing order instead of a second payment.

Session creation uses an order-specific Stripe idempotency key. Failed creation
retains the unpaid order/attempt so that a timeout can be retried without
orphaning a potentially created Stripe session. Do not delete these attempts
while a payment could still be outstanding.
Confirmed payment removes the purchased quantities from that browser's current
bag once, preserving additions made afterward. Webhooks do not clear unrelated
bags. Late failure/expiration events cannot overwrite an already paid order.
Stripe success and cancel redirects use the validated incoming request hostname,
so customers keep their host-only session whether they start on `www` or the
apex domain. Session cookies are not shared with email-provider subdomains.

`ORDER_TRACKING_SITE_URL` selects the public hostname for one-click order links
in confirmation and dispatch emails; production uses
`https://www.mishaislandheritage.com`. It defaults to `SITE_URL` if unset and
does not change the rest of the shop's canonical URLs. Signed tokens open the
specific order without entering an email, order reference or postcode.

Control Panel > Orders has a Payment confirmation filter and labels unpaid
checkout attempts explicitly. Its default remains all orders for compatibility.
Use **Payment confirmed** for fulfilment; it includes refunded payments so their
history remains visible. Django Admin also shows payment status and the unpaid
attempt warning. Fulfilment **Completed** is not the same as payment **Paid**.

Staff reminder links use `/controlpanel/orders/?edit=ORDER_ID`. The direct-link
view shows that order even outside the current date/search filters and opens
its management dialog automatically. A **View all orders** link returns to
the normal list. Staff authentication and MFA are still required.

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
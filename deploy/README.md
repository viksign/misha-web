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
#!/bin/sh
set -eu

domain="mishaislandheritage.com"
domains="$(sed -n 's/^CERTBOT_DOMAINS=//p' .env | tail -n 1 | tr ',' ' ')"
email="$(sed -n 's/^CERTBOT_EMAIL=//p' .env | tail -n 1)"
staging="$(sed -n 's/^CERTBOT_STAGING=//p' .env | tail -n 1)"

if [ -z "$email" ]; then
    echo "CERTBOT_EMAIL must be set in .env" >&2
    exit 1
fi
if [ -z "$domains" ] || [ "${domains%% *}" != "$domain" ]; then
    echo "CERTBOT_DOMAINS must start with $domain" >&2
    exit 1
fi

if docker compose run --rm --no-deps --entrypoint sh nginx \
    -c "test -f /etc/letsencrypt/live/$domain/fullchain.pem"; then
    echo "A certificate for $domain already exists."
    docker compose up -d
    exit 0
fi

echo "Creating a temporary certificate so Nginx can start..."
docker compose --profile bootstrap run --rm --no-deps certbot-init sh -c \
    "apk add --no-cache openssl >/dev/null && \
    mkdir -p /etc/letsencrypt/live/$domain && \
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -keyout /etc/letsencrypt/live/$domain/privkey.pem \
      -out /etc/letsencrypt/live/$domain/fullchain.pem \
      -subj '/CN=localhost' >/dev/null 2>&1"

docker compose up -d --wait db web nginx

echo "Requesting the Let's Encrypt certificate..."
docker compose run --rm --no-deps --entrypoint sh certbot -c \
    "rm -rf /etc/letsencrypt/live/$domain"

domain_args=""
for item in $domains; do
    domain_args="$domain_args -d $item"
done

staging_arg=""
if [ "$staging" = "1" ]; then
    staging_arg="--staging"
fi

docker compose run --rm --no-deps --entrypoint certbot certbot certonly \
    --webroot --webroot-path /var/www/certbot \
    $staging_arg $domain_args \
    --email "$email" --rsa-key-size 4096 --agree-tos --no-eff-email

docker compose exec nginx nginx -s reload
docker compose up -d
echo "HTTPS deployment is running for $domain."
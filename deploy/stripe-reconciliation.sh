#!/bin/sh
set -eu
exec /usr/bin/flock -n /var/lock/misha-stripe-reconciliation.lock \
  /usr/bin/docker exec misha-web-web-1 python manage.py sync_stripe_reconciliation

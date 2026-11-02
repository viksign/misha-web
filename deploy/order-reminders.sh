#!/bin/sh
set -eu
cd /opt/misha-web
exec /usr/bin/flock -n /home/dockadmin/.local/state/misha/order-reminders.lock /usr/bin/docker compose exec -T web python manage.py send_order_reminders

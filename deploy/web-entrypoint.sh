#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput
chown -R app:app /app/staticfiles /app/media

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile - \
    --user app \
    --group app
#!/bin/sh
# Standby health check: MySQL replica threads, heartbeat lag, read-only state
# and the media mirror. Exits non-zero and prints FAIL lines when unhealthy.
#
# Optional alerting: set MISHA_ALERT_EMAIL to send one email when the state
# changes (OK -> FAIL or FAIL -> OK). Mail is sent with the web image's Django
# settings without starting the web service or running migrations.
set -u
cd "$(dirname "$0")/.."

MAX_LAG_SECONDS=${MAX_LAG_SECONDS:-120}
STATE_DIR=${MISHA_STATE_DIR:-$HOME/.local/state/misha}
mkdir -p "$STATE_DIR"

sql() {
  printf '%s\n' "$1" | docker compose exec -T db sh -c \
    'mysql -N -B -u root -p"$MYSQL_ROOT_PASSWORD"' 2>/dev/null
}

problems=""
fail() { problems="${problems}FAIL: $1
"; }

status=$(printf 'SHOW REPLICA STATUS\\G\n' | docker compose exec -T db sh -c \
  'mysql -u root -p"$MYSQL_ROOT_PASSWORD"' 2>/dev/null)
field() { printf '%s\n' "$status" | sed -n "s/^ *$1: //p" | head -n 1; }

if [ -z "$status" ]; then
  fail "no replica status (database down or replication not configured)"
else
  io=$(field Replica_IO_Running)
  sqlt=$(field Replica_SQL_Running)
  [ "$io" = "Yes" ] || fail "Replica_IO_Running=$io $(field Last_IO_Error)"
  [ "$sqlt" = "Yes" ] || fail "Replica_SQL_Running=$sqlt $(field Last_SQL_Error)"
fi

lag=$(sql "SELECT TIMESTAMPDIFF(SECOND, ts, UTC_TIMESTAMP(6)) FROM misha_replication.heartbeat WHERE id=1")
case "$lag" in
  ''|*[!0-9-]*) fail "heartbeat unreadable" ;;
  *) [ "$lag" -le "$MAX_LAG_SECONDS" ] || fail "heartbeat is ${lag}s old (limit ${MAX_LAG_SECONDS}s)" ;;
esac

ro=$(sql "SELECT @@global.super_read_only")
[ "$ro" = "1" ] || fail "super_read_only is not ON (standby database is writable)"

media=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q media-sync 2>/dev/null)" 2>/dev/null)
[ "$media" = "healthy" ] || fail "media-sync is ${media:-not running}"

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ -z "$problems" ]; then
  state=OK
  echo "$now OK lag=${lag}s media=$media"
else
  state=FAIL
  printf '%s\n%s' "$now FAIL" "$problems"
fi

previous=$(cat "$STATE_DIR/replication-state" 2>/dev/null || echo OK)
printf '%s\n' "$state" > "$STATE_DIR/replication-state"

if [ -n "${MISHA_ALERT_EMAIL:-}" ] && [ "$state" != "$previous" ]; then
  body="$now standby replication is $state on $(hostname)
$problems"
  printf '%s' "$body" | docker compose run --rm --no-deps -T --entrypoint python \
    -e ALERT_TO="$MISHA_ALERT_EMAIL" -e ALERT_SUBJECT="[misha standby] replication $state" \
    web -c 'import os, sys, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.core.mail import send_mail
send_mail(os.environ["ALERT_SUBJECT"], sys.stdin.read(), None, [os.environ["ALERT_TO"]])' \
    >/dev/null 2>&1 || echo "$now alert email failed"
fi

[ "$state" = OK ]

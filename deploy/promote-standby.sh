#!/bin/sh
# Promote the local standby to the live primary (failover).
#
#   ./deploy/promote-standby.sh --check   preflight only, changes nothing
#   ./deploy/promote-standby.sh           promote (asks for confirmation)
#
# Before promoting, make sure Azure cannot take orders: stop the Azure VM in
# the portal, or run "docker compose stop web" on it. After this script
# finishes, point the Cloudflare @ and www records to this server.
set -eu
cd "$(dirname "$0")/.."

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1
STATE_DIR=$HOME/.local/state/misha
mkdir -p "$STATE_DIR"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
sql() {
  printf '%s\n' "$1" | docker compose exec -T db sh -c \
    'mysql -N -B -u root -p"$MYSQL_ROOT_PASSWORD"' 2>/dev/null
}

say "Preflight"
grep -q '^COMPOSE_FILE=.*docker-compose.replica.yaml' .env \
  || die ".env is not in standby role (already promoted?)"
docker compose ps --status running db | grep -q db || die "database is not running"
docker image inspect misha-web-web >/dev/null 2>&1 || die "web image missing: run 'docker compose build web' first"

status=$(printf 'SHOW REPLICA STATUS\\G\n' | docker compose exec -T db sh -c \
  'mysql -u root -p"$MYSQL_ROOT_PASSWORD"' 2>/dev/null || true)
field() { printf '%s\n' "$status" | sed -n "s/^ *$1: //p" | head -n 1; }
echo "Replica IO thread : $(field Replica_IO_Running)  (No is expected if Azure is down)"
echo "Replica SQL thread: $(field Replica_SQL_Running)"
lag=$(sql "SELECT TIMESTAMPDIFF(SECOND, ts, UTC_TIMESTAMP(6)) FROM misha_replication.heartbeat WHERE id=1")
echo "Last data from Azure: ${lag}s ago"
[ "$(field Replica_SQL_Running)" = "Yes" ] || echo "WARNING: SQL thread stopped: $(field Last_SQL_Error)"

if [ "$CHECK_ONLY" = 1 ]; then
  say "Check only: nothing changed"
  exit 0
fi

cat <<'EOF'

This makes THIS server the live, writable shop.
  * Azure must already be stopped/fenced. Two writable shops = lost orders.
  * Afterwards Azure is out of date and must be re-seeded before failback.
EOF
printf 'Type PROMOTE to continue: '
read -r answer || true
[ "$answer" = "PROMOTE" ] || die "aborted"

say "Applying already-downloaded changes (up to 120s)"
i=0
while [ $i -lt 120 ]; do
  done_applying=$(sql "SELECT GTID_SUBSET(RECEIVED_TRANSACTION_SET, @@global.gtid_executed)
    FROM performance_schema.replication_connection_status LIMIT 1")
  [ "$done_applying" = "1" ] && break
  sleep 2; i=$((i + 2))
done
[ "${done_applying:-0}" = "1" ] || echo "WARNING: not every received transaction was applied"

say "Final media sync (skipped if Azure is unreachable)"
docker compose exec -T media-sync sh -c \
  'timeout 120 rsync -a --timeout=60 --password-file=/tmp/rsync.password rsync://mediasync@replication-tunnel/media/ /media/' \
  || echo "Media sync skipped; using the last mirror ($(docker compose exec -T media-sync cat /tmp/last-success 2>/dev/null || echo unknown))"

say "Promoting database to writable primary"
sql "STOP REPLICA; RESET REPLICA ALL; SET PERSIST super_read_only = OFF; SET PERSIST read_only = OFF;" >/dev/null
[ "$(sql 'SELECT @@global.read_only')" = "0" ] || die "database is still read-only"

say "Switching role to primary in .env"
cp -p .env "$STATE_DIR/env.before-promote.$(date -u +%Y%m%dT%H%M%SZ)"
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=docker-compose.yaml/' .env

say "Starting live site (runs migrations; replication services removed)"
docker compose up -d --remove-orphans --wait --wait-timeout 300 db cache web nginx
docker compose up -d certbot

say "Verifying HTTPS"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
  --resolve www.mishaislandheritage.com:443:127.0.0.1 https://www.mishaislandheritage.com/)
echo "Local main site returned HTTP $code"
[ "$code" = "200" ] || echo "WARNING: expected 200; check 'docker compose logs web nginx'"

say "Enabling production jobs and disabling standby monitoring (crontab)"
repo=$(pwd)
{
  crontab -l 2>/dev/null | grep -v -e replication-status.sh -e send_order_reminders -e stripe-reconciliation.sh || true
  echo "*/5 * * * * cd $repo && flock -n $STATE_DIR/order-reminders.lock docker compose exec -T web python manage.py send_order_reminders >> $STATE_DIR/order-reminders.log 2>&1"
  echo "0 3 * * * $repo/deploy/stripe-reconciliation.sh >> $STATE_DIR/stripe-reconciliation.log 2>&1"
} | crontab -

cat <<'EOF'

==> Promotion complete. Now:
  1. Cloudflare DNS: point @ and www (A records) to 82.8.59.50, keep Proxied,
     SSL mode Full (strict).
  2. Place a test order and confirm the confirmation email arrives.
  3. Keep Azure stopped. Failback: see deploy/README.md "Failback".
EOF

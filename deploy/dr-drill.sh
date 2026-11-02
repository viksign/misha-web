#!/bin/sh
# Disaster-recovery drill on the standby: starts a private, throwaway copy of
# the shop from a snapshot of the replica. Replication keeps running, and the
# drill can't charge real cards or email customers.
#
#   ./deploy/dr-drill.sh up      snapshot replica + start copy on 127.0.0.1:8081
#   ./deploy/dr-drill.sh status  show containers and the URL
#   ./deploy/dr-drill.sh emails  show emails the copy "sent" (printed, not sent)
#   ./deploy/dr-drill.sh down    stop and delete the copy and its data
#
# Optional: DRILL_STRIPE_SECRET_KEY=sk_test_... to test checkout in Stripe test
# mode (card 4242 4242 4242 4242). Live keys are refused.
set -eu
cd "$(dirname "$0")/.."

export DRILL_PORT=${DRILL_PORT:-8081}
STATE_DIR=$HOME/.local/state/misha
PW_FILE=$STATE_DIR/drill-db.pw
DC="docker compose -f deploy/drill/docker-compose.drill.yaml"
mkdir -p "$STATE_DIR"

case "${DRILL_STRIPE_SECRET_KEY:-}" in
  ''|sk_test_*|rk_test_*) ;;
  *) echo "ERROR: DRILL_STRIPE_SECRET_KEY must be a Stripe TEST key" >&2; exit 1 ;;
esac

load_pw() {
  [ -f "$PW_FILE" ] || { echo "No drill running (run: $0 up)" >&2; exit 1; }
  DRILL_DB_PASSWORD=$(cat "$PW_FILE"); export DRILL_DB_PASSWORD
}

case "${1:-}" in
up)
  if [ -f "$PW_FILE" ]; then
    echo "A drill already exists; run '$0 down' first." >&2; exit 1
  fi
  docker image inspect misha-web-web >/dev/null 2>&1 \
    || { echo "web image missing: run 'docker compose build web'" >&2; exit 1; }
  ( umask 077; head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$PW_FILE" )
  load_pw

  echo "==> Starting drill database"
  $DC up -d --wait db cache

  echo "==> Copying a consistent snapshot of the replica (replication keeps running)"
  docker compose exec -T db sh -c 'exec mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" \
      --single-transaction --set-gtid-purged=OFF --routines --triggers --no-tablespaces djapp' 2>/dev/null \
    | $DC exec -T db sh -c 'exec mysql -u root -p"$MYSQL_ROOT_PASSWORD" djapp' 2>/dev/null
  orders=$($DC exec -T db sh -c 'mysql -N -B -u root -p"$MYSQL_ROOT_PASSWORD" djapp -e "SELECT COUNT(*) FROM shop_order"' 2>/dev/null)
  echo "    snapshot loaded: $orders orders"

  echo "==> Starting drill web (applies pending migrations to the copy only)"
  $DC up -d --wait --wait-timeout 300 web nginx

  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:$DRILL_PORT/")
  echo "==> Home page returned HTTP $code"
  cat <<EOF

Drill copy is running. From your PC:
  ssh -L $DRILL_PORT:127.0.0.1:$DRILL_PORT dockerhost2
then browse http://localhost:$DRILL_PORT/
Stripe: ${DRILL_STRIPE_SECRET_KEY:+test mode}${DRILL_STRIPE_SECRET_KEY:-disabled}. Emails: '$0 emails'. Finish with: $0 down
EOF
  ;;
status)
  load_pw; $DC ps; echo "URL (via SSH tunnel): http://localhost:$DRILL_PORT/" ;;
emails)
  load_pw; $DC logs --no-log-prefix web | sed -n '/^Content-Type: text\/plain/,/^-\{20,\}/p' | grep -v '^Content-' ;;
logs)
  load_pw; $DC logs --tail=100 web ;;
down)
  load_pw
  $DC down -v --remove-orphans
  rm -f "$PW_FILE"
  echo "Drill removed." ;;
*)
  sed -n '2,13p' "$0"; exit 1 ;;
esac

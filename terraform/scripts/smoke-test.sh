#!/usr/bin/env bash
# End-to-end sanity check of the edge: DNS resolves to the jump, Caddy is routing
# to a VM, and all VMs report ready. Does NOT deploy an app, so a 502 for the
# app host is EXPECTED until an attendee pushes one — it still proves the path.
set -euo pipefail
cd "$(dirname "$0")/.."

JUMP="$(terraform output -raw jump_public_ip)"
DOMAIN="$(terraform output -raw base_domain)"
HOST="myapp-user01.${DOMAIN}"

echo "== DNS =="
RESOLVED="$(dig +short "$HOST" | tail -1 || true)"
echo "  $HOST -> ${RESOLVED:-<none>}  (expect ${JUMP})"
[ "$RESOLVED" = "$JUMP" ] && echo "  OK" || echo "  WARN: wildcard DNS not propagated yet"

echo "== Caddy edge =="
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${HOST}" || echo 000)"
echo "  GET http://${HOST} -> ${CODE}"
case "$CODE" in
  200) echo "  OK (an app is already deployed)" ;;
  502|503|404) echo "  OK: Caddy is routing to the VM (no app pushed yet)" ;;
  000) echo "  FAIL: no response from the jump (check firewall / Caddy)" ;;
  *)   echo "  Unexpected: ${CODE}" ;;
esac

echo "== VM readiness =="
./scripts/check-ready.sh

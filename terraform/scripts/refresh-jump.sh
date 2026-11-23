#!/usr/bin/env bash
# Push the current VM list and proxy config to the RUNNING jump host.
#
# The jump host learns its VM list only at first boot, and Terraform cannot
# update a running instance's boot script. So after any `terraform apply` that
# adds or removes VMs (e.g. a higher vm_count), run this:
#
#   terraform apply
#   ./scripts/check-ready.sh        # wait until the new VMs are built
#   ./scripts/refresh-jump.sh
#   -> then click "Sync pool from keyctl" on the app's /admin page
#
# What it changes on the jump host (each file is backed up first):
#   /etc/workshop/vm_pool.json   the pool that keyctl, the app and workshop-check use
#   /etc/caddy/Caddyfile         <app>-userNN.<base_domain> -> that user's VM
# Attendee keys, the host key and running SSH sessions are not touched.
#
# Both files are rendered by Terraform from its state, so they always match
# what `terraform apply` built. Nothing is applied by this script.
#
# Options:
#   --dry-run   show what would change, change nothing
#   --force     allow a refresh that REMOVES VMs the jump host currently lists
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force)   FORCE=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Operator key: OPERATOR_KEY, else the path in terraform.tfvars, else the default.
tfvars_key="$(sed -n 's/^[[:space:]]*operator_ssh_private_key_path[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' terraform.tfvars 2>/dev/null | head -1)"
KEY="${OPERATOR_KEY:-${tfvars_key:-$HOME/.ssh/id_ed25519}}"
KEY="${KEY/#\~/$HOME}"
[ -r "$KEY" ] || { echo "operator key not readable: $KEY (set OPERATOR_KEY=...)" >&2; exit 1; }

JUMP="$(terraform output -raw jump_public_ip)"

# Render from Terraform's own locals. base64 keeps the multi-line text intact
# through `terraform console`, which prints strings quoted.
render() {
  local out
  out="$(echo "base64encode($1)" | terraform console 2>&1 | tail -1)"
  case "$out" in
    \"*\") printf '%s' "${out//\"/}" ;;
    *) echo "could not render $1 from Terraform: $out" >&2; return 1 ;;
  esac
}
POOL_B64="$(render local.pool_json)"
CADDY_B64="$(render local.caddyfile)"

N_NEW="$(printf '%s' "$POOL_B64" | base64 -d | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
[ "$N_NEW" -gt 0 ] || { echo "Terraform's pool is empty; refusing to push it." >&2; exit 1; }

echo "Jump host:  ${JUMP}"
echo "Terraform:  ${N_NEW} VMs"

# The payload travels on stdin ahead of the remote script, not on the command
# line, so a few hundred VMs don't hit the kernel's per-argument size limit.
{
  printf 'POOL_B64=%s\nCADDY_B64=%s\nDRY_RUN=%s\nFORCE=%s\n' \
         "$POOL_B64" "$CADDY_B64" "$DRY_RUN" "$FORCE"
  cat <<'REMOTE'
set -euo pipefail
POOL=/etc/workshop/vm_pool.json
CADDY=/etc/caddy/Caddyfile
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '%s' "$POOL_B64"  | base64 -d > "$TMP/vm_pool.json"
printf '%s' "$CADDY_B64" | base64 -d > "$TMP/Caddyfile"

# Compare the pool on this host with the one Terraform rendered.
python3 - "$POOL" "$TMP/vm_pool.json" > "$TMP/diff" <<'PY'
import json, sys
def load(p):
    try:
        return {e["user"]: e["ip"] for e in json.load(open(p))}
    except FileNotFoundError:
        return {}
old, new = load(sys.argv[1]), load(sys.argv[2])
added   = sorted(set(new) - set(old))
removed = sorted(set(old) - set(new))
moved   = sorted(u for u in set(old) & set(new) if old[u] != new[u])
print(f"On this host: {len(old)} VMs  ->  after refresh: {len(new)} VMs")
if added:   print("  added:   " + ", ".join(f"{u} ({new[u]})" for u in added))
if removed: print("  REMOVED: " + ", ".join(f"{u} ({old[u]})" for u in removed))
if moved:   print("  address changed: " + ", ".join(f"{u} {old[u]} -> {new[u]}" for u in moved))
if not (added or removed or moved): print("  VM list unchanged")
print(f"__removed={len(removed)} __moved={len(moved)}")
PY
grep -v '^__' "$TMP/diff"
REMOVED="$(sed -n 's/.*__removed=\([0-9]*\).*/\1/p' "$TMP/diff")"
MOVED="$(sed -n 's/.*__moved=\([0-9]*\).*/\1/p' "$TMP/diff")"

# The proxy config must be valid before it goes anywhere near the live one.
if ! /usr/local/bin/caddy validate --config "$TMP/Caddyfile" --adapter caddyfile >"$TMP/validate" 2>&1; then
  echo "New proxy config is NOT valid; nothing changed:" >&2
  tail -5 "$TMP/validate" >&2
  exit 1
fi
if cmp -s "$TMP/Caddyfile" "$CADDY"; then echo "  proxy config unchanged"; else echo "  proxy config will be updated"; fi

# Dropping or re-addressing a VM cuts off whoever holds it. Make that explicit.
if [ "$FORCE" != 1 ] && { [ "${REMOVED:-0}" -gt 0 ] || [ "${MOVED:-0}" -gt 0 ]; }; then
  echo "Refusing: this would remove or re-address VMs that this jump host serves." >&2
  echo "Attendees on them would lose access. Re-run with --force if that is intended." >&2
  exit 3
fi

if [ "$DRY_RUN" = 1 ]; then
  echo "Dry run: nothing changed."
  exit 0
fi

TS="$(date +%Y%m%d-%H%M%S)"
cp -p "$POOL"  "$POOL.bak-$TS"  2>/dev/null || true
cp -p "$CADDY" "$CADDY.bak-$TS" 2>/dev/null || true
install -o root -g root -m 0644 "$TMP/vm_pool.json" "$POOL"
install -o root -g root -m 0644 "$TMP/Caddyfile"    "$CADDY"
install -o root -g root -m 0600 "$TMP/Caddyfile"    /etc/workshop/Caddyfile
systemctl reload caddy 2>/dev/null || systemctl restart caddy
sleep 1
[ "$(systemctl is-active caddy)" = active ] || {
  echo "Caddy did not come back; restoring the previous proxy config." >&2
  cp -p "$CADDY.bak-$TS" "$CADDY" && systemctl restart caddy
  exit 1
}
echo "Updated. Backups: $POOL.bak-$TS  $CADDY.bak-$TS"
REMOTE
} | ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 \
        -o StrictHostKeyChecking=accept-new "ops@${JUMP}" 'sudo bash -s'

if [ "$DRY_RUN" = 1 ]; then exit 0; fi
cat <<EOF

Next:
  1. ./scripts/check-ready.sh      (every VM, including new ones, should be OK)
  2. On the app's /admin page, click "Sync pool from keyctl" -> expect ${N_NEW} VMs.
EOF

#!/usr/bin/env bash
# Tests workshop-keyctl end to end with NO jump host and NO VMs.
# A stub ssh records the "dokku" calls so we exercise the full path.
#
# Run:  bash keyctl/test/test_keyctl.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../workshop-keyctl"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
check() { # check <desc> <condition-rc>
  if [ "$2" -eq 0 ]; then echo "  ok   - $1"; PASS=$((PASS+1));
  else echo "  FAIL - $1"; FAIL=$((FAIL+1)); fi
}

# --- fixtures -------------------------------------------------------------- #
cat > "$WORK/pool.json" <<'JSON'
[{"user":"user01","ip":"10.0.0.11"},{"user":"user02","ip":"10.0.0.12"}]
JSON
: > "$WORK/authkeys"

# stub ssh: logs argv + stdin, pretends dokku succeeded
cat > "$WORK/fake-ssh" <<'SH'
#!/usr/bin/env bash
echo "SSH_CALL: $*" >> "$FAKE_SSH_LOG"
cat >> "$FAKE_SSH_LOG" 2>/dev/null || true
exit 0
SH
chmod +x "$WORK/fake-ssh"

# a real keypair to feed in
ssh-keygen -t ed25519 -N "" -C "tester@laptop" -f "$WORK/id" >/dev/null
PUB="$(cat "$WORK/id.pub")"

export WORKSHOP_POOL_FILE="$WORK/pool.json"
export WORKSHOP_JUMP_AUTHKEYS="$WORK/authkeys"
export WORKSHOP_LOCK="$WORK/lock"
export WORKSHOP_VM_KEY="$WORK/id"
export WORKSHOP_SSH_BIN="$WORK/fake-ssh"
export WORKSHOP_NO_CHOWN=1
export FAKE_SSH_LOG="$WORK/ssh.log"
: > "$FAKE_SSH_LOG"

kc() { python3 "$SCRIPT" "$@"; }
ok_field() { python3 -c "import sys,json;print(json.load(sys.stdin).get('ok'))"; }

echo "# pool"
OUT="$(kc pool)"; echo "$OUT" | python3 -c "import sys,json;a=json.load(sys.stdin);assert isinstance(a,list) and a[0]['user']=='user01'"
check "pool prints JSON array" $?

echo "# add"
OUT="$(printf '%s\n' "$PUB" | kc add user01 user01)"
echo "$OUT" | grep -q '"ok":true'; check "add returns ok" $?
echo "$OUT" | grep -q '"idempotent":false'; check "add not idempotent first time" $?
grep -q 'permitopen="10.0.0.11:22"' "$WORK/authkeys"; check "authorized_keys got restricted line" $?
grep -q ' user01$' "$WORK/authkeys"; check "line is labelled user01" $?
grep -q 'ssh-keys:add user01' "$FAKE_SSH_LOG"; check "dokku ssh-keys:add was called" $?

echo "# add is idempotent"
OUT="$(printf '%s\n' "$PUB" | kc add user01 user01)"
echo "$OUT" | grep -q '"idempotent":true'; check "second identical add is idempotent" $?
test "$(grep -c 'permitopen' "$WORK/authkeys")" -eq 1; check "no duplicate authorized_keys line" $?

echo "# list"
OUT="$(kc list)"
echo "$OUT" | python3 -c "import sys,json;a=json.load(sys.stdin);assert any(r['vm_user']=='user01' and r['label']=='user01' for r in a)"
check "list shows the authorization" $?

echo "# remove"
OUT="$(kc remove user01 user01)"
echo "$OUT" | grep -q '"removed":1'; check "remove reports one removed" $?
! grep -q 'permitopen="10.0.0.11:22"' "$WORK/authkeys"; check "authorized_keys line is gone" $?
grep -q 'ssh-keys:remove user01' "$FAKE_SSH_LOG"; check "dokku ssh-keys:remove was called" $?

echo "# bad input is rejected (never trust the app)"
OUT="$(printf '%s\n' "$PUB" | kc add user99 user01)"; echo "$OUT" | grep -q '"ok":false'; check "unknown vm_user rejected" $?
OUT="$(printf '%s\n' "$PUB" | kc add user01 'Bad Label!')"; echo "$OUT" | grep -q '"ok":false'; check "bad label rejected" $?
OUT="$(printf 'not-a-key\n' | kc add user01 user01)"; echo "$OUT" | grep -q '"ok":false'; check "invalid key rejected" $?
OUT="$(printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\n' | kc add user01 user01)"; echo "$OUT" | grep -q '"ok":false'; check "private key rejected" $?

echo
echo "RESULT: $PASS passed, $FAIL failed"
test "$FAIL" -eq 0

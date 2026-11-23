# Jump host provisioning body (static — read verbatim by Terraform file(); the
# prelude above it has already written secrets/config under /etc/workshop and
# the keyctl script to /usr/local/bin). Shell ${} is safe here: not a template.
set -euo pipefail

chmod 600 /etc/workshop/vm_root_ed25519
chmod 644 /etc/workshop/vm_pool.json
chmod 755 /usr/local/bin/workshop-keyctl

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 curl

# --- Caddy (static binary — avoids the flaky cloudsmith apt-repo GPG) ------ #
# Pinned GitHub release. caddyserver.com/api/download now answers 401
# ("Please log in or use an API key"), which aborted this script under set -e.
curl -fsSL -o /tmp/caddy.tgz \
  "https://github.com/caddyserver/caddy/releases/download/v2.11.9/caddy_2.11.9_linux_amd64.tar.gz"
tar -xzf /tmp/caddy.tgz -C /tmp caddy
install -m 0755 /tmp/caddy /usr/local/bin/caddy
rm -f /tmp/caddy /tmp/caddy.tgz
id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
install -d -o caddy -g caddy -m 0755 /etc/caddy /var/lib/caddy
install -o root -g root -m 0644 /etc/workshop/Caddyfile /etc/caddy/Caddyfile
cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network-online.target
Requires=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile --adapter caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
Restart=on-failure
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable caddy
systemctl restart caddy

# --- users ---------------------------------------------------------------- #
# jump: attendee restricted keys land here; tunnel-only, no shell.
id jump >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin jump
install -d -o jump -g jump -m 700 /home/jump/.ssh
touch /home/jump/.ssh/authorized_keys
chown jump:jump /home/jump/.ssh/authorized_keys
chmod 600 /home/jump/.ssh/authorized_keys

# keyadmin: the signup app's only foothold — forced command, runs workshop-keyctl
# as root (needs root to chown jump's authorized_keys and read the VM key).
id keyadmin >/dev/null 2>&1 || useradd -m -s /bin/bash keyadmin
install -d -o keyadmin -g keyadmin -m 700 /home/keyadmin/.ssh
APP_KEY="$(cat /etc/workshop/app_public_key)"
printf 'command="sudo /usr/local/bin/workshop-keyctl",restrict %s signup-app\n' "$APP_KEY" \
  > /home/keyadmin/.ssh/authorized_keys
chown keyadmin:keyadmin /home/keyadmin/.ssh/authorized_keys
chmod 600 /home/keyadmin/.ssh/authorized_keys

cat > /etc/sudoers.d/keyadmin <<'SUDO'
Defaults!/usr/local/bin/workshop-keyctl env_keep += "SSH_ORIGINAL_COMMAND"
keyadmin ALL=(root) NOPASSWD: /usr/local/bin/workshop-keyctl
SUDO
chmod 440 /etc/sudoers.d/keyadmin
visudo -cf /etc/sudoers.d/keyadmin

# ops: operator admin login (normal shell + sudo) for the helper scripts.
if [ -s /etc/workshop/operator_public_key ]; then
  id ops >/dev/null 2>&1 || useradd -m -s /bin/bash ops
  usermod -aG sudo ops
  install -d -o ops -g ops -m 700 /home/ops/.ssh
  cat /etc/workshop/operator_public_key > /home/ops/.ssh/authorized_keys
  chown ops:ops /home/ops/.ssh/authorized_keys
  chmod 600 /home/ops/.ssh/authorized_keys
  echo 'ops ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ops
  chmod 440 /etc/sudoers.d/ops
fi

# --- readiness helper (used by scripts/check-ready.sh) -------------------- #
cat > /usr/local/bin/workshop-check <<'CHECK'
#!/usr/bin/env bash
set -uo pipefail
KEY=/etc/workshop/vm_root_ed25519
fail=0
while IFS='=' read -r u ip; do
  [ -n "$u" ] || continue
  if ssh -n -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=6 "root@$ip" \
       'command -v dokku >/dev/null && systemctl is-active --quiet nginx' >/dev/null 2>&1; then
    echo "OK    $u ($ip)"
  else
    echo "FAIL  $u ($ip)"
    fail=1
  fi
done < <(python3 -c 'import json
for v in json.load(open("/etc/workshop/vm_pool.json")): print(v["user"]+"="+v["ip"])')
if [ "$fail" = 0 ]; then echo "ALL READY"; else echo "SOME NOT READY"; fi
exit $fail
CHECK
chmod 755 /usr/local/bin/workshop-check

# --- sshd: forwarding on (for ProxyJump) ---------------------------------- #
if ! grep -q '^AllowTcpForwarding' /etc/ssh/sshd_config; then
  echo 'AllowTcpForwarding yes' >> /etc/ssh/sshd_config
fi

# Listen on 2222 as well as 22: the signup app's host may block outbound 22.
# Keys only; Civo's first-boot script turns password logins on, turn them off.
cat > /etc/ssh/sshd_config.d/10-workshop.conf <<'SSHD'
Port 22
Port 2222
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
# A whole room connects at once (ssh, git push). The default 10:30:100 starts
# dropping new connections past 10 that are still logging in; with 15 parallel
# logins that was 1-2 "Connection closed" errors per round.
MaxStartups 300:30:600
SSHD
sshd -t
# Ubuntu 24.04 runs sshd from ssh.socket; the port list comes from a
# generator that only re-reads sshd_config on daemon-reload.
systemctl daemon-reload
systemctl restart ssh.socket 2>/dev/null || true
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true

echo "jump host init complete"

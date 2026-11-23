# Attendee VM provisioning body (static — read verbatim by Terraform file(); the
# prelude above authorized the jump's vm_root key for root and exported
# WORKSHOP_BASE_DOMAIN and DOKKU_TAG). Shell ${} is safe here: not a template.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Ubuntu starts unattended-upgrades at first boot and it holds the dpkg lock
# for a minute or more. Any apt call during that window fails with "Could not
# get lock", and under `set -e` that aborted this whole script: about one VM
# in five came up without Dokku. Stop it, wait for the lock to clear, and make
# every later apt/dpkg call (including Dokku's own bootstrap) wait instead of
# failing.
systemctl stop unattended-upgrades.service apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
for _ in $(seq 1 120); do
  if ! pgrep -x apt-get >/dev/null && ! pgrep -x dpkg >/dev/null \
     && ! pgrep -f unattended-upgrade$ >/dev/null && ! pgrep -f 'unattended-upgrade ' >/dev/null; then
    break
  fi
  sleep 5
done
cat > /etc/apt/apt.conf.d/99-workshop-lock-timeout <<'APT'
DPkg::Lock::Timeout "600";
APT

apt-get update -y
apt-get install -y curl ca-certificates wget

# Root logs in by key only (the jump host's vm_root key).
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# --- Dokku ---------------------------------------------------------------- #
cd /root
wget -NP . "https://dokku.com/install/${DOKKU_TAG}/bootstrap.sh"
DOKKU_TAG="${DOKKU_TAG}" bash bootstrap.sh

# Apps are served as <app>.<base_domain> by dokku's nginx; the jump's Caddy maps
# myapp-userNN.<base_domain> -> this VM with Host: myapp.<base_domain>.
dokku domains:set-global "${WORKSHOP_BASE_DOMAIN}"

# --- k3s scheduler plugin ------------------------------------------------- #
# Installs the plugin and bootstraps a single-node k3s on this VM so attendees
# can deploy the same app via the k3s scheduler (myapp-k8s-userNN).
dokku plugin:install https://github.com/dokku/dokku-scheduler-k3s.git scheduler-k3s || true
dokku scheduler-k3s:initialize || true

echo "vm init complete"

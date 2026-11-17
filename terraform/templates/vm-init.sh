# Attendee VM provisioning body (static — read verbatim by Terraform file(); the
# prelude above authorized the jump's vm_root key for root and exported
# WORKSHOP_BASE_DOMAIN and DOKKU_TAG). Shell ${} is safe here: not a template.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
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

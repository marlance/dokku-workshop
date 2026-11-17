#!/usr/bin/env bash
# Check every attendee VM is built and ready (Dokku installed, nginx up).
# Runs the on-jump helper, which reaches the private VMs with the VM key.
set -euo pipefail
cd "$(dirname "$0")/.."

JUMP="$(terraform output -raw jump_public_ip)"
KEY="${OPERATOR_KEY:-$HOME/.ssh/id_ed25519}"   # override with OPERATOR_KEY=...

echo "Checking VMs via jump host ${JUMP} ..."
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "ops@${JUMP}" \
  'sudo /usr/local/bin/workshop-check'

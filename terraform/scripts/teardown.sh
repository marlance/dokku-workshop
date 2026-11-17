#!/usr/bin/env bash
# Destroy ALL workshop infrastructure (jump host + every attendee VM + DNS).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "This will DESTROY the jump host and all ${TF_VAR_vm_count:-} attendee VMs in this workspace."
echo "Type 'destroy' to confirm:"
read -r ans
[ "$ans" = "destroy" ] || { echo "aborted."; exit 1; }

terraform destroy -auto-approve
echo "Teardown complete."

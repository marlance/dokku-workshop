# Assembles the cloud-init scripts. To avoid Terraform-template vs. shell `${}`
# escaping hell, values are injected only in a small HCL-built "prelude" (which
# writes them to files / env), and the large provisioning bodies are static
# shell files read verbatim with file().

locals {
  vm_users = [for i in range(var.vm_count) : format("user%02d", i + 1)]

  # user -> reachable IP (private when VMs have no public IP), after create.
  vm_ips = {
    for u in local.vm_users : u => (
      var.vm_public_ip ? civo_instance.vm[u].public_ip : civo_instance.vm[u].private_ip
    )
  }

  # Pool consumed by `workshop-keyctl pool` on the jump host.
  pool_json = jsonencode([for u in local.vm_users : { user = u, ip = local.vm_ips[u] }])

  # Caddy reverse-proxy config: subdomain -> VM:80 with the Host rewritten to the
  # dokku vhost on that VM.
  caddyfile = templatefile("${path.module}/templates/Caddyfile.tftpl", {
    base_domain = var.base_domain
    vms         = local.vm_ips
  })

  # ---- jump host user-data ------------------------------------------------- #
  jump_prelude = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p /etc/workshop
    umask 077
    echo '${base64encode(tls_private_key.keyctl.public_key_openssh)}' | base64 -d > /etc/workshop/app_public_key
    echo '${base64encode(tls_private_key.vm_root.private_key_openssh)}' | base64 -d > /etc/workshop/vm_root_ed25519
    echo '${base64encode(var.operator_ssh_public_key)}' | base64 -d > /etc/workshop/operator_public_key
    echo '${base64encode(local.pool_json)}' | base64 -d > /etc/workshop/vm_pool.json
    echo '${base64encode(local.caddyfile)}' | base64 -d > /etc/workshop/Caddyfile
    echo '${base64encode(file("${path.module}/../keyctl/workshop-keyctl"))}' | base64 -d > /usr/local/bin/workshop-keyctl
  EOT

  jump_user_data = join("\n", [
    local.jump_prelude,
    file("${path.module}/templates/jump-init.sh"),
  ])

  # ---- attendee VM user-data ----------------------------------------------- #
  vm_prelude = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    echo '${base64encode(tls_private_key.vm_root.public_key_openssh)}' | base64 -d >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    export WORKSHOP_BASE_DOMAIN='${var.base_domain}'
    export DOKKU_TAG='${var.dokku_version}'
  EOT

  vm_user_data = join("\n", [
    local.vm_prelude,
    file("${path.module}/templates/vm-init.sh"),
  ])
}

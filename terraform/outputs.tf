output "jump_public_ip" {
  description = "Jump host public (reserved) IP."
  value       = civo_reserved_ip.jump.ip
}

output "jump_host" {
  description = "Friendly jump hostname (use as JUMP_HOST in the app)."
  value       = "jump.${var.base_domain}"
}

output "base_domain" {
  description = "Domain attendee apps are served under."
  value       = var.base_domain
}

output "vm_pool" {
  description = "The VM pool (user -> public IP) written to the jump host."
  value       = { for u in local.vm_users : u => local.vm_ips[u] }
}

output "keyctl_private_key" {
  description = "The app's keyctl private key. Set as KEYCTL_PRIVATE_KEY on the signup app (sensitive)."
  value       = tls_private_key.keyctl.private_key_openssh
  sensitive   = true
}

# Copy/paste helper for wiring the signup app to real infrastructure.
output "app_env" {
  description = "Env vars to set on the signup app to leave dry-run mode."
  value = {
    DRY_RUN       = "0"
    JUMP_HOST     = "jump.${var.base_domain}"
    JUMP_PUBLIC_IP = civo_reserved_ip.jump.ip
    BASE_DOMAIN   = var.base_domain
    KEYADMIN_USER = "keyadmin"
    # KEYCTL_PRIVATE_KEY -> `terraform output -raw keyctl_private_key`
    # KEYCTL_KNOWN_HOSTS -> `ssh-keyscan -t ed25519 <jump_public_ip>`
  }
}

output "next_steps" {
  description = "What to do after apply."
  value       = <<-EOT
    1. Wait for build to finish, then: ./scripts/check-ready.sh   (expect ALL READY)
    2. Pin the jump host key:   ssh-keyscan -t ed25519 ${civo_reserved_ip.jump.ip}
       -> set that as KEYCTL_KNOWN_HOSTS on the signup app.
    3. Set KEYCTL_PRIVATE_KEY:  terraform output -raw keyctl_private_key
    4. Set the rest of app_env (above) and unset DRY_RUN.
    5. In the app's /admin, click "Sync pool from keyctl" and confirm ${var.vm_count} VMs.
    6. Smoke-test one signup end to end:  ./scripts/smoke-test.sh
  EOT
}

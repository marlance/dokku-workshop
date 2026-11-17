data "civo_disk_image" "ubuntu" {
  filter {
    key      = "name"
    values   = [var.ubuntu_filter]
    match_by = "substring"
  }
}

# --------------------------------------------------------------------------- #
# Attendee VMs: user01..userNN. Identical build (Dokku + k3s scheduler); the
# per-attendee identity is just the hostname and which one the app assigns.
# --------------------------------------------------------------------------- #
resource "civo_instance" "vm" {
  for_each = toset(local.vm_users)

  hostname    = "${var.name_prefix}-${each.key}"
  size        = var.instance_size
  disk_image  = element(data.civo_disk_image.ubuntu.diskimages, 0).id
  network_id  = civo_network.main.id
  firewall_id = civo_firewall.vm.id
  sshkey_id   = civo_ssh_key.vm_root.id
  region      = var.region
  script      = local.vm_user_data
  volume_type = "standard"
  notes       = "Dokku on Civo workshop — attendee VM ${each.key}"
  tags        = [var.name_prefix, "attendee-vm"]
}

# --------------------------------------------------------------------------- #
# Jump host: Caddy ingress + SSH bastion + keyadmin forced command. Created
# after the VMs because its cloud-init embeds their IPs (pool + Caddyfile).
# --------------------------------------------------------------------------- #
resource "civo_instance" "jump" {
  hostname      = "${var.name_prefix}-jump"
  size          = var.jump_size
  disk_image    = element(data.civo_disk_image.ubuntu.diskimages, 0).id
  network_id    = civo_network.main.id
  firewall_id   = civo_firewall.jump.id
  sshkey_id     = civo_ssh_key.operator.id
  region        = var.region
  reserved_ipv4 = civo_reserved_ip.jump.ip
  script        = local.jump_user_data
  volume_type   = "standard"
  notes         = "Dokku on Civo workshop — jump host"
  tags          = [var.name_prefix, "jump"]

  depends_on = [civo_instance.vm]
}

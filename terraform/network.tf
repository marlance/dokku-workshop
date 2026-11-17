resource "civo_network" "main" {
  label  = "${var.name_prefix}-net"
  region = var.region
}

# Stable public IP for the jump host. Created independently of the jump instance
# so the VM firewall can reference it without a dependency cycle (VM firewall
# allows the jump's IP; the jump instance embeds the VMs' IPs).
resource "civo_reserved_ip" "jump" {
  name   = "${var.name_prefix}-jump"
  region = var.region
}

# --------------------------------------------------------------------------- #
# Jump host firewall — public. Attendees reach SSH (22, for ProxyJump) and
# HTTP (80, Caddy serving their apps) from anywhere.
# --------------------------------------------------------------------------- #
resource "civo_firewall" "jump" {
  name                 = "${var.name_prefix}-jump-fw"
  network_id           = civo_network.main.id
  region               = var.region
  create_default_rules = false

  ingress_rule {
    label      = "ssh"
    protocol   = "tcp"
    port_range = "22"
    cidr       = var.allowed_ssh_cidr
    action     = "allow"
  }

  ingress_rule {
    label      = "http"
    protocol   = "tcp"
    port_range = "80"
    cidr       = ["0.0.0.0/0"]
    action     = "allow"
  }

  egress_rule {
    label      = "all"
    protocol   = "tcp"
    port_range = "1-65535"
    cidr       = ["0.0.0.0/0"]
    action     = "allow"
  }
}

# --------------------------------------------------------------------------- #
# Attendee VM firewall — locked down. SSH (22) and HTTP (80) are reachable ONLY
# from the jump: over the private network when the VMs are private-only, or from
# the jump's reserved IP when they have public IPs. Everything attendee-facing
# goes through the jump. Egress is open so the VM can install Dokku/k3s.
# --------------------------------------------------------------------------- #
resource "civo_firewall" "vm" {
  name                 = "${var.name_prefix}-vm-fw"
  network_id           = civo_network.main.id
  region               = var.region
  create_default_rules = false

  ingress_rule {
    label      = "ssh-from-jump"
    protocol   = "tcp"
    port_range = "22"
    cidr       = var.vm_public_ip ? ["${civo_reserved_ip.jump.ip}/32"] : [civo_network.main.cidr_v4]
    action     = "allow"
  }

  ingress_rule {
    label      = "http-from-jump"
    protocol   = "tcp"
    port_range = "80"
    cidr       = var.vm_public_ip ? ["${civo_reserved_ip.jump.ip}/32"] : [civo_network.main.cidr_v4]
    action     = "allow"
  }

  egress_rule {
    label      = "all"
    protocol   = "tcp"
    port_range = "1-65535"
    cidr       = ["0.0.0.0/0"]
    action     = "allow"
  }
}

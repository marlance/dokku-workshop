variable "civo_token" {
  description = "Civo API token. Leave null to use the CIVO_TOKEN environment variable (recommended)."
  type        = string
  default     = null
  sensitive   = true
}

variable "region" {
  description = "Civo region to deploy into."
  type        = string
  default     = "mum1"
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "dokku-workshop"
}

variable "base_domain" {
  description = <<-EOT
    Domain that attendee apps are served under, e.g. "dokku.example.com".
    MUST be a zone managed in Civo DNS on this account — Terraform creates the
    wildcard (*) and jump A records in it. Attendee apps resolve as
    myapp-userNN.<base_domain> and myapp-k8s-userNN.<base_domain>.
  EOT
  type        = string
}

variable "vm_count" {
  description = "Number of attendee VMs to provision (attendees + spares). 12 for the dry run; bump to ~160 for the real 150-attendee workshop."
  type        = number
  default     = 12
}

variable "instance_size" {
  description = <<-EOT
    Civo size slug for each attendee VM (target: 8GB RAM / 4 CPU).
    Confirm the exact current slug with `civo size ls` — g3.large is a common
    name for that tier but Civo's catalogue changes.
  EOT
  type        = string
  default     = "g3.large"
}

variable "jump_size" {
  description = "Civo size slug for the jump host (Caddy + SSH bastion). Confirm with `civo size ls`."
  type        = string
  default     = "g3.medium"
}

variable "operator_ssh_public_key" {
  description = "Your SSH public key. Added to an 'ops' sudo user on the jump host so you can log in and run the scripts."
  type        = string
}

variable "operator_ssh_private_key_path" {
  description = "Path to the private key matching operator_ssh_public_key. Used only by the local helper scripts (check-ready/smoke-test) to SSH to the jump host."
  type        = string
  default     = "~/.ssh/id_ed25519"
}

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to reach the jump host on 22 and 80. Attendees connect from anywhere, so this is 0.0.0.0/0 by default."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "dokku_version" {
  description = "Dokku release tag to install on each VM (DOKKU_TAG). Confirm a current tag at https://github.com/dokku/dokku/releases."
  type        = string
  default     = "v0.35.20"
}

variable "ubuntu_filter" {
  description = "Substring used to select the Ubuntu disk image. Verify the available names with `civo diskimage ls`."
  type        = string
  default     = "ubuntu-24.04"
}

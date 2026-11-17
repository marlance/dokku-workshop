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
    Full domain attendee apps are served under, e.g. "workshop.example.com".
    Terraform creates wildcard (*) and jump A records for it in Cloudflare.
    Must sit inside var.cloudflare_zone. Attendee apps resolve as
    myapp-userNN.<base_domain> and myapp-k8s-userNN.<base_domain>.
  EOT
  type        = string
}

variable "cloudflare_zone" {
  description = "The Cloudflare zone (registrable domain) that contains base_domain, e.g. \"example.com\". Pick one of your Cloudflare domains."
  type        = string
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:DNS:Edit on cloudflare_zone. Leave null to use the CLOUDFLARE_API_TOKEN environment variable (recommended)."
  type        = string
  default     = null
  sensitive   = true
}

variable "vm_public_ip" {
  description = <<-EOT
    Whether attendee VMs get a public IP. false (recommended) = private-only:
    the VMs are unreachable from the internet and only the jump host can reach
    them over the private network — true network isolation. REQUIRES that Civo
    gives private-only instances outbound internet (NAT) so cloud-init can install
    Dokku/k3s; if it does not, set this true or add a NAT path.
  EOT
  type        = bool
  default     = false
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
  description = "Substring used to select the Ubuntu disk image. In mum1 the 24.04 image is named 'ubuntu-noble' (22.04 = 'ubuntu-jammy'). Verify with `civo diskimage ls --region <region>`."
  type        = string
  default     = "ubuntu-noble"
}

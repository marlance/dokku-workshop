# base_domain must already be a zone in Civo DNS on this account.
data "civo_dns_domain_name" "base" {
  name = var.base_domain
}

# Wildcard: every attendee app host (myapp-userNN.<base_domain>,
# myapp-k8s-userNN.<base_domain>) resolves to the jump host, where Caddy routes
# to the right VM by subdomain.
resource "civo_dns_domain_record" "wildcard" {
  domain_id = data.civo_dns_domain_name.base.id
  type      = "A"
  name      = "*"
  value     = civo_reserved_ip.jump.ip
  ttl       = 600
}

# Friendly name for the bastion itself (use as JUMP_HOST in the app / SSH config).
resource "civo_dns_domain_record" "jump" {
  domain_id = data.civo_dns_domain_name.base.id
  type      = "A"
  name      = "jump"
  value     = civo_reserved_ip.jump.ip
  ttl       = 600
}

# DNS in Cloudflare. base_domain must live inside cloudflare_zone (e.g.
# base_domain = "workshop.example.com", cloudflare_zone = "example.com").
#
# Records are DNS-only (proxied = false) on purpose: attendees SSH-ProxyJump
# through the jump host, and SSH can't traverse Cloudflare's HTTP proxy; a
# proxied record would also force Cloudflare TLS in front of Caddy's plain HTTP.
data "cloudflare_zone" "base" {
  name = var.cloudflare_zone
}

# Wildcard: every attendee app host (myapp-userNN.<base_domain>,
# myapp-k8s-userNN.<base_domain>) resolves to the jump host, where Caddy routes
# to the right VM by subdomain.
resource "cloudflare_record" "wildcard" {
  zone_id = data.cloudflare_zone.base.id
  name    = "*.${var.base_domain}"
  type    = "A"
  value   = civo_reserved_ip.jump.ip
  proxied = false
  ttl     = 300
}

# Friendly name for the bastion itself (use as JUMP_HOST in the app / SSH config).
resource "cloudflare_record" "jump" {
  zone_id = data.cloudflare_zone.base.id
  name    = "jump.${var.base_domain}"
  type    = "A"
  value   = civo_reserved_ip.jump.ip
  proxied = false
  ttl     = 300
}

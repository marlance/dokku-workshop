# The token is read from CIVO_TOKEN in the environment when var.civo_token is
# null (recommended — keep it out of tfvars/state input). `civo apikey current`
# shows the active one, or `export CIVO_TOKEN=...`.
provider "civo" {
  token  = var.civo_token
  region = var.region
}

# Reads CLOUDFLARE_API_TOKEN from the environment when var.cloudflare_api_token
# is null (recommended). The token needs Zone:DNS:Edit on the chosen zone.
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

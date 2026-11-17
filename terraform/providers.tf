# The token is read from CIVO_TOKEN in the environment when var.civo_token is
# null (recommended — keep it out of tfvars/state input). `civo apikey current`
# shows the active one, or `export CIVO_TOKEN=...`.
provider "civo" {
  token  = var.civo_token
  region = var.region
}

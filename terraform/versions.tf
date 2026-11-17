terraform {
  required_version = ">= 1.3.0"

  required_providers {
    civo = {
      source  = "civo/civo"
      version = ">= 1.0.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

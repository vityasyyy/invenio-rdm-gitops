variable "cloudflare_api_token" {
  description = "Cloudflare API token"
  sensitive   = true
}

variable "account_id" {
  description = "Cloudflare account ID"
}

variable "zone_id" {
  description = "Cloudflare zone ID"
}

variable "tunnel_secret" {
  description = "32-byte random secret"
  sensitive   = true
}

variable "account_id" {}
variable "zone_id" {}
variable "name" {}
variable "hostname" {}
variable "service" {}
variable "tunnel_secret" {
  sensitive = true
}

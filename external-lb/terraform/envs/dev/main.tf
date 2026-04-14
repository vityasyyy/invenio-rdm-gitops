module "tunnel" {
  source = "../../modules/cloudflare-tunnel"

  account_id    = var.account_id
  zone_id       = var.zone_id
  name          = "infra-tunnel"
  hostname      = "*.vityasy.me"
  service       = "http://traefik.traefik.svc.cluster.local:80"
  tunnel_secret = var.tunnel_secret
}

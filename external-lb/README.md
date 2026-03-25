# External Load Balancer via Cloudflare Tunnel

Exposes an internal Kubernetes service to the internet via a Cloudflare Tunnel, without opening inbound firewall ports.

## Architecture

```
Internet → Cloudflare Edge → Cloudflare Tunnel → Traefik (K8s) → Your Service
```

Cloudflare Tunnel creates an outbound-only connection from your infrastructure to Cloudflare's edge. This means:

- **No firewall holes** - No inbound ports needed on your K8s cluster
- **DDoS protection** - Traffic goes through Cloudflare's network
- **Automatic SSL** - Certificates managed by Cloudflare

## File Structure

```
external-lb/terraform/
├── envs/dev/
│   ├── main.tf        # Entry point - instantiates the tunnel module
│   ├── providers.tf   # Provider & terraform version config
│   └── variables.tf  # Input variables for this environment
└── modules/
    └── cloudflare-tunnel/
        ├── main.tf       # Creates tunnel, DNS record, and config
        ├── variables.tf  # Module input variables
        └── outputs.tf    # Module outputs (tunnel_id)
```

## Entry Point

**`envs/dev/main.tf`** is where you run Terraform from:

```hcl
module "tunnel" {
  source = "../../modules/cloudflare-tunnel"

  account_id    = var.account_id
  zone_id       = var.zone_id
  name          = "invenio-tunnel"
  hostname      = "invenio.vityasy.me"
  service       = "http://traefik.traefik.svc.cluster.local:80"
  tunnel_secret = var.tunnel_secret
}
```

This instantiates the reusable `cloudflare-tunnel` module with environment-specific values.

## What Gets Created

### 1. `cloudflare_tunnel.this`

A new tunnel in your Cloudflare account.

### 2. `cloudflare_record.this`

A CNAME DNS record pointing `invenio.vityasy.me` to `<tunnel-id>.cfargotunnel.com`.

### 3. `cloudflare_tunnel_config.this`

Routing configuration telling the tunnel:

- `invenio.vityasy.me` → forward to `http://traefik.traefik.svc.cluster.local:80`
- Everything else → return 404

## Prerequisites

1. **Cloudflare account** with a domain added
2. **Cloudflare API token** with permissions:
   - `Zone:Edit` (for DNS records)
   - `Account:Edit` (for tunnels)
3. **Terraform >= 1.5.0**

## Setup

### 1. Generate Tunnel Secret

```bash
openssl rand -base64 32
```

This creates a 32-byte random secret used to authenticate the tunnel.

### 2. Create `terraform.tfvars`

In `envs/dev/terraform.tfvars`:

```hcl
cloudflare_api_token = "your_cloudflare_api_token"
account_id           = "your_cloudflare_account_id"
zone_id              = "your_cloudflare_zone_id"
tunnel_secret        = "your_32_byte_base64_secret"
```

To find `zone_id` and `account_id`:

- Zone ID: Cloudflare Dashboard → Your Domain → Overview (scroll to API section)
- Account ID: Cloudflare Dashboard → Profile → API Tokens (or in URL when in Zero Trust settings)

### 3. Initialize & Deploy

```bash
cd envs/dev
terraform init
terraform plan   # Review changes
terraform apply  # Create resources
```

### 4. Verify

After apply:

- DNS record should exist at your domain registrar or Cloudflare
- Tunnel should show as "Active" in Cloudflare Zero Trust dashboard
- Visit `https://invenio.vityasy.me` (or your configured hostname)

## Variables Reference

| Variable | Description | Required |
|----------|-------------|----------|
| `cloudflare_api_token` | API token from Cloudflare | Yes |
| `account_id` | Cloudflare account ID | Yes |
| `zone_id` | Cloudflare zone ID for DNS | Yes |
| `tunnel_secret` | 32-byte secret (base64) | Yes |

## Module Outputs

The `cloudflare-tunnel` module exposes:

- `tunnel_id` - ID of the created tunnel (useful for referencing in other configs)

## Troubleshooting

**Tunnel not connecting:**

- Verify the tunnel secret matches exactly
- Check if the tunnel shows as active in Cloudflare Zero Trust dashboard

**DNS not resolving:**

- Allow a few minutes for DNS propagation
- Verify CNAME record exists in Cloudflare DNS settings

**502/503 errors:**

- Ensure your K8s service is running
- Verify the service URL is correct (check if Traefik is in `traefik` namespace)

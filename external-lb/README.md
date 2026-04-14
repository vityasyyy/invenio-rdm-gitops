# External Load Balancer via Cloudflare Tunnel

Exposes an internal Kubernetes service to the internet via a Cloudflare Tunnel, without opening inbound firewall ports.

## Architecture

```
Internet → Cloudflare Edge → Cloudflare Tunnel → Traefik (K8s) → Your Service
```

Cloudflare Tunnel creates an outbound-only connection from your infrastructure to Cloudflare's edge. This means:

- **No firewall holes** - No inbound ports needed on your K8s cluster
- **DDoS protection** - Traffic goes through Cloudflare's global network
- **Automatic SSL** - Certificates managed by Cloudflare
- **Zero config** - Works with any Kubernetes service

## File Structure

```
external-lb/terraform/
├── envs/dev/
│   ├── main.tf        # Entry point - instantiates tunnel module
│   ├── providers.tf   # Provider & terraform version config
│   ├── variables.tf  # Input variables for this environment
│   ├── .env          # Your Cloudflare credentials (gitignored)
│   └── .env.example  # Template for .env
└── modules/cloudflare-tunnel/
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
  name          = "infra-tunnel"
  hostname      = "*.vityasy.me"
  service       = "http://traefik.traefik.svc.cluster.local:80"
  tunnel_secret = var.tunnel_secret
}
```

The `hostname: "*.vityasy.me"` means any subdomain (e.g., `argocd.vityasy.me`, `app.vityasy.me`) will route through this tunnel to Traefik. Traefik then uses IngressRoute rules to route to specific services.

## What Gets Created

### 1. `cloudflare_tunnel.this`

A new tunnel in your Cloudflare account.

### 2. `cloudflare_record.this`

A CNAME DNS record pointing `*.vityasy.me` to `<tunnel-id>.cfargotunnel.com`.

### 3. `cloudflare_tunnel_config.this`

Routing configuration telling the tunnel to forward all subdomain traffic to Traefik.

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

This creates a 32-byte random secret used to authenticate the tunnel. Save this value to your `.env` file.

### 2. Create `terraform.tfvars`

In `envs/dev/terraform.tfvars` (or `.env` file):

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

### 4. Deploy cloudflared DaemonSet

After Terraform creates the tunnel:

```bash
../scripts/bootstrap-dev.sh
```

This script:
1. Fetches tunnel credentials from Cloudflare API
2. Creates a K8s secret with the credentials
3. Deploys the cloudflared DaemonSet to `kube-system`

### 5. Verify

After apply:

- DNS record should exist at Cloudflare
- Tunnel should show as "Active" in Cloudflare Zero Trust dashboard
- Visit `https://argocd.vityasy.me` (or any subdomain) to test

## Variables Reference

| Variable | Description | Required |
|----------|-------------|----------|
| `cloudflare_api_token` | API token from Cloudflare | Yes |
| `account_id` | Cloudflare account ID | Yes |
| `zone_id` | Cloudflare zone ID for DNS | Yes |
| `tunnel_secret` | 32-byte secret (base64) | Yes |

## Module Outputs

The `cloudflare-tunnel` module exposes:

- `tunnel_id` - ID of the created tunnel (useful for referencing)
- `tunnel_name` - Name of the tunnel
- `cname` - CNAME target (`<tunnel-id>.cfargotunnel.com`)

## Troubleshooting

**Tunnel not connecting:**

- Verify tunnel secret matches exactly
- Check if tunnel shows as active in Cloudflare Zero Trust dashboard
- Review cloudflared pod logs: `kubectl logs -n kube-system -l app=cloudflared --tail=50`

**DNS not resolving:**

- Allow a few minutes for DNS propagation
- Verify CNAME record exists in Cloudflare DNS settings
- Check that wildcard record `*.vityasy.me` was created

**502/503 errors:**

- Ensure your Traefik service is running
- Verify service URL is correct (check if Traefik is in `traefik` namespace)
- Check IngressRoute exists for the subdomain you're accessing

## Security Notes

- The tunnel secret (`TF_VAR_tunnel_secret`) should be treated like a password
- Store it in a password manager or encrypted vault
- Rotate it periodically for improved security (requires Terraform reapply)
- The cloudflared DaemonSet runs as non-root user 65534 with minimal privileges

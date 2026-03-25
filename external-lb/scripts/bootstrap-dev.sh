#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$(dirname "$SCRIPT_DIR")/terraform/envs/dev"

echo "==> Loading environment variables from .env"
if [ -f "$ENV_DIR/.env" ]; then
  set -a
  source "$ENV_DIR/.env"
  set +a
else
  echo "ERROR: .env file not found at $ENV_DIR/.env"
  echo "Copy .env.example to .env and fill in your values"
  exit 1
fi

echo "==> Validating required variables"
: "${TF_VAR_cloudflare_api_token:?missing - set in .env}"
: "${TF_VAR_account_id:?missing - set in .env}"
: "${TF_VAR_zone_id:?missing - set in .env}"
: "${TF_VAR_tunnel_secret:?missing - set in .env}"

echo "==> Terraform apply"
cd "$ENV_DIR"
terraform init
terraform apply -auto-approve

echo "==> Extract tunnel token"
export CLOUDFLARE_API_TOKEN="$TF_VAR_cloudflare_api_token"
TUNNEL_NAME=invenio-tunnel
TOKEN=$(cloudflared tunnel token $TUNNEL_NAME)

echo "==> Inject into Kubernetes"
kubectl create secret generic cloudflared-token \
  -n traefik \
  --from-literal=TUNNEL_TOKEN="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Done"
echo "Your app should be accessible at https://invenio.vityasy.me"
